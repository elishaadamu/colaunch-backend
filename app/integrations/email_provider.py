"""
Email sending integration (HTTPS APIs + Google SMTP Fallback).
Supports HTTPS REST APIs (Resend, SendGrid, Brevo) over Port 443 to bypass cloud container SMTP port blocks (e.g. Render/AWS/Vercel).
Also supports direct Google SMTP on Port 465/587 when outbound SMTP is available.
"""
import socket
import smtplib
import ssl
import uuid
import logging
import urllib.request
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.integrations.base import IntegrationError, IntegrationNotConfiguredError

logger = logging.getLogger(__name__)


def _connect_smtp_multi_ipv4(host: str = "smtp.gmail.com", port: int = 465, timeout: int = 5, use_ssl: bool = True):
    """
    Connects to SMTP trying all resolved IPv4 (AF_INET) addresses with fast timeout.
    """
    try:
        addr_infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except Exception as e:
        raise OSError(f"Could not resolve IPv4 for {host}: {e}")

    last_err = None
    for addr in addr_infos:
        ip, resolved_port = addr[4]
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((ip, resolved_port))

            if use_ssl:
                ctx = ssl.create_default_context()
                sslsock = ctx.wrap_socket(sock, server_hostname=host)
                server = smtplib.SMTP_SSL()
                server._host = host
                server.sock = sslsock
                server.file = sslsock.makefile("rb")
                server.getreply()
                return server
            else:
                server = smtplib.SMTP()
                server._host = host
                server.sock = sock
                server.file = sock.makefile("rb")
                server.getreply()
                server.ehlo("creatorforge.com")
                ctx = ssl.create_default_context()
                server.starttls(context=ctx)
                server.ehlo("creatorforge.com")
                return server
        except Exception as conn_err:
            last_err = conn_err
            continue

    if last_err:
        raise last_err
    raise OSError(f"All IPv4 endpoints for {host}:{port} failed")


class EmailProvider:
    def is_configured(self) -> bool:
        has_resend = bool(settings.RESEND_API_KEY)
        has_sendgrid = bool(settings.SENDGRID_API_KEY)
        has_brevo = bool(settings.BREVO_API_KEY)
        has_google = bool(settings.GOOGLE_EMAIL) and bool(settings.GOOGLE_APP_PASSWORD)
        return has_resend or has_sendgrid or has_brevo or has_google

    def send(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        from_email: str = None,
        from_name: str = None,
        reply_to: str = None,
    ) -> dict:
        """
        Sends an email prioritizing cloud-friendly HTTPS APIs (Port 443) before SMTP.
        Returns {"message_id": str, "status": str}.
        """
        if not self.is_configured():
            raise IntegrationNotConfiguredError(
                "No email credentials configured. Please set RESEND_API_KEY, SENDGRID_API_KEY, or GOOGLE_EMAIL/GOOGLE_APP_PASSWORD in Render environment."
            )

        # Ensure body_html is formatted with standard luxury responsive HTML template
        if not body_html or "<!DOCTYPE" not in body_html:
            try:
                from app.services.email_template import format_luxury_html_email
                body_html = format_luxury_html_email(
                    body_text=body_text or body_html or "",
                    subject=subject,
                )
            except Exception as fmt_err:
                logger.warning(f"[EmailProvider] Failed to format luxury HTML: {fmt_err}")
                if not body_html:
                    body_html = (body_text or "").replace("\n", "<br>")

        display_name = from_name or settings.FROM_NAME or "Creator Forge"
        candidate_sender = (
            from_email
            or settings.FROM_EMAIL
            or settings.ADMIN_EMAIL
            or settings.GOOGLE_EMAIL
            or "creatorforgeweb@gmail.com"
        ).strip()

        # Sanitize away unroutable brevosend relay domain to genuine Gmail address
        if "brevosend.com" in candidate_sender.lower():
            sender_email = (settings.GOOGLE_EMAIL or "creatorforgeweb@gmail.com").strip()
        else:
            sender_email = candidate_sender

        # Ensure Reply-To points to explicitly passed reply_to or a real, routable Gmail inbox
        if reply_to and reply_to.strip():
            reply_email = reply_to.strip()
        else:
            reply_email = (
                settings.GOOGLE_EMAIL
                or (sender_email if "brevosend.com" not in sender_email.lower() else None)
                or "creatorforgeweb@gmail.com"
            ).strip()

        errors = []

        # ── 1. Google SMTP Direct (Port 465 SSL & Port 587 STARTTLS — Direct verified SPF/DKIM delivery) ──
        if settings.GOOGLE_EMAIL and settings.GOOGLE_APP_PASSWORD:
            smtp_user = settings.GOOGLE_EMAIL.strip()
            smtp_password = settings.GOOGLE_APP_PASSWORD.replace(" ", "").strip()

            import os
            import re as _re
            from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
            from email.mime.image import MIMEImage

            smtp_body_html = body_html
            inline_concept_parts = []

            # Rewrite hosted logo badge URLs to inline CID when logo file is attached below
            logo_path = os.path.join(os.path.dirname(__file__), "..", "assets", "colaunch_logo.png")
            has_inline_logo = os.path.exists(logo_path)
            if has_inline_logo:
                smtp_body_html = _re.sub(
                    r'https?://[^"\'\s>]+/images/colaunch_(?:badge|logo)\.png',
                    'cid:colaunch_logo',
                    smtp_body_html,
                )

            # Inline-attach concept preview PNGs if pointing to localhost (unreachable from Gmail proxy)
            concept_img_matches = list(
                _re.finditer(r'(https?://(?:localhost|127\.0\.0\.1)[^"\'\s>]+/api/outreach/concept-card-image\?[^"\'\s>]+)', smtp_body_html)
            )
            if concept_img_matches:
                try:
                    from app.services.concept_image_generator import generate_concept_card_image
                    for idx, m in enumerate(concept_img_matches[:3]):
                        raw_url = m.group(1).replace("&amp;", "&")
                        qs = _parse_qs(_urlparse(raw_url).query)
                        c_data = {
                            "name": qs.get("name", [f"Concept {idx + 1}"])[0],
                            "appUrl": qs.get("app_url", [""])[0],
                            "tagline": qs.get("tagline", [""])[0],
                            "features": [f for f in qs.get("features", [""])[0].split("|") if f],
                            "customer": qs.get("customer", [""])[0],
                            "pricing": qs.get("pricing", [""])[0],
                            "brandColor": qs.get("brand_color", [""])[0],
                            "mockupType": qs.get("mockup_type", ["saas_os"])[0],
                        }
                        png_bytes = generate_concept_card_image(c_data)
                        cid_name = f"concept_preview_{idx}"
                        part = MIMEImage(png_bytes, name=f"{cid_name}.png")
                        part.add_header("Content-ID", f"<{cid_name}>")
                        part.add_header("Content-Disposition", "inline", filename=f"{cid_name}.png")
                        inline_concept_parts.append(part)
                        smtp_body_html = smtp_body_html.replace(m.group(1), f"cid:{cid_name}")
                except Exception as c_img_err:
                    logger.warning(f"[EmailProvider] Failed inline concept preview fallback: {c_img_err}")

            msg = MIMEMultipart("related")
            msg["Subject"] = subject
            msg["From"] = f"{display_name} <{smtp_user}>"
            msg["Reply-To"] = reply_email
            msg["To"] = to_email

            msg_alt = MIMEMultipart("alternative")
            msg.attach(msg_alt)
            msg_alt.attach(MIMEText(body_text, "plain"))
            msg_alt.attach(MIMEText(smtp_body_html, "html"))

            # Inline CoLaunch Logo attachment for 100% reliable image display in Gmail & Outlook
            if has_inline_logo:
                try:
                    with open(logo_path, "rb") as f_img:
                        img_part = MIMEImage(f_img.read(), name="colaunch_logo.png")
                        img_part.add_header("Content-ID", "<colaunch_logo>")
                        img_part.add_header("Content-Disposition", "inline", filename="colaunch_logo.png")
                        msg.attach(img_part)
                except Exception as img_err:
                    logger.warning(f"[EmailProvider] Failed to attach inline logo: {img_err}")

            for c_part in inline_concept_parts:
                msg.attach(c_part)

            # Port 465 SSL via IPv4
            try:
                server = _connect_smtp_multi_ipv4("smtp.gmail.com", 465, timeout=5, use_ssl=True)
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, to_email, msg.as_string())
                server.quit()
                logger.info(f"[EmailProvider] Sent email to {to_email} via Google SMTP (Port 465 SSL)")
                return {"message_id": str(uuid.uuid4()), "status": "sent", "provider": "google_smtp_465"}
            except Exception as e465:
                errors.append(f"Google SMTP 465: {e465}")
                logger.warning(f"[EmailProvider] Google SMTP 465 failed: {e465}, trying Port 587 STARTTLS...")

            # Port 587 STARTTLS via IPv4
            try:
                server = _connect_smtp_multi_ipv4("smtp.gmail.com", 587, timeout=5, use_ssl=False)
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, to_email, msg.as_string())
                server.quit()
                logger.info(f"[EmailProvider] Sent email to {to_email} via Google SMTP (Port 587 STARTTLS)")
                return {"message_id": str(uuid.uuid4()), "status": "sent", "provider": "google_smtp_587"}
            except Exception as e587:
                errors.append(f"Google SMTP 587: {e587}")
                logger.warning(f"[EmailProvider] Google SMTP 587 failed: {e587}, falling back to HTTPS relays...")

        # ── 2. Brevo HTTPS API Direct (Port 443) ──────────────────────────────
        if settings.BREVO_API_KEY:
            try:
                brevo_payload = {
                    "sender": {"name": display_name, "email": sender_email},
                    "to": [{"email": to_email}],
                    "replyTo": {"email": reply_email, "name": display_name},
                    "subject": subject,
                    "htmlContent": body_html,
                    "textContent": body_text,
                }

                # Inline CoLaunch Logo attachment for 100% reliable image display
                logo_path = os.path.join(os.path.dirname(__file__), "..", "assets", "colaunch_logo.png")
                if os.path.exists(logo_path):
                    try:
                        import base64
                        with open(logo_path, "rb") as f_img:
                            b64_data = base64.b64encode(f_img.read()).decode("utf-8")
                            brevo_payload["attachment"] = [
                                {
                                    "content": b64_data,
                                    "name": "colaunch_logo.png"
                                }
                            ]
                    except Exception as b_err:
                        logger.warning(f"[EmailProvider] Failed to attach inline logo for Brevo: {b_err}")

                req = urllib.request.Request(
                    "https://api.brevo.com/v3/smtp/email",
                    data=json.dumps(brevo_payload).encode("utf-8"),
                    headers={
                        "api-key": settings.BREVO_API_KEY.strip(),
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"[EmailProvider] Sent email to {to_email} via Brevo HTTPS API (sender: {sender_email})")
                        return {"message_id": str(uuid.uuid4()), "status": "sent", "provider": "brevo"}
            except Exception as e_brevo:
                errors.append(f"Brevo HTTPS (443): {e_brevo}")
                logger.warning(f"[EmailProvider] Brevo API error: {e_brevo}, trying fallback providers...")

        # ── 3. Resend HTTPS API (Port 443) ────────────────────────────────────
        if settings.RESEND_API_KEY:
            try:
                resend_from = (
                    f"{display_name} <{sender_email}>"
                    if not sender_email.endswith("@gmail.com")
                    else f"{display_name} <onboarding@resend.dev>"
                )
                resend_payload = {
                    "from": resend_from,
                    "to": [to_email],
                    "subject": subject,
                    "html": body_html,
                    "text": body_text,
                }

                # Inline CoLaunch Logo attachment for Resend
                logo_path = os.path.join(os.path.dirname(__file__), "..", "assets", "colaunch_logo.png")
                if os.path.exists(logo_path):
                    try:
                        import base64
                        with open(logo_path, "rb") as f_img:
                            b64_data = base64.b64encode(f_img.read()).decode("utf-8")
                            resend_payload["attachments"] = [
                                {
                                    "content": b64_data,
                                    "filename": "colaunch_logo.png"
                                }
                            ]
                    except Exception as r_err:
                        logger.warning(f"[EmailProvider] Failed to attach inline logo for Resend: {r_err}")

                req = urllib.request.Request(
                    "https://api.resend.com/emails",
                    data=json.dumps(resend_payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}",
                        "Content-Type": "application/json",
                        "User-Agent": "CreatorForge/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status in (200, 201):
                        resp_data = json.loads(resp.read().decode("utf-8"))
                        logger.info(f"[EmailProvider] Sent email to {to_email} via Resend HTTPS API")
                        return {
                            "message_id": resp_data.get("id", str(uuid.uuid4())),
                            "status": "sent",
                            "provider": "resend",
                        }
            except Exception as e_resend:
                errors.append(f"Resend HTTPS (443): {e_resend}")
                logger.warning(f"[EmailProvider] Resend API error: {e_resend}")

        # ── 4. SendGrid HTTPS API (Port 443) ──────────────────────────────────
        if settings.SENDGRID_API_KEY:
            try:
                sg_payload = {
                    "personalizations": [{"to": [{"email": to_email}]}],
                    "from": {"email": sender_email, "name": display_name},
                    "subject": subject,
                    "content": [
                        {"type": "text/plain", "value": body_text},
                        {"type": "text/html", "value": body_html},
                    ],
                }
                req = urllib.request.Request(
                    "https://api.sendgrid.com/v3/mail/send",
                    data=json.dumps(sg_payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {settings.SENDGRID_API_KEY.strip()}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status in (200, 202):
                        logger.info(f"[EmailProvider] Sent email to {to_email} via SendGrid HTTPS API")
                        return {"message_id": str(uuid.uuid4()), "status": "sent", "provider": "sendgrid"}
            except Exception as e_sg:
                errors.append(f"SendGrid HTTPS (443): {e_sg}")
                logger.warning(f"[EmailProvider] SendGrid API error: {e_sg}")

        raise IntegrationError(
            f"Email delivery failed across all available methods: {' | '.join(errors)}. "
            f"Note: Render free/starter tier blocks direct SMTP ports (25, 465, 587). "
            f"Add RESEND_API_KEY (from resend.com) or SENDGRID_API_KEY to Render Environment Variables for guaranteed delivery over HTTPS."
        )

    def check_bounce_status(self, email: str) -> bool:
        return False

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        from_email: str = None,
        display_name: str = None,
        reply_to: str = None,
        **kwargs
    ) -> dict:
        """Alias for send() to provide seamless backwards compatibility."""
        return self.send(
            to_email=to_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_email=from_email,
            from_name=display_name,
            reply_to=reply_to,
        )


email_provider = EmailProvider()
