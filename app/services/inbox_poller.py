import asyncio
import email
from email.header import decode_header
import imaplib
import logging
import re
import urllib.parse
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Optional, List

from app.config import settings
from app.database import SessionLocal
from app.models.creator import Contact, Creator
from app.models.outreach import OutreachMessage, Thread, Reply
from app.services.reply_classifier import record_reply

logger = logging.getLogger(__name__)

# Control flag for the async loop
_RUNNING = False

def _decode_str(val, charset=None):
    if isinstance(val, bytes):
        try:
            return val.decode(charset or 'utf-8', errors='replace')
        except LookupError:
            return val.decode('utf-8', errors='replace')
    return val

def _clean_email_body(body: str) -> str:
    """Strip quoted text from email replies and decode URL form-encoded strings."""
    if not body: return body
    
    # Clean URL/form-encoded text where spaces became "+" (e.g. "I+will+be+interested+in+Concept+3...")
    if "+" in body and ("I+will" in body or "Concept+" in body or "Let's+build" in body or ("+" in body[:60] and " " not in body[:30])):
        try:
            body = urllib.parse.unquote_plus(body)
        except Exception:
            body = body.replace("+", " ")
    elif "%20" in body or "%28" in body or "%29" in body:
        try:
            body = urllib.parse.unquote(body)
        except Exception:
            pass

    # Replace \r\n with \n
    body = body.replace('\r\n', '\n')
    
    # Look for multi-line Gmail "On ... wrote:" pattern
    pattern = re.compile(r'\nOn\s+.*wrote:\s*', re.IGNORECASE | re.DOTALL)
    match = pattern.search(body)
    if match:
        top_part = body[:match.start()].strip()
        if top_part:
            body = top_part
        else:
            # User replied below the quoted text — extract non-quoted lines below
            after_quote = body[match.end():]
            cleaned_after = [line for line in after_quote.split('\n') if not line.strip().startswith('>')]
            bottom_part = '\n'.join(cleaned_after).strip()
            if bottom_part:
                body = bottom_part
        
    lines = body.split('\n')
    cleaned = []
    
    quote_patterns = [
        re.compile(r'^_{3,}\s*$'),
        re.compile(r'^-{3,}\s*Original Message\s*-{3,}$', re.IGNORECASE),
        re.compile(r'^From:\s+.*$', re.IGNORECASE),
    ]
    
    for line in lines:
        stripped = line.strip()
        is_quote = False
        
        if stripped.startswith('>'):
            is_quote = True
            
        for p in quote_patterns:
            if p.match(stripped):
                is_quote = True
                break
                
        if is_quote:
            break
            
        cleaned.append(line)
        
    res = '\n'.join(cleaned).strip()
    # Final safety unquote if leftover +
    if "+" in res and ("I+will" in res or "Concept+" in res or "interested+in" in res):
        try:
            res = urllib.parse.unquote_plus(res)
        except Exception:
            res = res.replace("+", " ")
    return res

def _parse_email_message(msg):
    # Parse Subject
    subject = ""
    if msg["Subject"]:
        headers = decode_header(msg["Subject"])
        subject = "".join([_decode_str(val, charset) for val, charset in headers])
        if "+" in subject and ("Interested+in" in subject or "Concept+" in subject):
            try:
                subject = urllib.parse.unquote_plus(subject)
            except Exception:
                subject = subject.replace("+", " ")
        elif "%20" in subject:
            try:
                subject = urllib.parse.unquote(subject)
            except Exception:
                pass
        
    # Parse From
    from_raw = msg.get("From", "")
    _, from_email = parseaddr(from_raw)
    from_email = from_email.lower().strip()
    
    # Parse Body
    raw_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset()
                    raw_body = _decode_str(payload, charset)
                break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset()
            raw_body = _decode_str(payload, charset)
            
    cleaned_body = _clean_email_body(raw_body)
    return subject, from_email, cleaned_body, raw_body

def _find_thread_for_sender(db, from_email: str, subject: str = "", body: str = "", raw_body: str = "", all_creators: Optional[List[Creator]] = None, msg_datetime: Optional[datetime] = None) -> Optional[str]:
    """Attempt to find the thread ID for a creator using tracking tokens, handle, subject, or email."""
    if not from_email:
        return None
    from_email_clean = from_email.lower().strip()
    creator_id = None
    all_text = f"{subject} {body} {raw_body}".lower()

    # 1. Primary & 100% Reliable: Direct Creator Tracking Token
    # Matches [CF-CID:<creator_id>] embedded in the subject or quoted email body
    cid_match = re.search(r"cf-cid:([a-z0-9\-_]+)", all_text)
    handle_match = re.search(r"handle:@([a-z0-9_.\-]+)", all_text) or re.search(r"\[#([a-z0-9_.\-]+)\]", all_text)
    cand_handle = handle_match.group(1).strip() if handle_match else None

    if cid_match:
        cand_id = cid_match.group(1).strip()
        c = db.get(Creator, cand_id)
        if c:
            creator_id = c.id
        else:
            # If the email explicitly contained a CF-CID that belongs to a deleted creator,
            # NEVER fall back to handle matching — that email was for a previous/deleted creator run!
            logger.info(f"[Inbox Poller] Explicit CF-CID token '{cand_id}' belongs to a deleted/inactive creator. Ignoring stale email.")
            return None
    elif cand_handle:
        c = db.query(Creator).filter(Creator.handle.ilike(f"%{cand_handle}%")).first()
        if c:
            creator_id = c.id

    # CRITICAL TOKEN GUARD:
    # If the email explicitly contained a tracking token ([#handle] or [CF-CID:...]),
    # but that token did NOT match any active creator in the database:
    # DO NOT fall through to from_email matching! That email was sent for a different or deleted creator.
    if not creator_id and (cid_match or handle_match):
        logger.info(f"[Inbox Poller] Explicit token ({cand_handle or (cid_match.group(0) if cid_match else '')}) did not match any active creator in DB. Rejecting attribution fallback.")
        return None

    if all_creators is None:
        all_creators = db.query(Creator).all()

    admin_email = (settings.GOOGLE_EMAIL or "").lower().strip()
    from_email = (settings.FROM_EMAIL or "").lower().strip()
    is_admin_email = (from_email_clean == admin_email or from_email_clean == from_email) if (admin_email or from_email) else False
    if is_admin_email:
        # Outbound admin messages must never be treated as inbound creator replies
        return None

    # 3. Match against Creator display_name or handle in subject line
    subj_lower = (subject or "").lower()
    if not creator_id and subject:
        sorted_creators = sorted(all_creators, key=lambda x: len(x.display_name or ""), reverse=True)
        for c in sorted_creators:
            c_name = (c.display_name or "").lower().strip()
            c_handle = (c.handle or "").lower().lstrip("@").strip()
            if (c_name and len(c_name) >= 3 and f"for {c_name}" in subj_lower) or (c_handle and len(c_handle) >= 3 and f"for {c_handle}" in subj_lower):
                creator_id = c.id
                break

    # 4. Match against Creator table (email_public) - strictly enforce that creator was contacted
    if not creator_id and not is_admin_email:
        # A creator who was NEVER contacted CANNOT have an outreach reply!
        contacted_creators = [
            c for c in all_creators 
            if (c.email_public or "").lower().strip() == from_email_clean
            and c.status in ("contacted", "in_review", "qualified", "approved", "pitched", "ready_for_launch", "partnered", "launched", "active", "building")
        ]
        if len(contacted_creators) == 1:
            creator_id = contacted_creators[0].id
        elif len(contacted_creators) > 1:
            # If multiple creators share this email (e.g. test addresses), disambiguate by subject line
            matching_by_subj = [
                c for c in contacted_creators
                if (c.display_name and len(c.display_name) >= 3 and c.display_name.lower() in subj_lower) or
                   (c.handle and len(c.handle) >= 3 and c.handle.lower().lstrip("@") in subj_lower)
            ]
            if len(matching_by_subj) == 1:
                creator_id = matching_by_subj[0].id
            else:
                recent_thread = db.query(Thread).filter(
                    Thread.creator_id.in_([c.id for c in contacted_creators])
                ).order_by(Thread.last_activity.desc()).first()
                if recent_thread:
                    return recent_thread.id
                creator_id = contacted_creators[0].id

    # 5. Match against Contacts table
    if not creator_id and not is_admin_email:
        contact = db.query(Contact).filter(Contact.value.ilike(f"%{from_email_clean}%"), Contact.contact_type == "email").first()
        if contact and contact.creator_id:
            c_candidate = db.get(Creator, contact.creator_id)
            if c_candidate and c_candidate.status in ("contacted", "in_review", "qualified", "approved", "pitched", "ready_for_launch", "partnered", "launched", "active", "building"):
                creator_id = contact.creator_id

    # Strictly do NOT assign unrecognized/marketing emails to random creators
    if not creator_id:
        return None

    # STRICT UNCONTACTED & LIFECYCLE GUARD:
    # A creator who is merely "discovered" or was NEVER sent an OutreachMessage in this run CANNOT have a reply!
    from app.models.outreach import OutreachMessage
    matched_creator = db.get(Creator, creator_id)
    if not matched_creator:
        return None

    if matched_creator.status == "discovered":
        logger.info(f"[Inbox Poller] Creator {matched_creator.handle} ({creator_id}) is in 'discovered' status (uncontacted). Ignoring stale email.")
        return None

    sent_outreach = db.query(OutreachMessage).filter(
        OutreachMessage.creator_id == creator_id,
        OutreachMessage.status == "sent"
    ).order_by(OutreachMessage.sent_at.desc()).first()

    if not sent_outreach:
        logger.info(f"[Inbox Poller] Creator {matched_creator.handle} ({creator_id}) has no sent outreach message in this run. Ignoring unaddressed email from {from_email_clean}.")
        return None

    # Timing guard: ensure reply was not sent before outreach was initiated
    if msg_datetime and sent_outreach.sent_at and not (cid_match or cand_handle):
        from datetime import timezone
        if msg_datetime.tzinfo is not None:
            msg_dt_utc = msg_datetime.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            msg_dt_utc = msg_datetime
        earliest_outreach = db.query(OutreachMessage).filter(
            OutreachMessage.creator_id == creator_id,
            OutreachMessage.status == "sent"
        ).order_by(OutreachMessage.sent_at.asc()).first()
        earliest_sent = (earliest_outreach.sent_at if earliest_outreach else sent_outreach.sent_at)
        sent_at_utc = earliest_sent.replace(tzinfo=None) if earliest_sent.tzinfo else earliest_sent
        # Allow 24 hours of clock skew/timezone drift
        if msg_dt_utc < (sent_at_utc - timedelta(hours=24)):
            logger.info(f"[Inbox Poller] Email from {from_email_clean} dated {msg_dt_utc} was sent before earliest outreach ({sent_at_utc}). Ignoring stale email.")
            return None

    # Find or create latest thread for this specific creator
    thread = db.query(Thread).filter(Thread.creator_id == creator_id).order_by(Thread.created_at.desc()).first()
    if not thread:
        thread = Thread(creator_id=creator_id, status="open", created_at=datetime.utcnow(), last_activity=datetime.utcnow())
        db.add(thread)
        db.commit()
        db.refresh(thread)
    return thread.id


import threading
import socket

_POLL_LOCK = threading.Lock()

def poll_inbox_sync(wait_timeout: float = 0.0) -> dict:
    """Synchronous function that connects to IMAP, fetches recent messages, and processes incoming creator replies."""
    if not settings.GOOGLE_EMAIL or not settings.GOOGLE_APP_PASSWORD:
        logger.warning("IMAP Poller: GOOGLE_EMAIL or GOOGLE_APP_PASSWORD not set. Skipping poll.")
        return {"status": "skipped", "reason": "no_credentials", "new_replies": 0}

    # Non-blocking or bounded lock acquisition
    acquired = _POLL_LOCK.acquire(timeout=wait_timeout) if wait_timeout > 0 else _POLL_LOCK.acquire(blocking=False)
    if not acquired:
        logger.debug("IMAP Poller already running in another thread. Skipping concurrent invocation.")
        return {"status": "busy", "reason": "already_running", "new_replies": 0}

    admin_email = (settings.GOOGLE_EMAIL or "").lower().strip()
    from_email = (settings.FROM_EMAIL or "").lower().strip()
    mail = None
    prev_timeout = socket.getdefaulttimeout()
    new_replies_count = 0
    candidate_messages = []
    try:
        socket.setdefaulttimeout(20)  # 20s socket timeout for reliable payload download
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
        mail.login(settings.GOOGLE_EMAIL, settings.GOOGLE_APP_PASSWORD.replace(" ", ""))
        mail.select("INBOX")
        
        # Search messages from the last 14 days (or fallback to ALL if empty)
        since_date = (datetime.utcnow() - timedelta(days=14)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK" or not messages or not messages[0]:
            status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages or not messages[0]:
            return {"status": "success", "new_replies": 0, "processed": 0}
            
        all_ids = messages[0].split()
        # Fetch up to the last 100 emails to guarantee no replies are missed across days/hours
        email_ids = all_ids[-100:]
        status, msg_data = mail.fetch(b",".join(email_ids), "(RFC822)")
        if status == "OK" and msg_data:
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    try:
                        msg = email.message_from_bytes(response_part[1])
                        subject, from_email_sender, body, raw_body = _parse_email_message(msg)
                        
                        if not from_email_sender:
                            continue

                        from_lower = from_email_sender.lower().strip()
                        is_reply_subject = subject.lower().lstrip().startswith(("re:", "fwd:", "fw:"))

                        # Filter out automated marketing, system alerts, and notification bots
                        ignore_patterns = (
                            "mailer-daemon", "no-reply", "noreply", "accounts.google.com",
                            "googleaistudio", "prisma.io", "openai.com", "twilio.com",
                            "qualtrics", "apify.com", "github.com", "notifications@",
                            "security-noreply"
                        )
                        if any(pat in from_lower for pat in ignore_patterns):
                            continue

                        # Ignore all outgoing/sent messages from admin/studio account
                        if (admin_email and from_lower == admin_email) or (from_email and from_lower == from_email):
                            continue

                        msg_date_hdr = msg.get("Date")
                        msg_datetime = None
                        if msg_date_hdr:
                            try:
                                msg_datetime = email.utils.parsedate_to_datetime(msg_date_hdr)
                            except Exception:
                                msg_datetime = None

                        candidate_messages.append((from_email_sender, subject, body, raw_body, msg_datetime))
                    except Exception as item_err:
                        logger.debug(f"IMAP item parse error: {item_err}")
                        continue

        # If candidate messages were found, open a dedicated short-lived DB session to process them
        if candidate_messages:
            db = SessionLocal()
            try:
                all_creators = db.query(Creator).all()
                for from_email, subject, body, raw_body, msg_datetime in candidate_messages:
                    thread_id = _find_thread_for_sender(db, from_email, subject, body, raw_body, all_creators=all_creators, msg_datetime=msg_datetime)
                    if thread_id:
                        # Check if this exact reply was already recorded for this creator across ANY thread
                        target_thread = db.get(Thread, thread_id)
                        clean_body = (body or "").strip()
                        if target_thread and target_thread.creator_id:
                            existing_reply = db.query(Reply).join(Thread).filter(
                                Thread.creator_id == target_thread.creator_id,
                                Reply.from_address.ilike(from_email.strip()),
                                Reply.body == clean_body
                            ).first()
                        else:
                            existing_reply = db.query(Reply).filter(
                                Reply.thread_id == thread_id,
                                Reply.from_address.ilike(from_email.strip()),
                                Reply.body == clean_body,
                            ).first()

                        if not existing_reply:
                            try:
                                record_reply(
                                    db=db,
                                    thread_id=thread_id,
                                    from_address=from_email,
                                    subject=subject,
                                    body=body,
                                    received_at=msg_datetime or datetime.utcnow(),
                                    actor="imap_poller"
                                )
                                new_replies_count += 1
                                logger.info(f"Recorded IMAP reply from {from_email} to thread {thread_id}")
                            except Exception as e:
                                safe_err = str(e).encode("ascii", "ignore").decode("ascii")
                                logger.error(f"Failed to record reply from {from_email}: {safe_err}")
            finally:
                db.close()
        return {"status": "success", "new_replies": new_replies_count, "processed": len(candidate_messages)}
    except Exception as e:
        safe_err = str(e).encode("ascii", "ignore").decode("ascii")
        logger.warning(f"IMAP Polling transient warning: {safe_err}")
        return {"status": "error", "error": safe_err, "new_replies": 0}
    finally:
        try:
            socket.setdefaulttimeout(prev_timeout)
        except Exception:
            pass
        if mail:
            try:
                mail.close()
            except Exception:
                pass
            try:
                mail.logout()
            except Exception:
                pass
        if _POLL_LOCK.locked():
            try:
                _POLL_LOCK.release()
            except Exception:
                pass

async def start_poller_loop(interval_seconds: int = 60):
    global _RUNNING
    _RUNNING = True
    logger.info("Starting IMAP Inbox Poller loop...")
    await asyncio.sleep(10)  # Let server complete startup and bind to port
    while _RUNNING:
        sleep_dur = interval_seconds
        try:
            # Run the synchronous IMAP polling in a threadpool to avoid blocking the async loop
            res = await asyncio.to_thread(poll_inbox_sync)
            if res and res.get("status") == "error":
                sleep_dur = max(interval_seconds, 180)
        except Exception as e:
            logger.error(f"Error in poller loop: {e}")
            sleep_dur = max(interval_seconds, 180)
        
        await asyncio.sleep(sleep_dur)

def stop_poller_loop():
    global _RUNNING
    _RUNNING = False
    logger.info("Stopping IMAP Inbox Poller loop...")
