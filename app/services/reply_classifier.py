"""
Module 10 — Reply Classification + CRM Pipeline.

Classifies incoming replies and updates CRM stage.
Handles opt-outs immediately (STOP keyword → suppression).
"""
import json
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.outreach import Reply, Thread
from app.services import audit as audit_svc
from app.services.suppression import add_suppression


OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "remove", "opt out", "opt-out", "no thanks", "not interested please remove"}


def _detect_opt_out(body: str) -> bool:
    lower = body.lower().strip()
    return any(kw in lower for kw in OPT_OUT_KEYWORDS)


def _build_classify_prompt(subject: str, body: str) -> str:
    return f"""Classify this email reply from a creator to a partnership outreach.

Subject: {subject}
Body: {body[:1000]}

Return JSON:
{{
  "classification": "<interested|not_interested|question|more_info|out_of_office|bounced|spam|other>",
  "sentiment": "<positive|neutral|negative>",
  "crm_stage": "<new|contacted|qualified|negotiating|closed_won|closed_lost>",
  "summary": "<1-2 sentence summary of what they said>",
  "next_action": "<recommended next step>"
}}

Classification guide:
- interested: explicit positive agreement or confirmation to move forward (e.g. 'let's do it', 'count me in', 'I am interested', 'let's build')
- question: asks questions, wants thoughts/feedback, or inquires about terms/tech/pricing (e.g. 'thoughts?', 'what tech?', 'how much time?')
- more_info: asks for deck, more details, or links before deciding
- not_interested: clear no, decline, or not right now
- out_of_office: auto-reply or vacation response
- bounced: delivery failure notification
- spam: clearly unrelated or malicious

Return ONLY valid JSON."""


def record_reply(
    db: Session,
    thread_id: str,
    from_address: str,
    subject: str,
    body: str,
    received_at: datetime = None,
    actor: str = "system",
) -> Reply:
    thread = db.get(Thread, thread_id)
    if not thread:
        raise ValueError("Thread not found")

    import urllib.parse
    # Decode URL form-encoded strings if spaces became "+" (e.g. "I+will+be+interested+in+Concept+3...")
    if body and "+" in body and ("I+will" in body or "Concept+" in body or "Let's+build" in body or ("+" in body[:50] and " " not in body[:25])):
        try:
            body = urllib.parse.unquote_plus(body)
        except Exception:
            body = body.replace("+", " ")
    elif body and ("%20" in body or "%28" in body or "%29" in body):
        try:
            body = urllib.parse.unquote(body)
        except Exception:
            pass

    if subject and "+" in subject and ("Interested+in" in subject or "Concept+" in subject):
        try:
            subject = urllib.parse.unquote_plus(subject)
        except Exception:
            subject = subject.replace("+", " ")

    # Immediate opt-out handling — no delay, no AI needed
    if _detect_opt_out(body):
        _handle_opt_out(db, thread, from_address, body, actor)

    # Deduplication guard: check if identical reply already exists in this thread
    clean_body = (body or "").strip()
    existing = db.query(Reply).filter(
        Reply.thread_id == thread_id,
        Reply.from_address.ilike((from_address or "").strip()),
        Reply.body == clean_body,
    ).first()
    if existing:
        return existing

    reply = Reply(
        thread_id=thread_id,
        from_address=from_address,
        subject=subject,
        body=body,
        received_at=received_at or datetime.utcnow(),
    )
    db.add(reply)

    # Update thread status
    thread.status = "replied"
    thread.last_activity = datetime.utcnow()

    db.commit()
    db.refresh(reply)

    # Run AI classification
    try:
        classify_reply(db, reply.id, actor=actor)
    except Exception:
        pass  # Classification failure shouldn't block reply recording

    # Autonomous hands-free creator progression in the background (no frontend required)
    try:
        from app.services.autonomous_pipeline import run_autonomous_creator_progression
        run_autonomous_creator_progression(db, reply)
    except Exception as auto_err:
        print(f"[Autonomous Pipeline] Background creator progression notice: {auto_err}")

    audit_svc.log(
        db, action="reply_recorded", entity_type="reply",
        entity_id=reply.id, actor=actor,
        details={"thread_id": thread_id, "from": from_address},
    )
    return reply


def classify_reply(
    db: Session,
    reply_id: str,
    actor: str = "system",
) -> Reply:
    reply = db.get(Reply, reply_id)
    if not reply:
        raise ValueError("Reply not found")

    has_ai = bool(settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY or settings.OPENAI_API_KEY)
    raw = None

    if has_ai:
        try:
            from app.services.llm import call_llm
            prompt = _build_classify_prompt(reply.subject or "", reply.body)
            raw = call_llm(prompt=prompt, max_tokens=500)
        except Exception as e:
            print(f"LLM reply classification failed: {e}")
            raw = None

    # Strict rule-based pre-check for negative / decline / opt-out
    lower = (reply.body or "").lower().strip()
    neg_patterns = [
        "not interested", "am not interested", "i am not interested", "im not interested",
        "i'm not interested", "not interest", "am not interest", "i am not interest", "sorry",
        "no thanks", "no thank you", "uninterested", "not for me",
        "not right now", "decline", "pass on this", "pass", "please remove", "unsubscribe",
        "stop", "dont contact", "don't contact", "not looking"
    ]
    more_info_patterns = [
        "can you tell me more", "more info", "send details", "send more", "send over details",
        "send deck", "pitch deck", "more information", "what are the details",
        "check them out", "check it out", "check these out", "i'll check", "ill check", "i will check",
        "will check", "checking", "take a look", "looking into", "looking over", "reviewing", "will review",
        "let me review", "will look", "give me a few days", "let you know", "get back to you",
        "can we continue", "how do we continue", "what's next", "whats next"
    ]
    question_patterns = [
        "?", "what tech", "what stack", "how does", "how do you", "revenue split", "how it works",
        "how this works", "who owns", "cost", "pricing", "how much", "what are the", "who builds",
        "what is the timeline", "thoughts", "thought", "think", "what do you think", "what are your thoughts",
        "benefit", "what do i get", "in it for me"
    ]
    pos_patterns = [
        "yes", "interested", "would be interested", "i would be interested", "i'm interested", "im interested",
        "i am interested", "interested, let's proceed", "interested let's proceed", "let's proceed", "lets proceed", "proceed",
        "love to", "sounds great", "sounds good", "sounds awesome", "sounds amazing", "looks great", "looks good",
        "sounds interesting",
        "sure", "sure thing", "sure, send it over", "send it over", "send over", "send it", "go ahead",
        "go ahead with the process", "let's go ahead", "lets go ahead", "ahead with the process",
        "let's talk", "lets talk", "let's do it", "lets do it", "let's connect", "lets connect",
        "count me in", "happy to chat", "open to", "schedule a call", "thanks for reaching out",
        "let me know next steps", "ready to move forward", "agreed", "agree", "deal", "i'm in", "im in",
        "i'm down", "im down", "let's build", "lets build", "yes please", "yeah", "yep", "ok", "okay", "start building"
    ]

    is_explicit_negative = any(p in lower for p in neg_patterns)
    is_explicit_question = not is_explicit_negative and any(p in lower for p in question_patterns)
    is_explicit_more_info = not is_explicit_negative and any(p in lower for p in more_info_patterns)
    is_explicit_positive = not is_explicit_negative and any(p in lower for p in pos_patterns)

    if not raw:
        if is_explicit_negative:
            reply.classification = "not_interested"
            reply.sentiment = "negative"
            reply.crm_stage = "closed_lost"
            reply.ai_summary = "Creator declined or expressed no interest."
        elif is_explicit_positive:
            reply.classification = "interested"
            reply.sentiment = "positive"
            reply.crm_stage = "qualified"
            reply.ai_summary = "Creator expressed positive interest."
        elif is_explicit_question:
            reply.classification = "question"
            reply.sentiment = "neutral"
            reply.crm_stage = "contacted"
            reply.ai_summary = "Creator asked clarifying questions regarding terms or tech."
        elif is_explicit_more_info:
            reply.classification = "more_info"
            reply.sentiment = "positive"
            reply.crm_stage = "contacted"
            reply.ai_summary = "Creator is reviewing software concepts / requesting more information."
        else:
            reply.classification = "other"
            reply.sentiment = "neutral"
            reply.crm_stage = "contacted"
            reply.ai_summary = "Reply received — active dialog in progress."
        reply.processed_at = datetime.utcnow()
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group()) if match else {}

        parsed_cls = data.get("classification", "other")
        # Guardrails: override classification if explicit sentiment keywords match
        if is_explicit_negative and parsed_cls == "interested":
            parsed_cls = "not_interested"
            data["sentiment"] = "negative"
            data["crm_stage"] = "closed_lost"
        elif is_explicit_positive and parsed_cls != "not_interested":
            parsed_cls = "interested"
            data["sentiment"] = "positive"
            data["crm_stage"] = "qualified"
            if not data.get("summary"):
                data["summary"] = "Creator expressed positive interest."

        reply.classification = parsed_cls
        reply.sentiment = data.get("sentiment", "neutral")
        reply.crm_stage = data.get("crm_stage", "contacted")
        reply.ai_summary = data.get("summary", "")
        reply.processed_at = datetime.utcnow()

    # Update thread CRM stage & Creator model
    thread = db.get(Thread, reply.thread_id)
    if thread:
        if reply.classification == "interested":
            thread.status = "replied"
        elif reply.classification == "not_interested":
            thread.status = "closed"

        if thread.creator_id:
            from app.models.creator import Creator
            creator = db.get(Creator, thread.creator_id)
            if creator:
                if reply.classification == "interested":
                    creator.status = "in_review"
                elif reply.classification == "not_interested":
                    creator.status = "rejected"
                existing_notes = {}
                if creator.discovery_notes:
                    try:
                        existing_notes = json.loads(creator.discovery_notes)
                    except:
                        existing_notes = {"raw": creator.discovery_notes}
                existing_notes["reply_classification"] = reply.classification
                existing_notes["reply_text"] = reply.body
                existing_notes["reply_subject"] = reply.subject
                creator.discovery_notes = json.dumps(existing_notes)

                # Sync to MongoDB Atlas creators collection (source of truth for list_creators)
                try:
                    from app.mongodb import get_collection
                    from bson import ObjectId
                    coll = get_collection("creators")
                    if coll is not None:
                        c_id_str = str(creator.id).strip()
                        c_handle_str = (creator.handle or "").lstrip("@").strip()
                        or_filters = []
                        if c_id_str:
                            or_filters.append({"id": c_id_str})
                            if ObjectId.is_valid(c_id_str):
                                or_filters.append({"_id": ObjectId(c_id_str)})
                        if c_handle_str:
                            or_filters.append({"handle": c_handle_str})
                            or_filters.append({"handle": f"@{c_handle_str}"})
                        if or_filters:
                            mongo_status = "in_review" if reply.classification == "interested" else ("rejected" if reply.classification == "not_interested" else "replied")
                            existing_doc = coll.find_one({"$or": or_filters})
                            m_notes = {}
                            if existing_doc and existing_doc.get("discovery_notes"):
                                try:
                                    m_notes = json.loads(existing_doc["discovery_notes"]) if isinstance(existing_doc["discovery_notes"], str) else existing_doc["discovery_notes"]
                                except Exception:
                                    m_notes = {}
                            m_notes["reply_classification"] = reply.classification
                            m_notes["reply_text"] = reply.body
                            m_notes["reply_subject"] = reply.subject

                            coll.update_many(
                                {"$or": or_filters},
                                {
                                    "$set": {
                                        "reply_classification": reply.classification,
                                        "reply_text": reply.body,
                                        "reply_subject": reply.subject,
                                        "status": mongo_status,
                                        "has_replied": True,
                                        "discovery_notes": json.dumps(m_notes),
                                        "updated_at": datetime.utcnow().isoformat(),
                                    }
                                }
                            )
                except Exception as mongo_err:
                    print(f"[Reply Classifier] MongoDB sync notice: {mongo_err}")

    db.commit()
    return reply


def _handle_opt_out(db: Session, thread: Thread, from_address: str, body: str, actor: str):
    """Immediately suppresses contact on opt-out reply."""
    add_suppression(
        db, reason="opt_out",
        email=from_address,
        creator_id=thread.creator_id,
        suppressed_by="auto_opt_out",
        notes=f"Opt-out detected in reply body: {body[:100]}",
        actor=actor,
    )
    thread.status = "closed"
    db.commit()
    audit_svc.log(
        db, action="opt_out_processed", entity_type="thread",
        entity_id=thread.id, actor=actor,
        details={"from": from_address},
    )


def get_crm_pipeline(db: Session) -> dict:
    """Returns counts by CRM stage for pipeline view."""
    from sqlalchemy import func
    rows = (
        db.query(Reply.crm_stage, func.count(Reply.id))
        .group_by(Reply.crm_stage)
        .all()
    )
    return {stage: count for stage, count in rows}
