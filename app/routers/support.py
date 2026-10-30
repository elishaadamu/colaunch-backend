import uuid
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.support import SupportMessage
from app.mongodb import get_collection, get_mongo_db
from app.integrations.email_provider import EmailProvider
from app.services.llm import call_llm
from app.services.support_inbox_poller import poll_support_email_replies, cleanup_corrupted_messages, CORRUPTED_MARKERS

logger = logging.getLogger("creator_forge.support")
router = APIRouter(prefix="/api/support", tags=["Customer Support"])

email_provider = EmailProvider()


class SupportMessageRequest(BaseModel):
    id: Optional[str] = None
    session_id: Optional[str] = None
    creator_id: Optional[str] = None
    creator_handle: Optional[str] = None
    creator_name: Optional[str] = None
    creator_email: Optional[str] = None
    message: str
    creator_context: Optional[Dict[str, Any]] = None
    force_escalate: Optional[bool] = False


class SupportEscalateRequest(BaseModel):
    session_id: Optional[str] = None
    creator_id: Optional[str] = None
    creator_handle: Optional[str] = None
    creator_name: Optional[str] = None
    creator_email: Optional[str] = None
    reason: Optional[str] = "Creator requested human administrator support"
    creator_context: Optional[Dict[str, Any]] = None


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _format_admin_escalation_email(
    creator_handle: str,
    creator_name: str,
    creator_id: str,
    session_id: str,
    reason: str,
    user_query: str,
    creator_context: dict,
    recent_history: list = None,
    creator_email: str = None
):
    clean_h = (creator_handle or "").lstrip("@").strip()
    handle = f"@{clean_h}" if clean_h else "@creator"
    name = creator_name or "Creator"
    niche = creator_context.get("niche", "Not specified")
    followers = creator_context.get("followers", "Unknown")
    platform = creator_context.get("platform", "YouTube")
    stage = creator_context.get("stage", "Onboarding / Live")
    blueprint = creator_context.get("blueprint", {})
    bp_name = blueprint.get("name", "Custom Platform") if isinstance(blueprint, dict) else str(blueprint)
    pricing = creator_context.get("pricing", "Standard tier")
    base_url = (settings.FRONTEND_URL or "https://colaunch-web.vercel.app").rstrip("/")
    dashboard_url = f"{base_url}/dashboard"
    hub_url = f"{base_url}/{clean_h.lower()}/{creator_id}" if clean_h and creator_id else dashboard_url

    history_html = ""
    history_text = ""
    if recent_history:
        history_rows = []
        for m in recent_history[-8:]:
            snd = m.get("sender", "user").capitalize()
            txt = (m.get("message", "") or "").replace("@@", "@")
            history_rows.append(f"<p style='margin:4px 0;'><strong>{snd}:</strong> {txt}</p>")
            history_text += f"{snd}: {txt}\n"
        history_html = "".join(history_rows)

    subject = f"[CoLaunch Support Ticket] {handle} ({name}) - Human Support Requested #{session_id}#{creator_id}#"

    body_text = f"""CoLaunch Customer Support Alert
====================================
A creator has requested administrator assistance or submitted a question requiring human review.

CREATOR DETAILS:
- Name: {name}
- Email: {creator_email or 'Not provided'}
- Handle: {handle}
- Creator ID: {creator_id or 'N/A'}
- Session ID: {session_id or 'N/A'}
- Platform: {platform}
- Niche: {niche}
- Audience: {followers}
- Stage: {stage}
- Blueprint: {bp_name}
- Pricing / Monetization: {pricing}
- Creator Dashboard URL: {dashboard_url}
- Live Hub URL: {hub_url}

REASON / TRIGGER:
{reason}

USER MESSAGE:
{user_query}

RECENT CONVERSATION HISTORY:
{history_text or 'None recorded.'}

====================================
Dispatched via CoLaunch Support Operations.
Admin Inbox: {settings.ADMIN_EMAIL}
"""

    body_html = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0e14; color: #f1f5f9; padding: 24px; margin: 0;">
  <div style="max-width: 620px; margin: 0 auto; background: #121722; border: 1px solid #232b3e; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px; border-bottom: 1px solid #232b3e; padding-bottom: 16px;">
      <h2 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: 700;">CoLaunch Support Ticket</h2>
      <span style="background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); padding: 4px 10px; border-radius: 9999px; font-size: 11px; font-weight: 700; text-transform: uppercase;">Urgent Escalation</span>
    </div>

    <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 16px; margin-bottom: 20px;">
      <h3 style="margin: 0 0 12px 0; font-size: 14px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em;">Creator Workspace Context</h3>
      <table style="width: 100%; font-size: 13px; color: #cbd5e1; border-collapse: collapse;">
        <tr><td style="padding: 4px 0; color: #94a3b8; width: 140px;">Creator Name:</td><td style="padding: 4px 0; font-weight: 600; color: #ffffff;">{name} (<span style="color: #38bdf8;">{handle}</span>)</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Contact Email:</td><td style="padding: 4px 0; font-weight: 600; color: #38bdf8;">{creator_email or 'Not provided'}</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Creator ID:</td><td style="padding: 4px 0; font-family: monospace;">{creator_id or 'N/A'}</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Session ID:</td><td style="padding: 4px 0; font-family: monospace;">{session_id or 'N/A'}</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Dashboard URL:</td><td style="padding: 4px 0;"><a href="{dashboard_url}" style="color: #38bdf8; text-decoration: underline;">{dashboard_url}</a></td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Niche / Audience:</td><td style="padding: 4px 0;">{niche} ({followers} on {platform})</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Current Stage:</td><td style="padding: 4px 0;">{stage}</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Blueprint:</td><td style="padding: 4px 0; color: #10b981; font-weight: 600;">{bp_name}</td></tr>
      </table>
    </div>

    <div style="background: #171d2b; border-left: 4px solid #38bdf8; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
      <p style="margin: 0 0 6px 0; font-size: 11px; text-transform: uppercase; color: #38bdf8; font-weight: 700;">Latest Creator Inquiry</p>
      <p style="margin: 0; font-size: 14px; color: #ffffff; line-height: 1.5; font-weight: 500;">{user_query}</p>
    </div>

    {f'''<div style="background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 16px; margin-bottom: 20px;">
      <h4 style="margin: 0 0 10px 0; font-size: 12px; text-transform: uppercase; color: #94a3b8;">Conversation Transcript</h4>
      <div style="font-size: 12.5px; color: #94a3b8; max-height: 220px; overflow-y: auto;">
        {history_html}
      </div>
    </div>''' if history_html else ''}

    <div style="border-top: 1px solid #232b3e; padding-top: 16px; font-size: 12px; color: #64748b; display: flex; justify-content: space-between;">
      <span>Sent by CoLaunch Autonomous Support Relay</span>
      <span>Admin Target: {settings.ADMIN_EMAIL}</span>
    </div>
  </div>
</body>
</html>"""

    return subject, body_html, body_text


def _format_creator_confirmation_email(
    creator_name: str,
    creator_handle: str,
    creator_id: str,
    session_id: str,
    user_query: str,
    creator_context: dict
):
    clean_h = (creator_handle or "").lstrip("@").strip()
    handle = f"@{clean_h}" if clean_h else "@creator"
    name = creator_name or "Creator"
    blueprint = creator_context.get("blueprint", {})
    bp_name = blueprint.get("name", "Custom Platform") if isinstance(blueprint, dict) else str(blueprint)
    
    base_url = (settings.FRONTEND_URL or "https://colaunch-web.vercel.app").rstrip("/")
    dashboard_url = f"{base_url}/dashboard"
    hub_url = f"{base_url}/{clean_h.lower()}/{creator_id}" if clean_h and creator_id else dashboard_url

    subject = f"[CoLaunch Support Ticket] Support Request Received · #{session_id}#{creator_id}#"

    body_text = f"""Hi {name},

We have received your support inquiry and escalated it directly to our human operations team.

An administrator is reviewing your workspace for {handle} and will follow up with you promptly.

==================================================
💬 TWO-WAY EMAIL COMMUNICATION:
You don't need to stay on the CoLaunch app! 
You can simply reply directly to this email from your inbox at any time to continue this conversation with our administration team. Every message you send will be routed directly to our operations desk and synced in your chat.
==================================================

YOUR CREATOR DASHBOARD:
Access and manage your {bp_name} suite anytime:
{dashboard_url}

Direct Suite Link:
{hub_url}

YOUR SUBMITTED INQUIRY:
"{user_query}"

Best regards,
CoLaunch Operations Team
"""

    body_html = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0e14; color: #f1f5f9; padding: 24px; margin: 0;">
  <div style="max-width: 600px; margin: 0 auto; background: #121722; border: 1px solid #232b3e; border-radius: 16px; padding: 28px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
    
    <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #232b3e; padding-bottom: 18px; margin-bottom: 24px;">
      <div>
        <h2 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: 800; letter-spacing: -0.02em;">CoLaunch Support</h2>
        <span style="font-size: 12px; color: #94a3b8;">Creator Priority Operations Desk</span>
      </div>
      <span style="background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 12px; border-radius: 9999px; font-size: 11px; font-weight: 700; text-transform: uppercase;">Team Assigned</span>
    </div>

    <p style="font-size: 15px; color: #e2e8f0; line-height: 1.6; margin: 0 0 16px 0;">
      Hi <strong style="color: #ffffff;">{name}</strong>,
    </p>

    <p style="font-size: 14px; color: #cbd5e1; line-height: 1.6; margin: 0 0 20px 0;">
      We've received your inquiry regarding your <strong style="color: #38bdf8;">{bp_name}</strong> platform (<span style="color: #94a3b8;">{handle}</span>). Your ticket has been escalated directly to our senior human operations team, and an administrator will follow up with you promptly.
    </p>

    <!-- 2-Way Email Callout Card -->
    <div style="background: linear-gradient(135deg, rgba(56, 189, 248, 0.1), rgba(99, 102, 241, 0.1)); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
      <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
        <span style="font-size: 16px;">💬</span>
        <strong style="color: #38bdf8; font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em;">2-Way Email Sync Active</strong>
      </div>
      <p style="margin: 0; font-size: 13px; color: #e2e8f0; line-height: 1.5;">
        You don't need to keep the app open! You can <strong>reply directly to this email from your phone or desktop inbox</strong> at any time. Your reply will be synced seamlessly to our operations team and your dashboard chat.
      </p>
    </div>

    <!-- Submitted Inquiry Box -->
    <div style="background: #171d2b; border-left: 4px solid #10b981; border-radius: 8px; padding: 14px 16px; margin-bottom: 24px;">
      <p style="margin: 0 0 4px 0; font-size: 11px; text-transform: uppercase; color: #10b981; font-weight: 700;">Your Message</p>
      <p style="margin: 0; font-size: 13.5px; color: #f1f5f9; line-height: 1.5;">"{user_query}"</p>
    </div>

    <!-- Creator Dashboard CTA Button -->
    <div style="text-align: center; margin: 28px 0;">
      <a href="{dashboard_url}" style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 700; padding: 12px 28px; border-radius: 10px; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);">
        Access Your Creator Dashboard &rarr;
      </a>
      <p style="margin: 8px 0 0 0; font-size: 11px; color: #64748b;">
        Direct URL: <a href="{dashboard_url}" style="color: #38bdf8; text-decoration: underline;">{dashboard_url}</a>
      </p>
    </div>

    <div style="border-top: 1px solid #232b3e; padding-top: 16px; font-size: 11.5px; color: #64748b; display: flex; justify-content: space-between;">
      <span>CoLaunch Autonomous Operations</span>
      <span>Ticket #{session_id}</span>
    </div>
  </div>
</body>
</html>"""

    return subject, body_html, body_text


def _save_message_to_db(msg_dict: dict, sql_db: Session = None):
    # 1. MongoDB Save
    try:
        coll = get_collection("support_messages")
        if coll is not None:
            coll.update_one(
                {"id": msg_dict["id"]},
                {"$set": msg_dict},
                upsert=True
            )
    except Exception as e:
        logger.warning(f"[Support] Failed saving to MongoDB: {e}")

    # 2. SQLite / PostgreSQL Save
    if sql_db:
        try:
            db_item = sql_db.query(SupportMessage).filter(SupportMessage.id == msg_dict["id"]).first()
            if not db_item:
                db_item = SupportMessage(
                    id=msg_dict["id"],
                    session_id=msg_dict.get("session_id"),
                    creator_id=msg_dict.get("creator_id"),
                    creator_handle=msg_dict.get("creator_handle"),
                    creator_name=msg_dict.get("creator_name"),
                    sender=msg_dict.get("sender", "user"),
                    message=msg_dict.get("message", ""),
                    creator_context=json.dumps(msg_dict.get("creator_context") or {}),
                    status=msg_dict.get("status", "active"),
                    escalated=bool(msg_dict.get("escalated", False)),
                )
                sql_db.add(db_item)
                sql_db.commit()
            else:
                db_item.status = msg_dict.get("status", db_item.status)
                db_item.escalated = bool(msg_dict.get("escalated", db_item.escalated))
                sql_db.commit()
        except Exception as e:
            logger.warning(f"[Support] Failed saving to SQL DB: {e}")
            try:
                sql_db.rollback()
            except Exception:
                pass


def _is_clean_message(msg: dict) -> bool:
    txt = msg.get("message") or ""
    txt_lower = txt.lower()
    for m in CORRUPTED_MARKERS:
        if m.lower() in txt_lower:
            return False
    return True


def _fetch_history(session_id: str = None, creator_id: str = None, limit: int = 50, sql_db: Session = None) -> List[dict]:
    results = []
    # 1. Try MongoDB
    try:
        coll = get_collection("support_messages")
        if coll is not None:
            query = {}
            if session_id and creator_id:
                query = {"$or": [{"session_id": session_id}, {"creator_id": creator_id}]}
            elif session_id:
                query = {"session_id": session_id}
            elif creator_id:
                query = {"creator_id": creator_id}

            cursor = coll.find(query).sort("created_at", 1).limit(limit * 2)
            seen_admin_texts = set()
            for doc in cursor:
                doc["_id"] = str(doc.get("_id", ""))
                if not _is_clean_message(doc):
                    continue
                # Normalize handle to avoid @@
                if doc.get("creator_handle"):
                    doc["creator_handle"] = "@" + doc["creator_handle"].lstrip("@")
                if doc.get("message"):
                    doc["message"] = doc["message"].replace("@@", "@")
                # Deduplicate admin messages
                if doc.get("sender") == "admin":
                    cleaned_txt = doc.get("message", "").strip().lower()
                    if cleaned_txt in seen_admin_texts:
                        continue
                    seen_admin_texts.add(cleaned_txt)
                results.append(doc)
            if results:
                return results[-limit:]
    except Exception as e:
        logger.warning(f"[Support] MongoDB history fetch error: {e}")

    # 2. Fallback SQL
    if sql_db:
        try:
            q = sql_db.query(SupportMessage)
            if session_id and creator_id:
                q = q.filter((SupportMessage.session_id == session_id) | (SupportMessage.creator_id == creator_id))
            elif session_id:
                q = q.filter(SupportMessage.session_id == session_id)
            elif creator_id:
                q = q.filter(SupportMessage.creator_id == creator_id)

            items = q.order_by(SupportMessage.created_at.asc()).limit(limit * 2).all()
            seen_admin_texts = set()
            for it in items:
                msg_txt = it.message or ""
                if not _is_clean_message({"message": msg_txt}):
                    continue
                # Deduplicate admin messages
                if it.sender == "admin":
                    cleaned_txt = msg_txt.strip().lower()
                    if cleaned_txt in seen_admin_texts:
                        continue
                    seen_admin_texts.add(cleaned_txt)

                ctx = {}
                try:
                    ctx = json.loads(it.creator_context) if it.creator_context else {}
                except Exception:
                    pass
                results.append({
                    "id": it.id,
                    "session_id": it.session_id,
                    "creator_id": it.creator_id,
                    "creator_handle": "@" + (it.creator_handle or "creator").lstrip("@"),
                    "creator_name": it.creator_name,
                    "sender": it.sender,
                    "message": msg_txt.replace("@@", "@"),
                    "creator_context": ctx,
                    "status": it.status,
                    "escalated": it.escalated,
                    "created_at": it.created_at.isoformat() if it.created_at else _now_iso(),
                })
        except Exception as e:
            logger.warning(f"[Support] SQL history fetch error: {e}")

    return results[-limit:]


@router.get("/history")
def get_support_history(
    session_id: Optional[str] = Query(None),
    creator_id: Optional[str] = Query(None),
    limit: int = Query(50),
    db: Session = Depends(get_db)
):
    """
    Returns unified support chat history.
    Can be picked by session_id (during onboarding) or by creator_id (in dashboard).
    """
    try:
        cleanup_corrupted_messages()
        poll_support_email_replies()
    except Exception as e:
        logger.debug(f"[Support] Instant poller check: {e}")

    history = _fetch_history(session_id=session_id, creator_id=creator_id, limit=limit, sql_db=db)
    return {"status": "ok", "count": len(history), "messages": history}


@router.post("/sync")
def sync_support_inbox(
    session_id: Optional[str] = Query(None),
    creator_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Explicitly triggers IMAP check for new email replies."""
    poll_res = {"status": "skipped"}
    try:
        poll_res = poll_support_email_replies()
    except Exception as e:
        logger.warning(f"[Support] Manual sync error: {e}")
    history = _fetch_history(session_id=session_id, creator_id=creator_id, limit=50, sql_db=db)
    return {"status": "ok", "poll": poll_res, "count": len(history), "messages": history}


@router.post("/message")
def post_support_message(req: SupportMessageRequest, db: Session = Depends(get_db)):
    """
    User sends a message to Customer Support.
    - Grounded in active creatorData.
    - Answers via Gemini/OpenAI AI LLM.
    - If user asks for human/admin or questions cannot be answered, escalates to the operations team.
    - Saves all messages to general support_messages table.
    """
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"
    creator_id = req.creator_id or ""
    raw_handle = req.creator_handle or ""
    clean_handle = raw_handle.lstrip("@").strip()
    display_handle = f"@{clean_handle}" if clean_handle else "@creator"
    creator_name = req.creator_name or "Creator"
    context = req.creator_context or {}
    creator_email = (req.creator_email or context.get("email") or context.get("contactEmail") or "").strip()

    user_msg_id = req.id or str(uuid.uuid4())
    user_record = {
        "id": user_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": display_handle,
        "creator_name": creator_name,
        "creator_email": creator_email,
        "sender": "user",
        "message": req.message,
        "creator_context": context,
        "status": "active",
        "escalated": False,
        "created_at": _now_iso(),
    }
    _save_message_to_db(user_record, db)

    # Check for escalation intent
    user_lower = req.message.lower().strip()
    clean_punct = re.sub(r"[!?.~,]", "", user_lower).strip()
    greetings = {"hi", "hello", "hey", "good morning", "good evening", "good afternoon", "greetings", "sup", "yo", "hi there", "hello there", "howdy"}
    is_greeting = clean_punct in greetings

    escalation_triggers = [
        "human", "talk to human", "speak to human", "real person", "admin", "agent",
        "representative", "call me", "phone", "refund", "chargeback", "legal",
        "contract amendment", "bank account error", "bug report", "escalate"
    ]
    should_escalate = req.force_escalate or any(t in user_lower for t in escalation_triggers)

    assistant_msg_id = str(uuid.uuid4())
    assistant_reply = ""
    is_escalated = False

    blueprint = context.get("blueprint") or {}
    bp_name = blueprint.get("name", "Creator Hub") if isinstance(blueprint, dict) else str(blueprint)
    features = context.get("features") or (blueprint.get("features") if isinstance(blueprint, dict) else [])
    features_str = ", ".join(features) if isinstance(features, list) else str(features)
    followers = context.get("followers", "audience")
    niche = context.get("niche", "creator niche")
    platform = context.get("platform", "YouTube")
    stage = context.get("stage", "creator onboarding/dashboard")
    pricing = context.get("pricing", "$29/mo membership & product vault")

    if should_escalate:
        is_escalated = True
        recent = _fetch_history(session_id=session_id, creator_id=creator_id, limit=10, sql_db=db)
        subject, body_html, body_text = _format_admin_escalation_email(
            creator_handle=display_handle,
            creator_name=creator_name,
            creator_id=creator_id,
            session_id=session_id,
            reason=f"Creator query: '{req.message}'",
            user_query=req.message,
            creator_context=context,
            recent_history=recent,
            creator_email=creator_email
        )
        email_sent = False
        try:
            email_provider.send(
                to_email=settings.ADMIN_EMAIL,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                from_name="CoLaunch Creator Support Desk",
                reply_to=creator_email if creator_email else None
            )
            email_sent = True
            logger.info(f"[Support] Escalation email dispatched to admin {settings.ADMIN_EMAIL}")
        except Exception as err:
            logger.error(f"[Support] Failed sending escalation email: {err}")

        # Send 2-way confirmation email to creator
        if creator_email and "@" in creator_email:
            try:
                c_subj, c_html, c_text = _format_creator_confirmation_email(
                    creator_name=creator_name,
                    creator_handle=display_handle,
                    creator_id=creator_id,
                    session_id=session_id,
                    user_query=req.message,
                    creator_context=context
                )
                email_provider.send(
                    to_email=creator_email,
                    subject=c_subj,
                    body_html=c_html,
                    body_text=c_text,
                    from_name="CoLaunch Support Team",
                    reply_to=settings.ADMIN_EMAIL
                )
                logger.info(f"[Support] Dispatched 2-way confirmation email to creator: {creator_email}")
            except Exception as c_err:
                logger.warning(f"[Support] Failed sending creator confirmation email: {c_err}")

        if creator_email:
            assistant_reply = (
                f"I have escalated your inquiry directly to our dedicated support team with your workspace snapshot. "
                f"A confirmation has been sent to your email ({creator_email}). "
                f"You can continue chatting right here, or simply reply directly to that email from your inbox anytime!"
            )
        else:
            assistant_reply = (
                f"I have escalated your inquiry directly to our dedicated support team with your workspace snapshot for {display_handle}. "
                f"Our team is reviewing it now and will follow up with you promptly. You can also share any further details right here."
            )
    elif is_greeting:
        greeting_name = creator_name if creator_name and creator_name != "Creator" else display_handle
        assistant_reply = f"Hi {greeting_name}! How can I help you with your {bp_name} platform today?"
    else:
        # Grounded AI generation
        system_prompt = f"""You are CoLaunch's AI Customer Support Agent for Creators. You provide real-time, helpful, smooth, friendly, and practical support.
CRITICAL GROUNDING: You are supporting THIS specific creator right now:
- Creator Name: {creator_name}
- Creator Email: {creator_email or 'Not provided'}
- Handle: {display_handle}
- Niche: {niche}
- Audience: {followers} followers on {platform}
- Current App / Blueprint: {bp_name}
- Configured Features: {features_str}
- Stage in CoLaunch: {stage}
- Monetization / Pricing: {pricing}

Guidelines:
1. Address the creator by their name ({creator_name}) warmly and naturally.
2. Speak smoothly, directly, concisely, and helpfully (under 75 words / 1-2 short paragraphs maximum).
3. Ground your answers specifically in their platform ({bp_name}) and niche ({niche}), but DO NOT dump or recite a whole list of stats, subscriber counts, or zero monetization unless the creator explicitly asks about them.
4. If the user asks something you cannot answer (such as custom contract adjustments, bank routing verification, server hardware bugs) or asks for human staff, reply with helpful reassurance and include the token [ESCALATE_TO_ADMIN] in your reply.
5. When referring to the creator handle, always use single '@', like {display_handle} (never double '@@')."""

        user_prompt = f"Creator {creator_name} ({display_handle}) asks: \"{req.message}\""

        try:
            ai_text = call_llm(
                prompt=user_prompt,
                max_tokens=300,
                system_prompt=system_prompt
            )
            if "[ESCALATE_TO_ADMIN]" in ai_text:
                is_escalated = True
                ai_text = ai_text.replace("[ESCALATE_TO_ADMIN]", "").strip()
                # Send escalation email
                recent = _fetch_history(session_id=session_id, creator_id=creator_id, limit=10, sql_db=db)
                subject, body_html, body_text = _format_admin_escalation_email(
                    creator_handle=display_handle,
                    creator_name=creator_name,
                    creator_id=creator_id,
                    session_id=session_id,
                    reason="AI determined human intervention required",
                    user_query=req.message,
                    creator_context=context,
                    recent_history=recent,
                    creator_email=creator_email
                )
                try:
                    email_provider.send(
                        to_email=settings.ADMIN_EMAIL,
                        subject=subject,
                        body_html=body_html,
                        body_text=body_text,
                        from_name="CoLaunch Creator Support Desk",
                        reply_to=creator_email if creator_email else None
                    )
                except Exception as e:
                    logger.warning(f"[Support] Failed sending auto-escalation email to admin: {e}")

                if creator_email and "@" in creator_email:
                    try:
                        c_subj, c_html, c_text = _format_creator_confirmation_email(
                            creator_name=creator_name,
                            creator_handle=display_handle,
                            creator_id=creator_id,
                            session_id=session_id,
                            user_query=req.message,
                            creator_context=context
                        )
                        email_provider.send(
                            to_email=creator_email,
                            subject=c_subj,
                            body_html=c_html,
                            body_text=c_text,
                            from_name="CoLaunch Support Team",
                            reply_to=settings.ADMIN_EMAIL
                        )
                    except Exception as ce:
                        logger.warning(f"[Support] Failed sending auto-escalation email to creator: {ce}")

            assistant_reply = ai_text.replace("@@", "@")
        except Exception as e:
            logger.error(f"[Support] AI generation error: {e}")
            assistant_reply = (
                f"I'm reviewing your setup, {creator_name}. "
                f"Your {bp_name} suite is configured for {niche} with {features_str}. "
                f"If you'd like to speak directly with our human support team, let me know and I'll connect you right away."
            )

    assistant_record = {
        "id": assistant_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": display_handle,
        "creator_name": creator_name,
        "sender": "assistant",
        "message": assistant_reply,
        "creator_context": context,
        "status": "escalated_to_admin" if is_escalated else "ai_resolved",
        "escalated": is_escalated,
        "created_at": _now_iso(),
    }
    _save_message_to_db(assistant_record, db)

    return {
        "status": "ok",
        "reply": assistant_reply,
        "escalated": is_escalated,
        "message_id": assistant_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
    }


@router.post("/escalate")
def escalate_to_admin(req: SupportEscalateRequest, db: Session = Depends(get_db)):
    """
    Manually escalate a creator support thread to human operations team.
    """
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"
    creator_id = req.creator_id or ""
    creator_handle = req.creator_handle or ""
    creator_name = req.creator_name or "Creator"
    context = req.creator_context or {}
    creator_email = (req.creator_email or context.get("email") or context.get("contactEmail") or "").strip()

    recent = _fetch_history(session_id=session_id, creator_id=creator_id, limit=12, sql_db=db)
    last_query = req.reason or "Creator requested direct admin contact"
    if recent and recent[-1].get("sender") == "user":
        last_query = recent[-1].get("message", last_query)

    subject, body_html, body_text = _format_admin_escalation_email(
        creator_handle=creator_handle,
        creator_name=creator_name,
        creator_id=creator_id,
        session_id=session_id,
        reason=req.reason,
        user_query=last_query,
        creator_context=context,
        recent_history=recent,
        creator_email=creator_email
    )

    send_status = "sent"
    try:
        email_provider.send(
            to_email=settings.ADMIN_EMAIL,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_name="CoLaunch Creator Support Desk",
            reply_to=creator_email if creator_email else None
        )
    except Exception as err:
        logger.error(f"[Support] Escalation email failure to admin: {err}")
        send_status = f"failed: {err}"

    if creator_email and "@" in creator_email:
        try:
            c_subj, c_html, c_text = _format_creator_confirmation_email(
                creator_name=creator_name,
                creator_handle=creator_handle,
                creator_id=creator_id,
                session_id=session_id,
                user_query=last_query,
                creator_context=context
            )
            email_provider.send(
                to_email=creator_email,
                subject=c_subj,
                body_html=c_html,
                body_text=c_text,
                from_name="CoLaunch Support Team",
                reply_to=settings.ADMIN_EMAIL
            )
            logger.info(f"[Support] Dispatched 2-way confirmation email to creator: {creator_email}")
        except Exception as cerr:
            logger.warning(f"[Support] Failed sending creator confirmation email: {cerr}")

    confirm_msg_id = str(uuid.uuid4())
    confirm_text = (
        f"Your ticket has been escalated directly to our senior human operations team. "
        f"A confirmation has also been sent to your email ({creator_email}). "
        f"You can continue chatting right here, or reply directly to that email from your inbox at any time!"
        if creator_email else
        "Your ticket has been escalated to our dedicated operations team. An administrator will follow up with you promptly."
    )
    confirm_record = {
        "id": confirm_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": creator_handle,
        "creator_name": creator_name,
        "sender": "assistant",
        "message": confirm_text,
        "creator_context": context,
        "status": "escalated_to_admin",
        "escalated": True,
        "created_at": _now_iso(),
    }
    _save_message_to_db(confirm_record, db)

    return {
        "status": "ok",
        "escalated": True,
        "email_status": send_status,
        "message_id": confirm_msg_id,
        "reply": confirm_record["message"],
    }


@router.get("/status")
def get_support_status():
    """
    Returns live connectivity status of AI and support desk.
    """
    return {
        "status": "ok",
        "ai_provider": settings.ACTIVE_AI_PROVIDER,
        "has_gemini": bool(settings.GEMINI_API_KEY),
        "has_openai": bool(settings.OPENAI_API_KEY),
        "support_desk_active": True,
        "team_relay_active": bool(email_provider.is_configured()),
    }

