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

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{display_name} <{smtp_user}>"
            msg["Reply-To"] = reply_email
            msg["To"] = to_email
            msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))

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


email_provider = EmailProvider()
