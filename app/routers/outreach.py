from typing import Optional, List, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.outreach import OutreachMessage, Thread, FollowUp, Reply
from app.services.outreach_generator import (
    generate_outreach_draft, submit_for_review, update_draft
)
from app.services.review_queue import (
    review_outreach_message, list_message_review_queue,
    review_follow_up, list_followup_review_queue,
)
from app.services.send_queue import queue_message, send_message, handle_bounce, list_send_queue
from app.services.followup import generate_followup, send_approved_followup, can_follow_up
from app.services.reply_classifier import record_reply, classify_reply
from app.services.concept_image_generator import generate_concept_card_image

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


@router.get("/concept-card-image")
def render_dynamic_concept_card(
    name: str = "ActionLoop",
    app_url: str = "actionloop.app",
    mrr: str = "",
    active_users: str = "",
    retention: str = "",
    customer: str = "Creators & Operators",
    pricing: str = "$49/mo Starter • $99/mo Pro",
    brand_color: Optional[str] = None,
    mockup_type: str = "saas_os",
    tagline: Optional[str] = None,
    features: Optional[str] = None,
    variant: Optional[int] = None,
):
    """
    Renders dynamic concept text into an authentic dark-mode concept card PNG
    matching the ConceptLandingPage design and the creator's chosen brand color.
    """
    feature_list = [f.strip() for f in (features or "").split("|") if f.strip()]
    concept_data = {
        "name": name,
        "appUrl": app_url,
        "tagline": tagline or "",
        "features": feature_list,
        "primaryMetric": mrr,
        "activeMetric": active_users,
        "efficiencyMetric": retention,
        "customer": customer,
        "pricing": pricing,
        "brandColor": brand_color,
        "mockupType": mockup_type,
        "variant": variant,
    }
    png_bytes = generate_concept_card_image(concept_data)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"}
    )


class DraftCreate(BaseModel):
    creator_id: str
    campaign_id: str
    contact_id: str
    product_recommendation_id: str
    deck_id: Optional[str] = None
    send_method: str = "email"
    tone: str = "professional_friendly"


class DraftUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


class ReviewDecision(BaseModel):
    decision: str          # approved | rejected | needs_changes
    reviewer: str
    notes: Optional[str] = None


class ReplyRecord(BaseModel):
    thread_id: str
    from_address: str
    subject: Optional[str] = ""
    body: str


# ── Draft Management ─────────────────────────────────────────────────────────

@router.post("/drafts")
def create_draft(body: DraftCreate, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        msg = generate_outreach_draft(db, actor=actor, **body.model_dump())
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/drafts/{message_id}")
def edit_draft(message_id: str, body: DraftUpdate, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        msg = update_draft(db, message_id, body.subject, body.body, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/drafts/{message_id}/submit")
def submit_draft(message_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        msg = submit_for_review(db, message_id, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Review Queue ─────────────────────────────────────────────────────────────

@router.get("/review-queue")
def message_review_queue(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    msgs = list_message_review_queue(db, skip, limit)
    return [_msg_dict(m) for m in msgs]


@router.post("/review-queue/{message_id}/review")
def review_message(message_id: str, body: ReviewDecision, db: Session = Depends(get_db)):
    try:
        msg = review_outreach_message(db, message_id, body.decision, body.reviewer, body.notes)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Send Queue ───────────────────────────────────────────────────────────────

@router.get("/send-queue")
def send_queue(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    msgs = list_send_queue(db, skip, limit)
    return [_msg_dict(m) for m in msgs]


@router.post("/{message_id}/queue")
def queue_for_send(message_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        msg = queue_message(db, message_id, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{message_id}/send")
def send(message_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        msg = db.get(OutreachMessage, message_id)
        if not msg:
            raise HTTPException(404, "Message not found")
        if msg.status in ("approved", "failed", "queued", "review_pending", "draft"):
            queue_message(db, message_id, actor)
        msg = send_message(db, message_id, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/{message_id}/bounce")
def mark_bounce(message_id: str, actor: str = "system", db: Session = Depends(get_db)):
    try:
        msg = handle_bounce(db, message_id, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Follow-ups ───────────────────────────────────────────────────────────────

@router.get("/threads/{thread_id}/followup-eligibility")
def check_followup(thread_id: str, db: Session = Depends(get_db)):
    can, reason = can_follow_up(db, thread_id)
    return {"can_follow_up": can, "reason": reason}


@router.post("/threads/{thread_id}/followup")
def create_followup(thread_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        fu = generate_followup(db, thread_id, actor)
        return _followup_dict(fu)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/followup-review-queue")
def followup_review_queue(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    fus = list_followup_review_queue(db, skip, limit)
    return [_followup_dict(f) for f in fus]


@router.post("/followups/{follow_up_id}/review")
def review_fu(follow_up_id: str, body: ReviewDecision, db: Session = Depends(get_db)):
    try:
        fu = review_follow_up(db, follow_up_id, body.decision, body.reviewer, body.notes)
        return _followup_dict(fu)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/followups/{follow_up_id}/send")
def send_followup(follow_up_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        fu = send_approved_followup(db, follow_up_id, actor)
        return _followup_dict(fu)
    except (ValueError, Exception) as e:
        raise HTTPException(400, str(e))


# ── Reply Inbox ──────────────────────────────────────────────────────────────

@router.post("/replies")
def record_incoming_reply(body: ReplyRecord, actor: str = "system", db: Session = Depends(get_db)):
    try:
        reply = record_reply(db, body.thread_id, body.from_address, body.subject, body.body, actor=actor)
        return _reply_dict(reply)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/replies")
def list_replies(classification: Optional[str] = None, skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(Reply)
    if classification:
        q = q.filter(Reply.classification == classification)
    replies = q.order_by(Reply.received_at.desc()).offset(skip).limit(limit).all()
    return [_reply_dict(r) for r in replies]


@router.post("/replies/{reply_id}/classify")
def reclassify(reply_id: str, actor: str = "internal", db: Session = Depends(get_db)):
    try:
        reply = classify_reply(db, reply_id, actor)
        return _reply_dict(reply)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── General message list (and ops-dashboard aliases) ────────────────────────

@router.get("")
@router.get("/messages")
def list_messages(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """List outreach messages. Called by ops dashboard as GET /outreach?status=review_pending"""
    q = db.query(OutreachMessage)
    if status:
        if "," in status:
            q = q.filter(OutreachMessage.status.in_(status.split(",")))
        else:
            q = q.filter(OutreachMessage.status == status)
    msgs = q.order_by(OutreachMessage.created_at.desc()).offset(skip).limit(limit).all()
    return [_msg_dict(m) for m in msgs]


class ApproveBody(BaseModel):
    reviewer: str = "ops_dashboard"
    notes: Optional[str] = None


@router.post("/{message_id}/approve")
def approve_message(message_id: str, body: ApproveBody = ApproveBody(), db: Session = Depends(get_db)):
    """Approve an outreach draft — ops dashboard shortcut."""
    try:
        msg = review_outreach_message(db, message_id, "approved", body.reviewer, body.notes)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{message_id}/reject")
def reject_message(message_id: str, body: ApproveBody = ApproveBody(), db: Session = Depends(get_db)):
    """Reject an outreach draft — ops dashboard shortcut."""
    try:
        msg = review_outreach_message(db, message_id, "rejected", body.reviewer, body.notes)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


class PatchDraft(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


@router.patch("/{message_id}")
def patch_message(message_id: str, body: PatchDraft, actor: str = "ops_dashboard", db: Session = Depends(get_db)):
    """Edit a draft's subject/body inline — ops dashboard shortcut."""
    try:
        msg = update_draft(db, message_id, body.subject, body.body, actor)
        return _msg_dict(msg)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{message_id}")
@router.delete("/messages/{message_id}")
def delete_message(message_id: str, db: Session = Depends(get_db)):
    """Delete an outreach message and its associated thread if any."""
    msg = db.get(OutreachMessage, message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    
    # Delete associated thread & replies if any
    threads = db.query(Thread).filter(Thread.outreach_message_id == message_id).all()
    for t in threads:
        db.query(Reply).filter(Reply.thread_id == t.id).delete(synchronize_session=False)
        db.query(FollowUp).filter(FollowUp.thread_id == t.id).delete(synchronize_session=False)
        db.delete(t)
        
    db.delete(msg)
    db.commit()
    return {"status": "deleted", "id": message_id}


# ── Threads ──────────────────────────────────────────────────────────────────

@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, db: Session = Depends(get_db)):
    """Delete a thread and its replies."""
    thread = db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")

    db.query(Reply).filter(Reply.thread_id == thread_id).delete(synchronize_session=False)
    db.query(FollowUp).filter(FollowUp.thread_id == thread_id).delete(synchronize_session=False)
    db.delete(thread)
    db.commit()
    return {"status": "deleted", "id": thread_id}


@router.delete("/replies/{reply_id}")
def delete_reply_endpoint(reply_id: str, db: Session = Depends(get_db)):
    """Delete an individual reply message."""
    reply = db.get(Reply, reply_id)
    if not reply:
        raise HTTPException(404, "Reply not found")
    db.delete(reply)
    db.commit()
    return {"status": "deleted", "id": reply_id}


@router.get("/threads")
def list_threads(status: Optional[str] = None, skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(Thread)
    if status:
        q = q.filter(Thread.status == status)
    threads = q.order_by(Thread.last_activity.desc()).offset(skip).limit(limit).all()
    return [_thread_dict(t) for t in threads]


@router.post("/poll-inbox")
def trigger_inbox_poll(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger an immediate IMAP fetch and return updated threads immediately."""
    from app.services.inbox_poller import poll_inbox_sync
    result = {}
    try:
        # Run sync with up to 5s wait if another poller is active
        result = poll_inbox_sync(wait_timeout=5.0)
    except Exception as e:
        logger.warning(f"Failed to run IMAP sync in poll-inbox: {e}")
        result = {"status": "error", "error": str(e), "new_replies": 0}
        
    try:
        threads = db.query(Thread).order_by(Thread.last_activity.desc()).all()
        return {
            "status": "success",
            "message": f"IMAP inbox polled: {result.get('new_replies', 0)} new replies detected",
            "new_replies": result.get("new_replies", 0),
            "threads": [_thread_dict(t) for t in threads]
        }
    except Exception as e:
        return {
            "status": "partial",
            "message": f"Polled with error: {str(e)}",
            "threads": []
        }


from app.services.email_template import format_luxury_html_email, convert_markdown_to_clean_html


class DirectEmailRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    creator_id: Optional[str] = None
    concept_image_url: Optional[str] = None
    concepts: Optional[List[dict]] = None
    is_rejection: Optional[bool] = None
    include_concepts: Optional[bool] = None



@router.post("/send-direct")
def send_direct_email(payload: DirectEmailRequest, db: Session = Depends(get_db)):
    """Directly dispatch an email via Google SMTP and record thread/message in DB."""
    from app.integrations.email_provider import email_provider
    from app.models.creator import Creator, Contact
    from app.models.outreach import Thread, OutreachMessage
    from app.services.autonomous_outreach import is_real_valid_email
    import uuid

    to_email = payload.to_email.strip()
    if not is_real_valid_email(to_email):
        raise HTTPException(400, f"Invalid recipient email address: '{to_email}'")

    creator = None
    if payload.creator_id:
        creator = db.get(Creator, payload.creator_id)
    if not creator and payload.subject:
        import re
        h_match = re.search(r"\[#([a-zA-Z0-9_.\-]+)\]", payload.subject)
        if h_match:
            c_handle_cand = h_match.group(1).strip()
            creator = db.query(Creator).filter(Creator.handle.ilike(f"%{c_handle_cand}%")).first()
    # DO NOT match by email fallback across creators — test emails are often reused for multiple creators
    if not creator:
        import re
        h_match = re.search(r"\[#([a-zA-Z0-9_.\-]+)\]", payload.subject)
        cand_h = h_match.group(1).strip() if h_match else to_email.split("@")[0].lower()[:30]
        creator = Creator(
            id=payload.creator_id if payload.creator_id else str(uuid.uuid4()),
            handle=cand_h,
            platform="youtube",
            display_name=cand_h.capitalize(),
            email_public=to_email,
            status="contacted"
        )
        db.add(creator)
        db.commit()
        db.refresh(creator)

    subject_to_send = payload.subject
    body_text = payload.body
    tracking_token = ""

    # Embed creator tracking token in subject and body for 100% reliable reply attribution
    if creator:
        c_handle = (creator.handle or "").lstrip("@").strip()
        c_id = str(creator.id).strip()
        tracking_token = f"[CF-STAGE:STEP3_INQUIRY | CF-CID:{c_id} | Handle:@{c_handle}]"
        
        # Ensure handle or token is in subject if not already present
        if f"[#{c_handle}]" not in subject_to_send and f"CF-CID" not in subject_to_send:
            if c_handle:
                subject_to_send = f"{subject_to_send} [#{c_handle}]"
            else:
                subject_to_send = f"{subject_to_send} [CF:{c_id[:8]}]"

        # Clean any accidental "undefined content" or visible Ref tracking strings in body_text
        import re
        body_text = re.sub(r'(?:---\s*)?Ref:\s*\[[^\]]+\]', '', body_text, flags=re.IGNORECASE).strip()
        body_text = re.sub(r'---\s*$', '', body_text).strip()
        body_text = re.sub(r'\bundefined\s+content\b', 'content', body_text, flags=re.IGNORECASE)
        body_text = re.sub(r'\bundefined\b', 'channel', body_text, flags=re.IGNORECASE)

        # Update email_public and status to contacted
        creator.email_public = to_email
        if creator.status in ("discovered", "new", None):
            creator.status = "contacted"

        # Record studio reply activity so CRM knows admin has replied
        try:
            import json
            notes = json.loads(creator.discovery_notes) if (creator.discovery_notes and creator.discovery_notes.startswith("{")) else {}
            notes["has_studio_replied"] = True
            notes["last_studio_reply_at"] = datetime.utcnow().isoformat()
            creator.discovery_notes = json.dumps(notes)
        except Exception:
            pass
        db.commit()

    # Check if this email is a rejection or opt-out notice
    all_subject_body = f"{subject_to_send} {body_text}".lower()
    is_rejection_email = (
        payload.is_rejection is True
        or payload.include_concepts is False
        or (payload.concepts is not None and len(payload.concepts) == 0)
        or "reject" in all_subject_body
        or "partnership update" in all_subject_body
        or "regarding creator forge partnership" in all_subject_body
        or "will not be proceeding" in all_subject_body
        or "won't be able to move forward" in all_subject_body
        or "prioritizing specific software verticals" in all_subject_body
        or (creator and getattr(creator, "status", None) == "rejected")
    )

    # Format beautiful luxury HTML template
    resolved_concepts = payload.concepts
    resolved_concept_image = payload.concept_image_url

    if is_rejection_email:
        # Strictly NEVER attach concept lists to a rejection notice
        resolved_concepts = None
        resolved_concept_image = None
    elif not resolved_concepts and creator:
        # Only pull concepts from notes if explicitly requested or if body/subject discusses concepts
        is_pitch_outreach = bool(re.search(r"concept 1|concept 2|concept 3|blueprint|opportunity deck|software concepts|pitch", all_subject_body))
        if payload.include_concepts is True or is_pitch_outreach:
            notes_str = getattr(creator, "discovery_notes", None) or getattr(creator, "niche_data", None)
            if notes_str:
                try:
                    nd = json.loads(notes_str) if isinstance(notes_str, str) else notes_str
                    resolved_concepts = nd.get("product_concepts") or nd.get("concepts")
                except Exception:
                    pass

    if not is_rejection_email and not resolved_concept_image and resolved_concepts and len(resolved_concepts) > 0:
        first_c = resolved_concepts[0]
        if isinstance(first_c, dict):
            mockup = first_c.get("mockup") if isinstance(first_c.get("mockup"), dict) else {}
            resolved_concept_image = (
                first_c.get("customImageUrl")
                or first_c.get("imageUrl")
                or first_c.get("image_url")
                or first_c.get("mockup_url")
                or mockup.get("imageUrl")
                or mockup.get("previewUrl")
            )


    body_html = format_luxury_html_email(
        body_text=payload.body,
        subject=subject_to_send,
        creator_name=creator.display_name if creator else "",
        tracking_token=tracking_token,
        concept_image_url=resolved_concept_image,
        concepts=resolved_concepts
    )

    # 1. Send via Google SMTP
    try:
        res = email_provider.send(
            to_email=to_email,
            subject=subject_to_send,
            body_html=body_html,
            body_text=body_text
        )
    except Exception as e:
        raise HTTPException(500, f"SMTP delivery failed: {str(e)}")

    # 2. Record OutreachMessage & Thread in database
    contact = None
    if creator:
        contact = db.query(Contact).filter(Contact.creator_id == creator.id, Contact.contact_type == "email").first()
        if not contact:
            contact = Contact(creator_id=creator.id, contact_type="email", value=to_email, source="outreach_dispatch")
            db.add(contact)
            db.commit()
            db.refresh(contact)

    msg = OutreachMessage(
        creator_id=creator.id if creator else None,
        campaign_id="default",
        contact_id=contact.id if contact else None,
        subject=subject_to_send,
        body=body_text,
        send_method="email",
        status="sent",
        sent_at=datetime.utcnow()
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # Link to existing thread or initialize new thread
    thread = db.query(Thread).filter(Thread.creator_id == creator.id).order_by(Thread.created_at.desc()).first() if creator else None
    if thread:
        from app.config import settings
        from app.models.outreach import Reply
        admin_from = settings.GOOGLE_EMAIL or settings.FROM_EMAIL or "partnerships@creatorforge.com"
        outgoing_reply = Reply(
            thread_id=thread.id,
            from_address=admin_from,
            subject=subject_to_send,
            body=payload.body,
            classification="other",
            ai_summary="Outgoing reply from you"
        )
        db.add(outgoing_reply)
        thread.last_activity = datetime.utcnow()
        db.commit()
    else:
        thread = Thread(
            creator_id=creator.id if creator else None,
            outreach_message_id=msg.id,
            status="open",
            created_at=datetime.utcnow(),
            last_activity=datetime.utcnow()
        )
        db.add(thread)
        db.commit()
        db.refresh(thread)

    return {
        "status": "sent",
        "message_id": res.get("message_id", str(uuid.uuid4())),
        "thread_id": thread.id,
        "recipient": to_email
    }


class ConceptSelectionRequest(BaseModel):
    creator_id: Optional[str] = None
    creator_slug: Optional[str] = None
    creator_name: Optional[str] = None
    creator_email: Optional[str] = None
    concept_id: Optional[str] = None
    concept_name: str
    concept_index: int = 0
    concept: Optional[Dict[str, Any]] = None
    notes: Optional[str] = ""


@router.post("/concept-selection")
def submit_concept_selection(payload: ConceptSelectionRequest, db: Session = Depends(get_db)):
    """
    Public endpoint triggered when a creator clicks 'Greenlight / Select Concept' on their
    shared live landing page (/preorder/:slug?concept=...).
    Persists their concept choice on the creator record, inserts an authentic inbound Step 6
    Reply into their thread so the Step 6 AI immediately locks onto the selected concept,
    and dispatches a notification email to CoLaunch Studio.
    """
    import json
    import re
    import uuid
    from app.config import settings
    from app.models.creator import Creator
    from app.integrations.email_provider import email_provider
    from app.services.email_template import format_luxury_html_email
    from app.services.autonomous_outreach import is_real_valid_email

    def _alnum(val: Optional[str]) -> str:
        return re.sub(r"[^a-z0-9]", "", (val or "").lower())

    clean_slug = _alnum(payload.creator_slug or payload.creator_name or payload.creator_id or "creator") or "creator"
    concept_name = (payload.concept_name or f"Concept {payload.concept_index + 1}").strip()
    concept_dict = dict(payload.concept or {})
    if payload.concept_id and not concept_dict.get("id"):
        concept_dict["id"] = payload.concept_id
    if not concept_dict.get("name"):
        concept_dict["name"] = concept_name
    concept_dict["selectedViaLandingPage"] = True
    concept_dict["selectedAt"] = datetime.utcnow().isoformat()

    # 1. Resolve creator in MongoDB & SQLAlchemy
    matched_mongo = None
    try:
        from app.mongodb import get_collection
        coll = get_collection("creators")
        if coll is not None:
            if payload.creator_id:
                matched_mongo = coll.find_one({"$or": [{"_id": payload.creator_id}, {"id": payload.creator_id}]})
            if not matched_mongo and clean_slug:
                for doc in coll.find({}):
                    c_h = _alnum(doc.get("handle"))
                    c_n = _alnum(doc.get("display_name") or doc.get("name"))
                    c_id = _alnum(str(doc.get("id") or doc.get("_id") or ""))
                    if clean_slug in (c_h, c_n, c_id):
                        matched_mongo = doc
                        break
    except Exception:
        matched_mongo = None

    creator_sql = None
    if payload.creator_id:
        creator_sql = db.get(Creator, payload.creator_id)
    if not creator_sql and matched_mongo:
        mid = str(matched_mongo.get("id") or matched_mongo.get("_id") or "")
        if mid:
            creator_sql = db.get(Creator, mid)
    if not creator_sql and clean_slug:
        for cand in db.query(Creator).all():
            if clean_slug in (_alnum(cand.handle), _alnum(cand.display_name), _alnum(cand.id)):
                creator_sql = cand
                break

    resolved_creator_id = (
        (str(matched_mongo.get("id") or matched_mongo.get("_id")) if matched_mongo else None)
        or (creator_sql.id if creator_sql else None)
        or payload.creator_id
    )
    resolved_creator_name = (
        (matched_mongo.get("display_name") or matched_mongo.get("name") or matched_mongo.get("handle") if matched_mongo else None)
        or (creator_sql.display_name or creator_sql.handle if creator_sql else None)
        or payload.creator_name
        or payload.creator_slug
        or "Creator Partner"
    )
    resolved_creator_email = (
        (payload.creator_email or "").strip()
        or ((matched_mongo.get("email_public") or matched_mongo.get("email") or "").strip() if matched_mongo else "")
        or ((creator_sql.email_public or "").strip() if creator_sql else "")
    )

    notes_text = (payload.notes or "").strip()
    selection_body = (
        f"Hi CoLaunch team,\n\n"
        f"I reviewed the interactive landing pages and I choose Concept {payload.concept_index + 1}: {concept_name}.\n\n"
        f"- Selected Concept: {concept_name} (Concept {payload.concept_index + 1})\n"
        f"- Pricing: {concept_dict.get('pricing') or '$29/mo Starter • $79/mo Pro'}\n"
        + (f"- Creator Notes / Tweaks: {notes_text}\n" if notes_text else "")
        + f"\nI am ready to move forward and start building!"
        + f"\n\n---\nRef: [CF-STAGE:STEP6_PITCH | CF-CID:{resolved_creator_id or clean_slug}]"
    )
    selection_subject = (
        f"Re: 3 bespoke software concepts & landing pages — Concept {payload.concept_index + 1}: {concept_name} Selected "
        f"[CF-STAGE:STEP6_PITCH | CF-CID:{resolved_creator_id or clean_slug}]"
    )

    # Resolve concrete concept_id from creator's saved concepts if possible
    resolved_concept_id = payload.concept_id or concept_dict.get("id")
    saved_concepts = []
    if matched_mongo:
        saved_concepts = matched_mongo.get("productConcepts") or matched_mongo.get("product_concepts") or []
    for idx_c, sc in enumerate(saved_concepts):
        if isinstance(sc, dict):
            if _alnum(sc.get("name")) == _alnum(concept_name) or (resolved_concept_id and sc.get("id") == resolved_concept_id):
                resolved_concept_id = sc.get("id") or resolved_concept_id or f"concept-{idx_c + 1}"
                concept_dict = {**sc, **concept_dict, "id": resolved_concept_id}
                break
    if not resolved_concept_id:
        resolved_concept_id = f"concept-{payload.concept_index + 1}"
    concept_dict["id"] = resolved_concept_id

    # Persist into Mongo creator document
    if matched_mongo is not None:
        try:
            from app.mongodb import get_collection
            coll = get_collection("creators")
            existing_notes = {}
            if matched_mongo.get("discovery_notes") and str(matched_mongo["discovery_notes"]).startswith("{"):
                try:
                    existing_notes = json.loads(matched_mongo["discovery_notes"])
                except Exception:
                    existing_notes = {}
            existing_notes["selected_concept_id"] = resolved_concept_id
            existing_notes["selected_concept"] = concept_dict
            existing_notes["selected_concept_index"] = payload.concept_index
            existing_notes["concept_selection_notes"] = notes_text
            existing_notes["reply_classification"] = "interested"
            existing_notes["reply_text"] = selection_body
            if resolved_creator_email and not matched_mongo.get("email_public"):
                matched_mongo["email_public"] = resolved_creator_email
            matched_mongo["status"] = "replied"
            matched_mongo["selectedConceptId"] = resolved_concept_id
            matched_mongo["selectedConcept"] = concept_dict
            matched_mongo["discovery_notes"] = json.dumps(existing_notes)
            matched_mongo["updated_at"] = datetime.utcnow().isoformat()
            if coll is not None:
                coll.replace_one({"_id": matched_mongo["_id"]}, matched_mongo)
        except Exception:
            pass

    # Persist into SQLAlchemy Creator & Thread + inbound Reply
    if creator_sql is not None:
        try:
            sql_notes = {}
            if creator_sql.discovery_notes and str(creator_sql.discovery_notes).startswith("{"):
                try:
                    sql_notes = json.loads(creator_sql.discovery_notes)
                except Exception:
                    sql_notes = {}
            sql_notes["selected_concept_id"] = resolved_concept_id
            sql_notes["selected_concept"] = concept_dict
            sql_notes["reply_classification"] = "interested"
            sql_notes["reply_text"] = selection_body
            creator_sql.discovery_notes = json.dumps(sql_notes)
            creator_sql.status = "replied"
            if resolved_creator_email and not creator_sql.email_public:
                creator_sql.email_public = resolved_creator_email
            db.commit()
        except Exception:
            db.rollback()

    admin_emails = {
        (settings.GOOGLE_EMAIL or "").lower().strip(),
        (settings.FROM_EMAIL or "").lower().strip(),
        "creatorforgeweb@gmail.com",
        "creatorforgestudio@gmail.com",
        "partnerships@creatorforge.com",
    }
    sender_addr = resolved_creator_email if (is_real_valid_email(resolved_creator_email) and resolved_creator_email.lower() not in admin_emails) else f"{clean_slug}@creator-selection.co-launch.app"

    thread = None
    if resolved_creator_id:
        thread = db.query(Thread).filter(Thread.creator_id == resolved_creator_id).order_by(Thread.last_activity.desc()).first()
    if not thread:
        out_msg = OutreachMessage(
            creator_id=resolved_creator_id,
            campaign_id="default",
            subject=f"3 bespoke software concepts & landing pages for @{clean_slug}",
            body="Step 6 Concept Showcase Pitch",
            send_method="email",
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(out_msg)
        db.commit()
        db.refresh(out_msg)
        thread = Thread(
            creator_id=resolved_creator_id,
            outreach_message_id=out_msg.id,
            status="open",
            created_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
        )
        db.add(thread)
        db.commit()
        db.refresh(thread)

    inbound_reply = Reply(
        thread_id=thread.id,
        from_address=sender_addr,
        subject=selection_subject,
        body=selection_body,
        classification="interested",
        sentiment="positive",
        ai_summary=f"Creator selected Concept {payload.concept_index + 1}: {concept_name} via live landing page",
        crm_stage="step6_concept_selected",
        received_at=datetime.utcnow(),
    )
    db.add(inbound_reply)
    thread.last_activity = datetime.utcnow()
    thread.status = "open"
    db.commit()
    db.refresh(inbound_reply)

    # 2. Dispatch real email to CoLaunch Studio inbox (and creator receipt if valid email exists)
    studio_email = (settings.GOOGLE_EMAIL or settings.FROM_EMAIL or "creatorforgeweb@gmail.com").strip()
    email_sent = False
    email_error = None
    try:
        notif_subject = f"[Concept Selected] {resolved_creator_name} chose Concept {payload.concept_index + 1}: {concept_name}"
        notif_html = format_luxury_html_email(
            subject=notif_subject,
            body_text=(
                f"{resolved_creator_name} clicked their live concept landing page and greenlit:\n\n"
                f"**Concept {payload.concept_index + 1}: {concept_name}**\n"
                f"• Creator Email: {resolved_creator_email or 'Not provided'}\n"
                f"• Pricing: {concept_dict.get('pricing') or '$29/mo Starter'}\n"
                + (f"• Creator Notes: {notes_text}\n" if notes_text else "")
                + f"\nOpen Step 6 in CoLaunch to review the selection and click Create Project."
            ),
            creator_name=resolved_creator_name,
        )
        email_provider.send(
            to_email=studio_email,
            subject=notif_subject,
            body_html=notif_html,
            body_text=selection_body,
        )
        email_sent = True
    except Exception as e:
        email_error = str(e)

    return {
        "status": "selected",
        "creator_id": resolved_creator_id,
        "selected_concept_id": resolved_concept_id,
        "selected_concept": concept_dict,
        "thread_id": thread.id,
        "reply_id": inbound_reply.id,
        "email_sent": email_sent,
        "email_recipient": studio_email,
        "email_error": email_error,
    }


class SendReplyRequest(BaseModel):
    body: str
    to_email: Optional[str] = None
    concept_image_url: Optional[str] = None
    concepts: Optional[List[Dict[str, Any]]] = None

@router.post("/threads/{thread_id}/reply")
def send_thread_reply(thread_id: str, payload: SendReplyRequest, actor: str = "ops_dashboard", db: Session = Depends(get_db)):
    from app.integrations.email_provider import email_provider
    from app.config import settings
    from app.services.autonomous_outreach import is_real_valid_email
    from app.models.creator import Contact
    
    thread = db.get(Thread, thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
        
    admin_email = (settings.GOOGLE_EMAIL or settings.FROM_EMAIL or "").lower().strip()
    to_email = None
    original_subject = ""

    # 0. Override from payload if user provided a recipient email explicitly
    if payload.to_email and is_real_valid_email(payload.to_email):
        to_email = payload.to_email.strip()

    # 1. Prefer incoming replies from creator (not admin)
    if not to_email and thread.replies:
        for r in reversed(thread.replies):
            if r.from_address and r.from_address.lower().strip() != admin_email and is_real_valid_email(r.from_address):
                to_email = r.from_address.strip()
                original_subject = r.subject or ""
                break

    # 2. Fallback to thread's creator public_email
    if not to_email and thread.creator and is_real_valid_email(thread.creator.email_public):
        to_email = thread.creator.email_public.strip()

    # 3. Fallback to thread's outreach message contact
    if not to_email and thread.outreach_message:
        if thread.outreach_message.contact and is_real_valid_email(thread.outreach_message.contact.value):
            to_email = thread.outreach_message.contact.value.strip()
        if not original_subject:
            original_subject = thread.outreach_message.subject or ""

    # 4. Fallback to any Contact associated with creator
    if not to_email and thread.creator_id:
        contact = db.query(Contact).filter(Contact.creator_id == thread.creator_id, Contact.contact_type == "email").first()
        if contact and is_real_valid_email(contact.value):
            to_email = contact.value.strip()
        
    if not to_email or not is_real_valid_email(to_email):
        raise HTTPException(400, "Could not determine recipient email address for this thread. Please specify a valid recipient email address.")

    # Save to creator's public_email if creator email was missing/not set
    if thread.creator:
        if not thread.creator.email_public:
            thread.creator.email_public = to_email
        contact = db.query(Contact).filter(Contact.creator_id == thread.creator_id, Contact.contact_type == "email").first()
        if not contact:
            contact = Contact(creator_id=thread.creator_id, contact_type="email", value=to_email, source="reply_form")
            db.add(contact)
        db.commit()
        
    # Format subject (Re: )
    subject = original_subject or "Re: Co-founder partnership inquiry"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
        
    # Resolve concept mockup preview for replies
    reply_concept_image = payload.concept_image_url
    reply_concepts = payload.concepts

    all_reply_text = f"{subject} {payload.body}".lower()
    is_rejection_reply = (
        (payload.concepts is not None and len(payload.concepts) == 0)
        or "reject" in all_reply_text
        or "partnership update" in all_reply_text
        or "regarding creator forge partnership" in all_reply_text
        or "will not be proceeding" in all_reply_text
        or "won't be able to move forward" in all_reply_text
        or "prioritizing specific software verticals" in all_reply_text
        or (thread.creator and getattr(thread.creator, "status", None) == "rejected")
    )

    if is_rejection_reply:
        reply_concepts = None
        reply_concept_image = None
    elif not reply_concepts and thread.creator:
        is_pitch_reply = bool(re.search(r"concept 1|concept 2|concept 3|blueprint|opportunity deck|software concepts|pitch", all_reply_text))
        if is_pitch_reply:
            creator_notes = getattr(thread.creator, "discovery_notes", None) or getattr(thread.creator, "niche_data", None)
            if creator_notes:
                try:
                    nd = json.loads(creator_notes) if isinstance(creator_notes, str) else creator_notes
                    reply_concepts = nd.get("product_concepts") or nd.get("concepts")
                    if not reply_concept_image and reply_concepts:
                        first_c = reply_concepts[0]
                        mockup = first_c.get("mockup") if isinstance(first_c.get("mockup"), dict) else {}
                        reply_concept_image = (
                            first_c.get("customImageUrl")
                            or first_c.get("imageUrl")
                            or first_c.get("image_url")
                            or first_c.get("mockup_url")
                            or mockup.get("imageUrl")
                            or mockup.get("previewUrl")
                        )
                except Exception:
                    pass

    # Send the email with luxury responsive HTML formatting
    c_name = thread.creator.display_name if thread.creator else ""
    body_html = format_luxury_html_email(
        body_text=payload.body,
        subject=subject,
        creator_name=c_name,
        concept_image_url=reply_concept_image,
        concepts=reply_concepts,
    )
    try:
        email_provider.send(
            to_email=to_email,
            subject=subject,
            body_text=payload.body,
            body_html=body_html
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to send reply: {str(e)}")
        
    # Save the reply in the DB so it appears in the thread
    from_address = settings.GOOGLE_EMAIL or settings.FROM_EMAIL
    
    reply = Reply(
        thread_id=thread_id,
        from_address=from_address,
        subject=subject,
        body=payload.body,
        classification="other",
        ai_summary="Outgoing reply from you"
    )
    db.add(reply)
    
    thread.last_activity = datetime.utcnow()
    db.commit()
    db.refresh(reply)
    
    return {"status": "sent", "recipient_email": to_email, "reply": _reply_dict(reply)}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _msg_dict(m: OutreachMessage) -> dict:
    creator_name = None
    creator_email = None
    if m.creator:
        creator_name = m.creator.display_name or m.creator.handle
        creator_email = m.creator.email_public
    if not creator_email and m.contact:
        creator_email = m.contact.value

    return {
        "id": m.id,
        "creator_id": m.creator_id,
        "creator_name": creator_name,
        "creator_email": creator_email,
        "campaign_id": m.campaign_id,
        "contact_id": m.contact_id,
        "deck_id": m.deck_id,
        "subject": m.subject,
        "body": m.body,
        "send_method": m.send_method,
        "status": m.status,
        "send_error": m.send_error,
        "reviewed_by": m.reviewed_by,
        "review_notes": m.review_notes,
        "sent_at": m.sent_at.isoformat() if m.sent_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _followup_dict(f: FollowUp) -> dict:
    return {
        "id": f.id, "thread_id": f.thread_id, "draft": f.draft,
        "status": f.status,
        "scheduled_for": f.scheduled_for.isoformat() if f.scheduled_for else None,
        "sent_at": f.sent_at.isoformat() if f.sent_at else None,
        "reviewed_by": f.reviewed_by,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _reply_dict(r: Reply) -> dict:
    from app.config import settings
    admin_emails = {
        (settings.GOOGLE_EMAIL or "").lower().strip(),
        (settings.FROM_EMAIL or "").lower().strip(),
        "creatorforgeweb@gmail.com",
        "creatorforgestudio@gmail.com",
        "partnerships@creatorforge.com",
        "noreply@creatorforge.com",
        "hello@creatorforge.com",
    }
    admin_emails.discard("")
    from_addr = (r.from_address or "").lower().strip()
    is_outgoing = (
        from_addr in admin_emails
        or r.ai_summary == "Outgoing reply from you"
        or "creatorforge" in from_addr
        or "brevosend" in from_addr
        or from_addr.startswith("admin@")
    )
    return {
        "id": r.id, "thread_id": r.thread_id, "from_address": r.from_address,
        "subject": r.subject, "body": r.body,
        "classification": "other" if is_outgoing else r.classification,
        "sentiment": r.sentiment,
        "ai_summary": "Outgoing reply from you" if is_outgoing else r.ai_summary,
        "is_outgoing": is_outgoing,
        "crm_stage": r.crm_stage,
        "received_at": (r.received_at.isoformat() + "Z") if (r.received_at and not r.received_at.isoformat().endswith("Z") and not ("+" in r.received_at.isoformat())) else (r.received_at.isoformat() if r.received_at else None),
    }


def _thread_dict(t: Thread) -> dict:
    from app.services.autonomous_outreach import is_real_valid_email
    from app.config import settings

    # Include the original outreach message details
    original_subject = None
    original_body = None
    if t.outreach_message:
        original_subject = t.outreach_message.subject
        original_body = t.outreach_message.body

    # Include creator name & email
    creator_name = None
    creator_handle = None
    creator_email = None
    creator_avatar = None
    if t.creator:
        creator_name = t.creator.display_name or t.creator.handle
        creator_handle = t.creator.handle
        creator_email = t.creator.email_public
        creator_avatar = t.creator.avatar_url
    if not creator_email and t.outreach_message and t.outreach_message.contact:
        creator_email = t.outreach_message.contact.value

    recipient_email = None
    admin_email = (settings.GOOGLE_EMAIL or settings.FROM_EMAIL or "").lower().strip()
    if t.replies:
        for r in reversed(t.replies):
            if r.from_address and r.from_address.lower().strip() != admin_email and is_real_valid_email(r.from_address):
                recipient_email = r.from_address.strip()
                break

    if not recipient_email:
        recipient_email = creator_email

    return {
        "id": t.id,
        "creator_id": t.creator_id,
        "creator_name": creator_name,
        "creator_handle": creator_handle,
        "creator_email": creator_email,
        "recipient_email": recipient_email,
        "creator_avatar": creator_avatar,
        "outreach_message_id": t.outreach_message_id,
        "subject": original_subject or (t.replies[0].subject if t.replies else None),
        "original_subject": original_subject,
        "original_body": original_body,
        "status": t.status,
        "last_activity": t.last_activity.isoformat() if t.last_activity else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "replies": [_reply_dict(r) for r in (t.replies or [])],
    }
