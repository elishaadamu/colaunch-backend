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


def _decode_str(val, charset=None):
    if isinstance(val, bytes):
        try:
            return val.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return val.decode("utf-8", errors="replace")
    return val


def clean_support_reply_body(body: str) -> str:
    """Strip quoted email thread and reply metadata to get only the new admin text."""
    if not body:
        return ""
    text = body.replace("\r\n", "\n")

    # Match standard email reply delimiters
    split_patterns = [
        re.compile(r"\nOn\s+.*wrote:\s*", re.IGNORECASE | re.DOTALL),
        re.compile(r"-+\s*Original Message\s*-+", re.IGNORECASE),
        re.compile(r"_{3,}\s*$", re.MULTILINE),
        re.compile(r"^From:\s+CoLaunch Support", re.IGNORECASE | re.MULTILINE),
        re.compile(r"CoLaunch Support Ticket Urgent Escalation", re.IGNORECASE),
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
        res["creator_handle"] = "@" + handle_match.group(1).strip().lstrip("@")

    # 4. Creator Name
    name_match = re.search(r"Creator:\s*([^\n\(]+)\s*\(", full_text)
    if name_match:
        res["creator_name"] = name_match.group(1).strip()

    return res


def poll_support_email_replies() -> Dict[str, Any]:
    """
    Polls Google IMAP for admin replies to support tickets.
    Extracts the admin reply, links it to session_id / creator_id,
    and saves to support_messages general table.
    """
    if not settings.GOOGLE_EMAIL or not settings.GOOGLE_APP_PASSWORD:
        return {"status": "not_configured", "ingested": 0}

    if not _POLL_LOCK.acquire(blocking=False):
        return {"status": "busy", "ingested": 0}

    mail = None
    ingested_count = 0
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=15)
        mail.login(settings.GOOGLE_EMAIL, settings.GOOGLE_APP_PASSWORD.replace(" ", ""))
        mail.select("INBOX")

        # Search recent messages in inbox
        status, data = mail.search(None, "ALL")
        if status != "OK" or not data[0]:
            return {"status": "ok", "ingested": 0}

        all_ids = data[0].split()
        # Inspect the last 20 emails
        recent_ids = all_ids[-20:]

        for eid in reversed(recent_ids):
            try:
                f_status, msg_data = mail.fetch(eid, "(RFC822)")
                if f_status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_email_bytes)

                subj_raw = msg.get("Subject", "")
                decoded_parts = decode_header(subj_raw)
                subject = ""
                for part, enc in decoded_parts:
                    subject += _decode_str(part, enc)

                # Check if this email is a support reply
                subj_lower = subject.lower().strip()
                is_support_subj = "support" in subj_lower or "ticket" in subj_lower or "colaunch" in subj_lower

                # Extract plain text body
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

                # Check body for support ticket markers if subject wasn't definitive
                is_support_body = (
                    "Session ID:" in raw_body or
                    "Creator ID:" in raw_body or
                    "CoLaunch Support Ticket" in raw_body or
                    "[TICKET-" in raw_body
                )

                if not (is_support_subj and is_support_body):
                    continue

                # Clean the new admin reply text
                cleaned_text = clean_support_reply_body(raw_body)
                if not cleaned_text or len(cleaned_text) < 2:
                    continue

                # Extract ticket identifiers
                idents = extract_ticket_identifiers(raw_body, subject)
                session_id = idents.get("session_id")
                creator_id = idents.get("creator_id")
                creator_handle = idents.get("creator_handle") or "@creator"

                if not session_id and not creator_id:
                    continue

                # Generate unique message hash for deduplication
                date_hdr = msg.get("Date", "")
                hash_source = f"{session_id}_{creator_id}_{cleaned_text}_{date_hdr}"
                msg_hash = hashlib.sha256(hash_source.encode()).hexdigest()[:16]
                msg_id = f"admin_reply_{msg_hash}"

                # Parse date
                created_at_iso = datetime.utcnow().isoformat() + "Z"
                if date_hdr:
                    try:
                        dt = email.utils.parsedate_to_datetime(date_hdr)
                        created_at_iso = dt.isoformat()
                    except Exception:
                        pass

                # Check if already exists in MongoDB
                coll = get_collection("support_messages")
                if coll is not None:
                    existing = coll.find_one({"id": msg_id})
                    if existing:
                        continue

                # Prepare record
                admin_record = {
                    "id": msg_id,
                    "session_id": session_id,
                    "creator_id": creator_id,
                    "creator_handle": creator_handle,
                    "creator_name": "Support Team",
                    "sender": "admin",
                    "message": cleaned_text,
                    "creator_context": {
                        "source": "admin_email_reply",
                        "subject": subject,
                        "date": date_hdr,
                    },
                    "status": "admin_replied",
                    "escalated": True,
                    "created_at": created_at_iso,
                }

                # Save to MongoDB
                if coll is not None:
                    coll.update_one({"id": msg_id}, {"$set": admin_record}, upsert=True)

                # Save to SQLite / SQL DB
                try:
                    db = SessionLocal()
                    existing_sql = db.query(SupportMessage).filter(SupportMessage.id == msg_id).first()
                    if not existing_sql:
                        import json
                        db_item = SupportMessage(
                            id=msg_id,
                            session_id=session_id,
                            creator_id=creator_id,
                            creator_handle=creator_handle,
                            creator_name="Support Team",
                            sender="admin",
                            message=cleaned_text,
                            creator_context=json.dumps(admin_record["creator_context"]),
                            status="admin_replied",
                            escalated=True,
                        )
                        db.add(db_item)
                        db.commit()
                    db.close()
                except Exception as sql_err:
                    logger.warning(f"[Support Poller] SQL save notice: {sql_err}")

                ingested_count += 1
                logger.info(f"[Support Poller] Ingested admin reply for {creator_handle} ({session_id}): '{cleaned_text[:50]}...'")

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
