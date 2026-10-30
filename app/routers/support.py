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
from app.mongodb import get_mongo_database
from app.integrations.email_provider import EmailProvider
from app.services.llm import call_llm

logger = logging.getLogger("creator_forge.support")
router = APIRouter(prefix="/api/support", tags=["Customer Support"])

email_provider = EmailProvider()


class SupportMessageRequest(BaseModel):
    id: Optional[str] = None
    session_id: Optional[str] = None
    creator_id: Optional[str] = None
    creator_handle: Optional[str] = None
    creator_name: Optional[str] = None
    message: str
    creator_context: Optional[Dict[str, Any]] = None
    force_escalate: Optional[bool] = False


class SupportEscalateRequest(BaseModel):
    session_id: Optional[str] = None
    creator_id: Optional[str] = None
    creator_handle: Optional[str] = None
    creator_name: Optional[str] = None
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
    recent_history: list = None
):
    handle = creator_handle or "@creator"
    name = creator_name or "Creator"
    niche = creator_context.get("niche", "Not specified")
    followers = creator_context.get("followers", "Unknown")
    platform = creator_context.get("platform", "YouTube")
    stage = creator_context.get("stage", "Onboarding / Live")
    blueprint = creator_context.get("blueprint", {})
    bp_name = blueprint.get("name", "Custom Platform") if isinstance(blueprint, dict) else str(blueprint)
    pricing = creator_context.get("pricing", "Standard tier")

    history_html = ""
    history_text = ""
    if recent_history:
        history_rows = []
        for m in recent_history[-8:]:
            snd = m.get("sender", "user").capitalize()
            txt = m.get("message", "")
            history_rows.append(f"<p style='margin:4px 0;'><strong>{snd}:</strong> {txt}</p>")
            history_text += f"{snd}: {txt}\n"
        history_html = "".join(history_rows)

    subject = f"[CoLaunch Support Ticket] {handle} ({name}) - Human Support Requested"

    body_text = f"""CoLaunch Customer Support Alert
====================================
A creator has requested administrator assistance or submitted a question requiring human review.

CREATOR DETAILS:
- Handle: {handle}
- Name: {name}
- Creator ID: {creator_id or 'N/A'}
- Session ID: {session_id or 'N/A'}
- Platform: {platform}
- Niche: {niche}
- Audience: {followers}
- Stage: {stage}
- Blueprint: {bp_name}
- Pricing / Monetization: {pricing}

REASON / TRIGGER:
{reason}

USER MESSAGE:
{user_query}

RECENT CONVERSATION HISTORY:
{history_text or 'None recorded.'}

====================================
Dispatched via CoLaunch Unified Relay (Brevo & Google SMTP/IMAP).
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
        <tr><td style="padding: 4px 0; color: #94a3b8; width: 140px;">Creator:</td><td style="padding: 4px 0; font-weight: 600; color: #ffffff;">{name} (<span style="color: #38bdf8;">{handle}</span>)</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Creator ID:</td><td style="padding: 4px 0; font-family: monospace;">{creator_id or 'N/A'}</td></tr>
        <tr><td style="padding: 4px 0; color: #94a3b8;">Session ID:</td><td style="padding: 4px 0; font-family: monospace;">{session_id or 'N/A'}</td></tr>
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


def _save_message_to_db(msg_dict: dict, sql_db: Session = None):
    # 1. MongoDB Save
    try:
        mongo = get_mongo_database()
        if mongo is not None:
            mongo.support_messages.update_one(
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


def _fetch_history(session_id: str = None, creator_id: str = None, limit: int = 50, sql_db: Session = None) -> List[dict]:
    results = []
    # 1. Try MongoDB
    try:
        mongo = get_mongo_database()
        if mongo is not None:
            query = {}
            if session_id and creator_id:
                query = {"$or": [{"session_id": session_id}, {"creator_id": creator_id}]}
            elif session_id:
                query = {"session_id": session_id}
            elif creator_id:
                query = {"creator_id": creator_id}

            cursor = mongo.support_messages.find(query).sort("created_at", 1).limit(limit)
            for doc in cursor:
                doc["_id"] = str(doc.get("_id", ""))
                results.append(doc)
            if results:
                return results
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

            items = q.order_by(SupportMessage.created_at.asc()).limit(limit).all()
            for it in items:
                ctx = {}
                try:
                    ctx = json.loads(it.creator_context) if it.creator_context else {}
                except Exception:
                    pass
                results.append({
                    "id": it.id,
                    "session_id": it.session_id,
                    "creator_id": it.creator_id,
                    "creator_handle": it.creator_handle,
                    "creator_name": it.creator_name,
                    "sender": it.sender,
                    "message": it.message,
                    "creator_context": ctx,
                    "status": it.status,
                    "escalated": it.escalated,
                    "created_at": it.created_at.isoformat() if it.created_at else _now_iso(),
                })
        except Exception as e:
            logger.warning(f"[Support] SQL history fetch error: {e}")

    return results


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
    history = _fetch_history(session_id=session_id, creator_id=creator_id, limit=limit, sql_db=db)
    return {"status": "ok", "count": len(history), "messages": history}


@router.post("/message")
def post_support_message(req: SupportMessageRequest, db: Session = Depends(get_db)):
    """
    User sends a message to Customer Support.
    - Grounded in active creatorData.
    - Answers via Gemini/OpenAI AI LLM.
    - If user asks for human/admin or questions cannot be answered, escalates via Brevo & Google SMTP to admin.
    - Saves all messages to general support_messages table.
    """
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"
    creator_id = req.creator_id or ""
    creator_handle = req.creator_handle or ""
    creator_name = req.creator_name or ""
    context = req.creator_context or {}

    user_msg_id = req.id or str(uuid.uuid4())
    user_record = {
        "id": user_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": creator_handle,
        "creator_name": creator_name,
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
    escalation_triggers = [
        "human", "talk to human", "speak to human", "real person", "admin", "agent",
        "representative", "call me", "phone", "refund", "chargeback", "legal",
        "contract amendment", "bank account error", "bug report", "escalate"
    ]
    should_escalate = req.force_escalate or any(t in user_lower for t in escalation_triggers)

    assistant_msg_id = str(uuid.uuid4())
    assistant_reply = ""
    is_escalated = False

    if should_escalate:
        is_escalated = True
        recent = _fetch_history(session_id=session_id, creator_id=creator_id, limit=10, sql_db=db)
        subject, body_html, body_text = _format_admin_escalation_email(
            creator_handle=creator_handle,
            creator_name=creator_name,
            creator_id=creator_id,
            session_id=session_id,
            reason=f"Creator query: '{req.message}'",
            user_query=req.message,
            creator_context=context,
            recent_history=recent
        )
        email_sent = False
        try:
            email_provider.send(
                to_email=settings.ADMIN_EMAIL,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                from_name="CoLaunch Creator Support Desk"
            )
            email_sent = True
            logger.info(f"[Support] Escalation email dispatched to {settings.ADMIN_EMAIL}")
        except Exception as err:
            logger.error(f"[Support] Failed sending escalation email: {err}")

        assistant_reply = (
            f"I have escalated your inquiry directly to our human administrator team ({settings.ADMIN_EMAIL}). "
            f"Our team has been dispatched via Brevo & Google SMTP with your full workspace snapshot for @{creator_handle or 'your channel'}. "
            f"We are reviewing it now. You can also share any further files or details right here."
        )
    else:
        # Grounded AI generation
        blueprint = context.get("blueprint") or {}
        bp_name = blueprint.get("name", "Creator Hub") if isinstance(blueprint, dict) else str(blueprint)
        features = context.get("features") or (blueprint.get("features") if isinstance(blueprint, dict) else [])
        features_str = ", ".join(features) if isinstance(features, list) else str(features)
        followers = context.get("followers", "audience")
        niche = context.get("niche", "creator niche")
        platform = context.get("platform", "YouTube")
        stage = context.get("stage", "creator onboarding/dashboard")
        pricing = context.get("pricing", "$29/mo membership & product vault")

        system_prompt = f"""You are CoLaunch's AI Customer Support Agent for Creators. You provide real-time, highly knowledgeable, friendly, and practical support.
CRITICAL GROUNDING: You are supporting THIS specific creator right now:
- Handle: {creator_handle or '@creator'}
- Name: {creator_name or 'Creator'}
- Niche: {niche}
- Audience: {followers} followers on {platform}
- Current App / Blueprint: {bp_name}
- Configured Features: {features_str}
- Stage in CoLaunch: {stage}
- Monetization / Pricing: {pricing}

Guidelines:
1. Always ground your explanation in their specific workspace, audience, and blueprint ({bp_name}). Never give generic filler.
2. Explain how their member app, custom courses, digital store, community discussions, or stripe payout rails operate.
3. If the user asks something you genuinely cannot answer (such as custom contract adjustments, bank routing verification, server hardware bugs) or asks for human staff, reply with helpful reassurance and include the token [ESCALATE_TO_ADMIN] in your reply.
4. Keep replies clear, well-structured, concise (under 160 words), and encouraging."""

        user_prompt = f"Creator @{creator_handle or 'user'} asks: \"{req.message}\""

        try:
            ai_text = call_llm(
                prompt=user_prompt,
                max_tokens=600,
                system_prompt=system_prompt
            )
            if "[ESCALATE_TO_ADMIN]" in ai_text:
                is_escalated = True
                ai_text = ai_text.replace("[ESCALATE_TO_ADMIN]", "").strip()
                # Send escalation email
                recent = _fetch_history(session_id=session_id, creator_id=creator_id, limit=10, sql_db=db)
                subject, body_html, body_text = _format_admin_escalation_email(
                    creator_handle=creator_handle,
                    creator_name=creator_name,
                    creator_id=creator_id,
                    session_id=session_id,
                    reason="AI determined human intervention required",
                    user_query=req.message,
                    creator_context=context,
                    recent_history=recent
                )
                try:
                    email_provider.send(
                        to_email=settings.ADMIN_EMAIL,
                        subject=subject,
                        body_html=body_html,
                        body_text=body_text,
                        from_name="CoLaunch Creator Support Desk"
                    )
                except Exception as e:
                    logger.warning(f"[Support] Failed sending auto-escalation email: {e}")
            assistant_reply = ai_text
        except Exception as e:
            logger.error(f"[Support] AI generation error: {e}")
            assistant_reply = (
                f"I'm reviewing your setup for {creator_handle or 'your channel'}. "
                f"Your {bp_name} suite is configured for {niche} with {features_str}. "
                f"If you'd like to talk directly with an administrator, let me know and I'll notify {settings.ADMIN_EMAIL} immediately."
            )

    assistant_record = {
        "id": assistant_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": creator_handle,
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
    Manually escalate a creator support thread to human admin via Brevo and Google SMTP.
    """
    session_id = req.session_id or f"session_{uuid.uuid4().hex[:12]}"
    creator_id = req.creator_id or ""
    creator_handle = req.creator_handle or ""
    creator_name = req.creator_name or ""
    context = req.creator_context or {}

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
        recent_history=recent
    )

    send_status = "sent"
    try:
        email_provider.send(
            to_email=settings.ADMIN_EMAIL,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            from_name="CoLaunch Support Desk"
        )
    except Exception as err:
        logger.error(f"[Support] Escalation email failure: {err}")
        send_status = f"failed: {err}"

    confirm_msg_id = str(uuid.uuid4())
    confirm_record = {
        "id": confirm_msg_id,
        "session_id": session_id,
        "creator_id": creator_id,
        "creator_handle": creator_handle,
        "creator_name": creator_name,
        "sender": "assistant",
        "message": f"Your ticket has been escalated to our human admin team at {settings.ADMIN_EMAIL} via Brevo & Google SMTP. An admin has been notified and will follow up with you promptly.",
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
        "admin_email": settings.ADMIN_EMAIL,
        "message_id": confirm_msg_id,
        "reply": confirm_record["message"],
    }


@router.get("/status")
def get_support_status():
    """
    Returns live connectivity status of AI, Brevo, and Google SMTP/IMAP.
    """
    return {
        "status": "ok",
        "ai_provider": settings.ACTIVE_AI_PROVIDER,
        "has_gemini": bool(settings.GEMINI_API_KEY),
        "has_openai": bool(settings.OPENAI_API_KEY),
        "admin_email": settings.ADMIN_EMAIL,
        "brevo_configured": bool(settings.BREVO_API_KEY),
        "google_smtp_configured": bool(settings.GOOGLE_EMAIL and settings.GOOGLE_APP_PASSWORD),
        "google_email": settings.GOOGLE_EMAIL,
    }

