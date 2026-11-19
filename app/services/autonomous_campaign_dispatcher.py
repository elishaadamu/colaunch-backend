# -*- coding: utf-8 -*-
"""
Autonomous Campaign Post Dispatcher Service
Enables zero-effort creator publishing:
- Automatically formats daily post kits (formatted caption, embedded visual preview, direct download links for high-res images and videos).
- Delivers post kits directly to creator/founder inboxes based on their campaign schedule day.
- Supports both autonomous cron dispatch and 1-click on-demand test delivery.
"""

import os
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.models.project import CoLaunchProject, ValidationCampaign
from app.integrations.email_provider import EmailProvider

logger = logging.getLogger(__name__)


def dispatch_campaign_post_email(
    db: Optional[Session] = None,
    project_id: str = "",
    task_id: Optional[str] = None,
    day: Optional[int] = None,
    recipient_email: Optional[str] = None,
    caller: Optional[str] = "admin"
) -> Dict[str, Any]:
    """
    Locates the scheduled post for the given project, constructs the daily post kit email,
    and dispatches it to the creator/founder's inbox.
    """
    # 1. Fetch project from MongoDB first with flexible ID / handle resolution
    mongo_proj = None
    try:
        from app.routers.projects import _find_mongo_project
        mongo_proj = _find_mongo_project(project_id)
    except Exception:
        pass

    if not mongo_proj:
        try:
            from app.mongodb import get_collection
            p_coll = get_collection("co_launch_projects")
            if p_coll is not None:
                clean_target = str(project_id).replace("@", "").lower().strip()
                mongo_proj = p_coll.find_one({"$or": [
                    {"_id": project_id},
                    {"id": project_id},
                    {"creator_id": project_id},
                    {"creatorId": project_id},
                    {"creator_handle": clean_target},
                    {"creatorHandle": clean_target},
                    {"creator_handle": f"@{clean_target}"},
                    {"creatorHandle": f"@{clean_target}"},
                ]})
        except Exception:
            pass

    proj = None
    if db:
        try:
            proj = db.get(CoLaunchProject, project_id)
        except Exception:
            proj = None
    if not proj and not mongo_proj:
        raise ValueError(f"Project '{project_id}' not found")

    # Extract campaign kit from MongoDB or in-memory model
    raw_kit = None
    if mongo_proj:
        raw_kit = (
            mongo_proj.get("campaign_kit")
            or mongo_proj.get("campaignKit")
            or (mongo_proj.get("metadataInfo") or {}).get("campaign_kit")
            or (mongo_proj.get("metadataInfo") or {}).get("campaignKit")
        )

    campaign = proj.validation_campaign if proj and getattr(proj, "validation_campaign", None) else None
    if not raw_kit and campaign:
        raw_kit = getattr(campaign, "campaign_kit", None)
    if not raw_kit and proj and proj.metadata_info:
        meta_dict = proj.metadata_info if isinstance(proj.metadata_info, dict) else {}
        raw_kit = meta_dict.get("campaign_kit") or meta_dict.get("campaignKit")

    if not raw_kit:
        raise ValueError("Campaign Kit has not been generated for this project yet")

    kit = dict(raw_kit)
    schedule: List[Dict[str, Any]] = list(kit.get("postingSchedule") or [])

    if not schedule:
        raise ValueError("No posting schedule found in campaign kit")

    # Resolve project metadata values
    c_name = (mongo_proj.get("creatorName") or mongo_proj.get("creator_name")) if mongo_proj else getattr(proj, "creator_name", "")
    c_handle = (mongo_proj.get("creatorHandle") or mongo_proj.get("creator_handle")) if mongo_proj else getattr(proj, "creator_handle", "")
    p_name = (mongo_proj.get("productName") or mongo_proj.get("product_name")) if mongo_proj else getattr(proj, "product_name", "Software Platform")
    p_tagline = (mongo_proj.get("productTagline") or mongo_proj.get("product_tagline")) if mongo_proj else getattr(proj, "product_tagline", "")
    c_email = (mongo_proj.get("creatorEmail") or mongo_proj.get("creator_email")) if mongo_proj else getattr(proj, "creator_email", None)

    # Select target task
    selected_task = None
    if task_id:
        for t in schedule:
            if str(t.get("id")) == str(task_id):
                selected_task = t
                break
    elif day is not None:
        for t in schedule:
            if t.get("day") == day:
                selected_task = t
                break

    if not selected_task:
        # Default to the active 'today' task or the first uncompleted task
        for t in schedule:
            if t.get("isToday") and not t.get("done") and not t.get("emailed"):
                selected_task = t
                break
        if not selected_task:
            for t in schedule:
                if not t.get("done") and not t.get("emailed"):
                    selected_task = t
                    break
        if not selected_task:
            for t in schedule:
                if not t.get("done"):
                    selected_task = t
                    break
        if not selected_task:
            selected_task = schedule[0]

    # Resolve recipient email address
    auto_config = kit.get("autonomousEmailDelivery") or {}
    proj_email = getattr(proj, "email", None) if proj else None
    target_email = (
        recipient_email
        or auto_config.get("recipientEmail")
        or c_email
        or proj_email
        or getattr(settings, "ADMIN_EMAIL", None)
        or getattr(settings, "GOOGLE_EMAIL", None)
    )

    if not target_email or "@" not in target_email:
        raise ValueError(
            "No valid recipient email address found. Please provide an email address or configure Autonomous Email Delivery."
        )

    target_email = target_email.strip()

    # Strictly determine sequential day and milestone numbers (Day 1 = Post 1, Day 2 = Post 2, etc.)
    task_idx = 0
    for idx, t in enumerate(schedule):
        if str(t.get("id")) == str(selected_task.get("id")):
            task_idx = idx
            break

    day_num = task_idx + 1
    selected_task["day"] = day_num
    selected_task["dayNumber"] = day_num
    selected_task["day_number"] = day_num
    selected_task["milestoneNumber"] = day_num

    # Determine post copy based on task content, draft, or channel
    channel = (selected_task.get("channel") or "Social Media").lower()
    task_title = selected_task.get("title") or "Campaign Post"
    task_desc = selected_task.get("description") or ""

    task_custom_content = selected_task.get("content") or selected_task.get("draft")
    if task_custom_content:
        caption = task_custom_content
    elif "video" in channel or "youtube" in channel or "reel" in channel or "short" in channel:
        caption = kit.get("videoScript") or task_desc
    elif "story" in channel:
        caption = kit.get("storySequence") or task_desc
    elif "newsletter" in channel or "email" in channel:
        caption = kit.get("newsletterDraft") or task_desc
    else:
        caption = kit.get("announcementPost") or task_desc

    # Visual assets
    image_url = (
        selected_task.get("imageUrl")
        or selected_task.get("postImageUrl")
        or kit.get("cloudinaryImageUrl")
        or kit.get("postImageUrl")
    )
    video_url = (
        selected_task.get("videoUrl")
        or kit.get("cloudinaryVideoUrl")
        or kit.get("videoUrl")
    )

    # ── Build the post kit (new template, real attachments) ────────────────────────────────
    from app.services import campaign_delivery as cd
    doc_for_links = mongo_proj or {"productName": p_name}
    kind = cd.channel_kind(selected_task)
    link = f"{cd.public_base_url()}/preorder/{cd.project_slug(doc_for_links)}?ref={cd.ref_for(kind)}"
    socials = {k: v for k, v in (kit.get("socialLinks") or {}).items() if isinstance(v, str) and v.startswith("http")}
    if not socials.get("youtube") and mongo_proj:
        ch_url = mongo_proj.get("channelUrl") or (mongo_proj.get("metadataInfo") or {}).get("channel_url")
        if ch_url:
            socials["youtube"] = ch_url
    sel = (mongo_proj or {}).get("selectedConcept") or {}
    accent = sel.get("landingBrandColor") or (mongo_proj or {}).get("brandColor") or "#c81e3c"
    if kind != "video":
        video_url = selected_task.get("videoUrl") if selected_task.get("videoUrl") else None
    if kind == "video" and not video_url:
        video_url = kit.get("videoUrl")
    video_thumb = selected_task.get("thumbnailUrl") or kit.get("videoThumbnailUrl")

    attachments = []
    attached = []
    if image_url and kind in ("post", "story", "dm") :
        att = cd.fetch_attachment(image_url, f"day-{day_num}-graphic.png", "image/png", cd.MAX_IMAGE_ATTACH_BYTES)
        if att:
            attachments.append(att)
            attached.append("image")
    if video_url:
        att = cd.fetch_attachment(video_url, f"day-{day_num}-video.mp4", "video/mp4", cd.MAX_VIDEO_ATTACH_BYTES)
        if att:
            attachments.append(att)
            attached.append("video")

    upcoming = [t for t in schedule[task_idx + 1:] if not t.get("done")]
    for i, t in enumerate(schedule):
        t["day"] = i + 1
    cfg = cd.get_delivery_config(str((mongo_proj or {}).get("_id") or (mongo_proj or {}).get("id") or project_id)) or {}
    tz_label = cfg.get("timezone") or kit.get("creatorTimezone") or auto_config.get("timezone") or ""
    send_label = cd.local_now(tz_label).strftime("%A %d %B") if tz_label else ""

    subject = f"Day {day_num}: {task_title} · {p_name}"
    html_content, plain_text = cd.build_post_kit_email(
        creator_name=c_name,
        product_name=p_name,
        product_tagline=p_tagline,
        task=selected_task,
        day_num=day_num,
        total_days=len(schedule),
        caption=caption,
        image_url=image_url,
        video_url=video_url,
        video_thumb_url=video_thumb,
        link=link,
        socials=socials,
        upcoming=upcoming,
        accent=accent,
        send_label=send_label,
        attached=attached,
    )

    provider = EmailProvider()
    send_result = provider.send(
        to_email=target_email,
        subject=subject,
        body_html=html_content,
        body_text=plain_text,
        from_name=f"{p_name} · CoLaunch",
        attachments=attachments or None,
    )

    # Durable sent log (survives any later kit save from the page)
    pid_for_log = str((mongo_proj or {}).get("_id") or (mongo_proj or {}).get("id") or project_id)
    try:
        cd.record_task_sent(pid_for_log, selected_task, target_email,
                            auto=str(caller or "").startswith("autonomous"), tz_name=tz_label or "UTC")
    except Exception as log_err:
        logger.warning(f"[Post kit] Could not record send: {log_err}")

    # Mark task as emailed and enforce sequential day numbers on all tasks
    selected_task["emailed"] = True
    selected_task["lastEmailedAt"] = datetime.utcnow().isoformat()
    selected_task["lastEmailedTo"] = target_email

    for idx, t in enumerate(schedule):
        t["day"] = idx + 1
        t["dayNumber"] = idx + 1
        t["day_number"] = idx + 1
        t["milestoneNumber"] = idx + 1
        t["spacingNotice"] = f"Day {idx + 1} of {len(schedule)}"

    # 1. Update MongoDB Atlas collections directly
    kit["postingSchedule"] = schedule
    if target_email:
        kit["creatorEmail"] = target_email
        auto_del = kit.get("autonomousEmailDelivery")
        if not auto_del or not isinstance(auto_del, dict):
            auto_del = {}
        auto_del["recipientEmail"] = target_email
        if "enabled" not in auto_del:
            auto_del["enabled"] = False
            auto_del["intervalHours"] = 24
            auto_del["intervalSeconds"] = 86400
            auto_del["cadence"] = "24_hours"
            auto_del["dispatchTime"] = "Every 24 Hours"
        auto_del["updatedAt"] = datetime.utcnow().isoformat()
        kit["autonomousEmailDelivery"] = auto_del

    try:
        from app.mongodb import get_collection
        from app.routers.projects import _find_mongo_project, _save_mongo_project, _sanitize_heavy_media
        p_doc = _find_mongo_project(project_id) or mongo_proj or {"_id": project_id, "id": project_id}
        meta_m = p_doc.get("metadataInfo") if isinstance(p_doc.get("metadataInfo"), dict) else {}
        meta_m["campaign_kit"] = kit
        meta_m["campaign_launched"] = True
        p_doc["metadataInfo"] = meta_m
        p_doc["campaign_kit"] = kit
        p_doc["campaignKit"] = kit
        if target_email:
            p_doc["creatorEmail"] = target_email
            p_doc["creator_email"] = target_email
        _save_mongo_project(p_doc)

        vc_coll = get_collection("validation_campaigns")
        if vc_coll is not None:
            doc_id = str(p_doc.get("_id") or p_doc.get("id") or project_id)
            vc_coll.update_one({"_id": doc_id}, {"$set": {
                "project_id": doc_id,
                "campaign_kit": _sanitize_heavy_media(kit),
                "campaign_launched": True,
                "updated_at": datetime.utcnow().isoformat(),
            }}, upsert=True)
    except Exception as m_err:
        logger.debug(f"[MongoDB] Autonomous dispatch MongoDB update note: {m_err}")

    # 2. Update in-memory session if present
    if proj and db:
        if not campaign:
            campaign = ValidationCampaign(project_id=proj.id, campaign_kit=kit)
            db.add(campaign)
        else:
            campaign.campaign_kit = kit
            flag_modified(campaign, "campaign_kit")

        meta = dict(proj.metadata_info or {})
        meta["campaign_kit"] = kit
        proj.metadata_info = meta
        flag_modified(proj, "metadata_info")

        try:
            db.commit()
        except Exception:
            pass

    logger.info(f"✅ Dispatched Day {selected_task.get('day')} Post Kit to {target_email} (MessageID: {send_result.get('message_id')})")

    return {
        "success": True,
        "recipient": target_email,
        "taskId": selected_task.get("id"),
        "day": selected_task.get("day"),
        "title": task_title,
        "channel": selected_task.get("channel"),
        "messageId": send_result.get("message_id"),
        "hasImage": bool(image_url),
        "hasVideo": bool(video_url),
        "message": f"Day {selected_task.get('day')} Post Kit successfully delivered to {target_email}!"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Autonomous Simulation & Schedule Polling
# ─────────────────────────────────────────────────────────────────────────────

import asyncio

_campaign_scheduler_running = False


async def start_campaign_schedule_loop(check_interval_seconds: int = 60):
    """
    Background loop that wakes up periodically (default every 60s),
    evaluates projects with autonomousEmailDelivery enabled,
    and dispatches due post kits on a strict 24-hour cadence.
    """
    global _campaign_scheduler_running
    if _campaign_scheduler_running:
        return
    _campaign_scheduler_running = True
    logger.info(f"🚀 [Autonomous Campaign Scheduler] 24-hour cadence loop started (polling every {check_interval_seconds}s)")

    await asyncio.sleep(5)  # Let server complete initial startup and socket binding

    from app.services.campaign_delivery import run_delivery_tick
    while _campaign_scheduler_running:
        try:
            sent = await asyncio.to_thread(run_delivery_tick)
            if sent:
                logger.info(f"[Post kit scheduler] Sent {sent} daily post kit(s)")
        except Exception as e:
            logger.error(f"[Post kit scheduler] Tick error: {e}")
        await asyncio.sleep(check_interval_seconds)


def stop_campaign_schedule_loop():
    global _campaign_scheduler_running
    _campaign_scheduler_running = False
    logger.info("[Campaign Scheduler] Loop stopped.")

