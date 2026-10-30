import email
import imaplib
import logging
import re
import socket
import threading
import hashlib
import asyncio
from datetime import datetime
from email.header import decode_header
from typing import Optional, Dict, Any, List

from app.config import settings

try:
    from app.mongodb import get_collection
except ImportError:
    get_collection = None

try:
    from app.database import SessionLocal
    from app.models.support import SupportMessage
except ImportError:
    SessionLocal = None
    SupportMessage = None

logger = logging.getLogger("creator_forge.support_poller")
_POLL_LOCK = threading.Lock()
_RUNNING = False

CORRUPTED_MARKERS = [
    "CoLaunch Customer Support Alert",
    "RECENT CONVERSATION HISTORY:",
    "Dispatched via CoLaunch",
    "A creator has requested administrator assistance",
    "Admin Inbox:",
    "CREATOR DETAILS:",
]


def _decode_str(val, charset=None):
    if isinstance(val, bytes):
        try:
            return val.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return val.decode("utf-8", errors="replace")
    return val or ""


def clean_support_reply_body(body: str) -> str:
    """Strip quoted email threads, reply headers, and alert templates to get only the new admin message."""
    if not body:
        return ""
    text = body.replace("\r\n", "\n")

    # If the body is just an internal system alert, it is NOT an admin reply
    for marker in CORRUPTED_MARKERS:
        if marker in text:
            # Check if this marker appears after an "On ... wrote:" or at the very top
            m_pos = text.find(marker)
            # If the alert marker is near the top or predominant, this is an outgoing alert, not a human reply
            if m_pos < 100:
                return ""

    # Match standard email reply delimiters to discard quoted previous thread
    split_patterns = [
        re.compile(r"\nOn\s+.*wrote:\s*", re.IGNORECASE | re.DOTALL),
        re.compile(r"\nLe\s+.*écrit\s*:\s*", re.IGNORECASE | re.DOTALL),
        re.compile(r"-+\s*Original Message\s*-+", re.IGNORECASE),
        re.compile(r"_{3,}\s*$", re.MULTILINE),
        re.compile(r"^From:\s+CoLaunch Support", re.IGNORECASE | re.MULTILINE),
        re.compile(r"CoLaunch Customer Support Alert", re.IGNORECASE),
        re.compile(r"CoLaunch Support Ticket Urgent Escalation", re.IGNORECASE),
        re.compile(r"\n>\s*CoLaunch Customer Support Alert", re.IGNORECASE),
    ]

    for pattern in split_patterns:
        m = pattern.search(text)
        if m:
            text = text[:m.start()]

    # Filter out blockquote lines
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        lines.append(line)

    cleaned = "\n".join(lines).strip()

    # Extra safety check: if cleaned text still contains alert markers, reject it
    for marker in CORRUPTED_MARKERS:
        if marker.lower() in cleaned.lower():
            return ""

    return cleaned


def extract_ticket_identifiers(full_text: str, subject: str = "") -> Dict[str, Optional[str]]:
    """Extract session_id, creator_id, and creator_handle from email body/subject."""
    res = {
        "session_id": None,
        "creator_id": None,
        "creator_handle": None,
        "creator_name": None
    }

    # 1. Session ID (e.g. "Session ID: ses_nh028l93mvvh8oow" or "[TICKET-ID: ses_xxx]")
    session_match = (
        re.search(r"Session ID:\s*([a-zA-Z0-9_\-]+)", full_text, re.IGNORECASE) or
        re.search(r"\[TICKET-ID:\s*([a-zA-Z0-9_\-]+)", full_text, re.IGNORECASE) or
        re.search(r"#([a-zA-Z0-9_\-]+)#[a-zA-Z0-9_\-]+#", subject)
    )
    if session_match:
        res["session_id"] = session_match.group(1).strip()

    # 2. Creator ID (e.g. "Creator ID: b8a8bbdd-25d1-4d1d-b6ed-b86b4d0762f6")
    creator_match = (
        re.search(r"Creator ID:\s*([a-zA-Z0-9_\-]+)", full_text, re.IGNORECASE) or
        re.search(r"CREATOR:\s*([a-zA-Z0-9_\-]+)", full_text, re.IGNORECASE) or
        re.search(r"#[a-zA-Z0-9_\-]+#([a-zA-Z0-9_\-]+)#", subject)
    )
    if creator_match:
        res["creator_id"] = creator_match.group(1).strip()

    # 3. Creator Handle (e.g. "@AngryBirds" or "Creator: Angry Birds (@AngryBirds)")
    handle_match = (
        re.search(r"Creator:\s*[^\n\(]+\(@?([a-zA-Z0-9_\-]+)\)", full_text) or
        re.search(r"\[CoLaunch Support Ticket\]\s*@?([a-zA-Z0-9_\-]+)", subject) or
        re.search(r"@([a-zA-Z0-9_\-]+)", subject)
    )
    if handle_match:
        clean_handle = handle_match.group(1).strip().lstrip("@")
        res["creator_handle"] = "@" + clean_handle

    # 4. Creator Name
    name_match = re.search(r"Creator:\s*([^\n\(]+)\s*\(", full_text)
    if name_match:
        res["creator_name"] = name_match.group(1).strip()

    return res


def cleanup_corrupted_messages():
    """Purge any internal system alert notifications that were accidentally saved as admin messages."""
    try:
        coll = get_collection("support_messages")
        if coll is not None:
            regex_patterns = [{"message": {"$regex": marker, "$options": "i"}} for marker in CORRUPTED_MARKERS]
            del_res = coll.delete_many({
                "sender": "admin",
                "$or": regex_patterns
            })
            if del_res.deleted_count > 0:
                logger.info(f"[Support Poller] Purged {del_res.deleted_count} corrupted alert templates from MongoDB support_messages")
    except Exception as e:
        logger.warning(f"[Support Poller] MongoDB cleanup error: {e}")

    if SessionLocal and SupportMessage:
        try:
            db = SessionLocal()
            for marker in CORRUPTED_MARKERS:
                db.query(SupportMessage).filter(
                    SupportMessage.sender == "admin",
                    SupportMessage.message.ilike(f"%{marker}%")
                ).delete(synchronize_session=False)
            db.commit()
            db.close()
        except Exception as e:
            logger.warning(f"[Support Poller] SQL cleanup error: {e}")


def poll_support_email_replies() -> Dict[str, Any]:
    """
    Polls Google IMAP for legitimate admin replies to support tickets.
    - Strictly filters out outbound ticket alerts (must be Re: / Fwd:).
    - Ignores automated email system senders (brevosend, mailer-daemon, etc).
    - Cleans out quoted email thread.
    - Rejects any body containing internal alert templates.
    - Deduplicates cleanly to avoid re-inserting old messages.
    """
    if not settings.GOOGLE_EMAIL or not settings.GOOGLE_APP_PASSWORD:
        return {"status": "not_configured", "ingested": 0}

    if not _POLL_LOCK.acquire(blocking=False):
        return {"status": "busy", "ingested": 0}

    mail = None
    ingested_count = 0
    try:
        # Run cleanup first
        cleanup_corrupted_messages()

        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=15)
        mail.login(settings.GOOGLE_EMAIL, settings.GOOGLE_APP_PASSWORD.replace(" ", ""))
        mail.select("INBOX")

        status, data = mail.search(None, "ALL")
        if status != "OK" or not data[0]:
            return {"status": "ok", "ingested": 0}

        all_ids = data[0].split()
        recent_ids = all_ids[-25:]

        for eid in reversed(recent_ids):
            try:
                f_status, msg_data = mail.fetch(eid, "(RFC822)")
                if f_status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_email_bytes)

                # 1. Subject Check - MUST be an actual reply
                subj_raw = msg.get("Subject", "")
                decoded_parts = decode_header(subj_raw)
                subject = ""
                for part, enc in decoded_parts:
                    subject += _decode_str(part, enc)

                subj_clean = subject.strip().lower()
                # Strict check: Outbound ticket alerts NEVER start with Re: or Fwd:
                if not (subj_clean.startswith("re:") or subj_clean.startswith("re :") or 
                        subj_clean.startswith("fw:") or subj_clean.startswith("fwd:") or 
                        subj_clean.startswith("re [")):
                    continue

                # 2. Sender Check - Exclude automated system alerts / relays
                from_hdr = (msg.get("From", "") or "").lower()
                if "brevosend" in from_hdr or "mailer-daemon" in from_hdr or "noreply" in from_hdr:
                    continue

                # 3. Extract plain text body
                raw_body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                raw_body += _decode_str(payload, part.get_content_charset())
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        raw_body = _decode_str(payload, msg.get_content_charset())

                # 4. Ticket marker check in the whole body/subject
                is_ticket = (
                    "Session ID:" in raw_body or
                    "Creator ID:" in raw_body or
                    "CoLaunch Support Ticket" in raw_body or
                    "[TICKET-" in raw_body or
                    "colaunch support ticket" in subj_clean
                )
                if not is_ticket:
                    continue

                # 5. Clean reply body to extract ONLY the new human message
                cleaned_text = clean_support_reply_body(raw_body)
                if not cleaned_text or len(cleaned_text.strip()) < 2:
                    continue

                # 6. Extract identifiers
                idents = extract_ticket_identifiers(raw_body, subject)
                session_id = idents.get("session_id")
                creator_id = idents.get("creator_id")
                raw_handle = idents.get("creator_handle") or "@creator"
                clean_handle = "@" + raw_handle.lstrip("@")

                if not session_id and not creator_id:
                    continue

                # 7. Determine Sender Role (Admin vs Creator email reply)
                is_from_admin = (settings.ADMIN_EMAIL and settings.ADMIN_EMAIL.lower() in from_hdr) or ("creatorforgeweb" in from_hdr)
                sender_role = "admin" if is_from_admin else "user"
                sender_name = "Support Team" if is_from_admin else (idents.get("creator_name") or "Creator")

                # Deterministic deduplication
                norm_msg = re.sub(r"\s+", " ", cleaned_text).strip().lower()
                target_key = creator_id or session_id or "general"
                hash_source = f"{sender_role}_{target_key}_{norm_msg}"
                msg_hash = hashlib.sha256(hash_source.encode()).hexdigest()[:16]
                msg_id = f"{sender_role}_reply_{msg_hash}"

                # Check if already in MongoDB
                coll = get_collection("support_messages")
                if coll is not None:
                    # Check by ID or exact matching message content
                    existing = coll.find_one({
                        "$or": [
                            {"id": msg_id},
                            {
                                "sender": sender_role,
                                "message": cleaned_text,
                                "$or": [{"session_id": session_id}, {"creator_id": creator_id}]
                            }
                        ]
                    })
                    if existing:
                        continue

                # Parse date
                date_hdr = msg.get("Date", "")
                created_at_iso = datetime.utcnow().isoformat() + "Z"
                if date_hdr:
                    try:
                        dt = email.utils.parsedate_to_datetime(date_hdr)
                        created_at_iso = dt.isoformat()
                    except Exception:
                        pass

                msg_record = {
                    "id": msg_id,
                    "session_id": session_id,
                    "creator_id": creator_id,
                    "creator_handle": clean_handle,
                    "creator_name": sender_name,
                    "sender": sender_role,
                    "message": cleaned_text,
                    "creator_context": {
                        "source": f"{sender_role}_email_reply",
                        "subject": subject,
                        "date": date_hdr,
                    },
                    "status": "admin_replied" if is_from_admin else "user_replied",
                    "escalated": True,
                    "created_at": created_at_iso,
                }

                # Save to MongoDB
                if coll is not None:
                    coll.update_one({"id": msg_id}, {"$set": msg_record}, upsert=True)

                # Save to SQLite / SQL DB
                if SessionLocal and SupportMessage:
                    try:
                        db = SessionLocal()
                        existing_sql = db.query(SupportMessage).filter(
                            (SupportMessage.id == msg_id) |
                            (
                                (SupportMessage.sender == sender_role) &
                                (SupportMessage.message == cleaned_text) &
                                ((SupportMessage.session_id == session_id) | (SupportMessage.creator_id == creator_id))
                            )
                        ).first()
                        if not existing_sql:
                            import json
                            db_item = SupportMessage(
                                id=msg_id,
                                session_id=session_id,
                                creator_id=creator_id,
                                creator_handle=clean_handle,
                                creator_name=sender_name,
                                sender=sender_role,
                                message=cleaned_text,
                                creator_context=json.dumps(msg_record["creator_context"]),
                                status="admin_replied" if is_from_admin else "user_replied",
                                escalated=True,
                            )
                            db.add(db_item)
                            db.commit()
                        db.close()
                    except Exception as sql_err:
                        logger.warning(f"[Support Poller] SQL save error: {sql_err}")

                ingested_count += 1
                logger.info(f"[Support Poller] Ingested 2-way {sender_role} email reply for {clean_handle} ({session_id}): '{cleaned_text[:50]}...'")

            except Exception as item_err:
                logger.warning(f"[Support Poller] Error parsing message {eid}: {item_err}")
                continue

    except Exception as e:
        logger.error(f"[Support Poller] IMAP polling failed: {e}")
        return {"status": "error", "error": str(e), "ingested": ingested_count}
    finally:
        try:
            if mail:
                mail.close()
                mail.logout()
        except Exception:
            pass
        _POLL_LOCK.release()

    return {"status": "ok", "ingested": ingested_count}


async def start_support_poller_loop(interval_seconds: int = 15):
    """Background async loop checking for admin support replies."""
    global _RUNNING
    _RUNNING = True
    logger.info(f"[Support Poller] Starting IMAP support reply monitor (every {interval_seconds}s)")
    while _RUNNING:
        try:
            await asyncio.to_thread(poll_support_email_replies)
        except Exception as e:
            logger.warning(f"[Support Poller] Loop error: {e}")
        await asyncio.sleep(interval_seconds)


def stop_support_poller_loop():
    global _RUNNING
    _RUNNING = False
    logger.info("[Support Poller] Stopped IMAP support reply monitor")
