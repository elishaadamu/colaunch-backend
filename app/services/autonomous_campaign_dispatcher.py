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


def format_daily_post_kit_email_html(
    creator_name: str,
    creator_handle: str,
    product_name: str,
    product_tagline: str,
    milestone_num: int,
    day_num: int,
    channel: str,
    title: str,
    description: str,
    caption_text: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    preorder_url: Optional[str] = None,
    recommended_time: Optional[str] = None
) -> str:
    """
    Renders an executive, ready-to-publish Daily Post Kit HTML email.
    Crafted with luxury modern aesthetics, responsive layout, clear CTAs,
    and 100% email client deliverability (Gmail, Apple Mail, Outlook).
    """
    c_name = creator_name or "Creator"
    handle = f"@{creator_handle.lstrip('@')}" if creator_handle else ""
    p_name = product_name or "Software Platform"
    ch = (channel or "Social Media").upper()
    rec_time = recommended_time or "12:00 AM (Midnight) in creator's local timezone"
    target_link = preorder_url or "https://creatorforge.app/preorder"

    # Clean caption for copy block
    clean_caption = (caption_text or description or "").strip()

    # Asset download buttons
    asset_buttons_html = ""
    asset_preview_html = ""

    if image_url:
        full_img_url = image_url
        if not full_img_url.startswith("http"):
            base_domain = getattr(settings, "BACKEND_URL", "https://creator-forge-backend-jj1y.onrender.com")
            full_img_url = f"{base_domain.rstrip('/')}/{image_url.lstrip('/')}"

        asset_preview_html += f"""
        <div style="margin: 22px 0 16px 0; background: #0f172a; border: 1px solid #1e293b; border-radius: 14px; padding: 16px; text-align: center; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);">
          <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 0 4px;">
            <span style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: #94a3b8; font-weight: 700; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">High-Res Campaign Visual</span>
            <span style="font-size: 10px; background: #1e293b; color: #38bdf8; padding: 2px 8px; border-radius: 6px; font-weight: 700; font-family: ui-monospace, monospace;">PNG • Ready to Post</span>
          </div>
          <img src="{full_img_url}" alt="Campaign Post Graphic" style="max-width: 100%; height: auto; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); display: inline-block;" />
        </div>
        """
        asset_buttons_html += f"""
        <a href="{full_img_url}" download="post_graphic_{day_num}.png" target="_blank" style="display: inline-block; background: #0284c7; color: #ffffff; text-decoration: none; padding: 12px 22px; font-weight: 700; font-size: 13px; border-radius: 10px; margin: 4px 4px; box-shadow: 0 2px 8px rgba(2,132,199,0.25); text-align: center; letter-spacing: 0.2px;">
          Download High-Res Graphic (PNG)
        </a>
        """

    if video_url:
        full_vid_url = video_url
        if not full_vid_url.startswith("http"):
            base_domain = getattr(settings, "BACKEND_URL", "https://creator-forge-backend-jj1y.onrender.com")
            full_vid_url = f"{base_domain.rstrip('/')}/{video_url.lstrip('/')}"

        asset_buttons_html += f"""
        <a href="{full_vid_url}" download="campaign_video_day_{day_num}.mp4" target="_blank" style="display: inline-block; background: #059669; color: #ffffff; text-decoration: none; padding: 12px 22px; font-weight: 700; font-size: 13px; border-radius: 10px; margin: 4px 4px; box-shadow: 0 2px 8px rgba(5,150,105,0.25); text-align: center; letter-spacing: 0.2px;">
          Download Video Teaser (MP4)
        </a>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Day {day_num} Campaign Post Kit: {title}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #0f172a; margin: 0; padding: 32px 12px;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 620px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 10px 30px -5px rgba(0, 0, 0, 0.04), 0 0 1px 1px rgba(0, 0, 0, 0.02); overflow: hidden;">
    
    <!-- Top Accent Bar (Obsidian with Emerald Gradient Accent) -->
    <tr>
      <td height="4" style="background: linear-gradient(90deg, #c81e3c 0%, #10b981 50%, #f59e0b 100%); line-height: 4px; font-size: 4px;">&nbsp;</td>
    </tr>

    <!-- Header Block -->
    <tr>
      <td style="padding: 28px 32px 20px 32px; background-color: #ffffff; border-bottom: 1px solid #f1f5f9;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0">
          <tr>
            <td>
              <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-bottom: 14px;">
                <span style="display: inline-block; background-color: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.9px; padding: 4px 12px; border-radius: 20px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                  DAY {day_num} CO-LAUNCH ASSET • {ch}
                </span>
                <span style="font-size: 11px; color: #64748b; font-weight: 600; font-family: ui-monospace, monospace;">
                  Milestone {milestone_num}
                </span>
              </div>
              
              <h1 style="color: #0f172a; font-size: 22px; font-weight: 800; margin: 0 0 8px 0; line-height: 1.35; letter-spacing: -0.3px;">
                {title}
              </h1>
              <p style="color: #64748b; font-size: 13px; margin: 0; line-height: 1.5;">
                Co-Launch Venture: <strong style="color: #0f172a;">{p_name}</strong> with {c_name} <span style="color: #0284c7; font-weight: 600;">{handle}</span>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- Main Body Area -->
    <tr>
      <td style="padding: 24px 32px;">
        
        <!-- Quick Guidance Pill Box -->
        <div style="background-color: #f8fafc; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; border: 1px solid #e2e8f0; border-left: 4px solid #0284c7;">
          <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
              <td style="font-size: 13px; color: #334155; line-height: 1.65;">
                <div style="margin-bottom: 4px;"><strong style="color: #0f172a;">Target Channel:</strong> <span style="color: #0284c7; font-weight: 700;">{ch}</span></div>
                <div style="margin-bottom: 4px;"><strong style="color: #0f172a;">Recommended Timing:</strong> {rec_time}</div>
                <div><strong style="color: #0f172a;">Objective:</strong> Copy the prepared caption, download the media asset below, and publish to activate audience pre-orders.</div>
              </td>
            </tr>
          </table>
        </div>

        <!-- Visual Media Showcase & Action Buttons -->
        {asset_preview_html}

        {f'<div style="text-align: center; margin: 16px 0 24px 0;">{asset_buttons_html}</div>' if asset_buttons_html else ''}

        <!-- Ready to Copy Caption Container -->
        <div style="margin-top: 24px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-size: 11px; font-weight: 800; color: #0f172a; text-transform: uppercase; letter-spacing: 0.8px;">
              READY-TO-PUBLISH CAPTION
            </span>
            <span style="font-size: 11px; color: #64748b; font-weight: 500;">(Select all & copy)</span>
          </div>
          <div style="background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 12px; padding: 18px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13.5px; line-height: 1.7; color: #0f172a; white-space: pre-wrap; word-break: break-word; user-select: all;">{clean_caption}</div>
        </div>

        <!-- Pre-Order Live URL Banner -->
        <div style="margin-top: 22px; padding: 16px 20px; background-color: #f0f9ff; border: 1px solid #bae6fd; border-radius: 12px;">
          <div style="font-size: 11px; font-weight: 800; color: #0369a1; text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 4px;">
            Pre-Order & Validation Page
          </div>
          <div style="font-size: 13px; color: #0c4a6e; line-height: 1.5;">
            Direct backer tracking link: <a href="{target_link}" style="color: #0284c7; font-weight: 700; text-decoration: underline;" target="_blank">{target_link}</a>
          </div>
        </div>

        <!-- 30-Second Publishing Flow -->
        <div style="margin-top: 28px; padding-top: 20px; border-top: 1px solid #f1f5f9;">
          <p style="font-size: 11px; font-weight: 800; color: #64748b; text-transform: uppercase; margin: 0 0 10px 0; letter-spacing: 0.6px;">
            HOW TO POST IN 30 SECONDS:
          </p>
          <ol style="margin: 0; padding-left: 20px; font-size: 13px; color: #475569; line-height: 1.8;">
            <li>Select and copy the caption block above.</li>
            <li>Click download for the image or video on this email.</li>
            <li>Open {ch} on your phone or computer, paste the caption, attach the downloaded asset, and publish!</li>
          </ol>
        </div>

      </td>
    </tr>

    <!-- Footer -->
    <tr>
      <td style="padding: 24px 32px; background-color: #f8fafc; border-top: 1px solid #e2e8f0; text-align: center;">
        <p style="font-size: 12px; color: #475569; margin: 0 0 6px 0; font-weight: 600;">
          Sent by Creator Forge Co-Launch Studio for {c_name} {handle}.
        </p>
        <p style="font-size: 11px; color: #94a3b8; margin: 0; line-height: 1.5;">
          Zero app logins required. Post kits are delivered on your scheduled milestone days.
        </p>
      </td>
    </tr>

  </table>
</body>
</html>
"""


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

    # Format HTML
    subject = f"[Day {day_num} Post Kit] {task_title} • {p_name}"
    tz_label = kit.get("creatorTimezone") or auto_config.get("timezone") or "local timezone"
    preorder_slug = (p_name or "launch").lower().replace(" ", "-")
    preorder_link = f"https://creator-forge-frontend.vercel.app/preorder/{preorder_slug}?ref=post_kit"

    html_content = format_daily_post_kit_email_html(
        creator_name=c_name,
        creator_handle=c_handle,
        product_name=p_name,
        product_tagline=p_tagline,
        milestone_num=day_num,
        day_num=day_num,
        channel=selected_task.get("channel", "Social Media"),
        title=task_title,
        description=task_desc,
        caption_text=caption,
        image_url=image_url,
        video_url=video_url,
        preorder_url=preorder_link,
        recommended_time=f"12:00 AM (Midnight) · {tz_label}"
    )

    plain_text = f"""
[CREATOR FORGE DAILY POST KIT • DAY {day_num}]
Venture: {p_name}
Milestone: {task_title}
Channel: {selected_task.get('channel', 'Social Media')}

CAPTION TO COPY:
{caption}

ASSETS:
- Image: {image_url or 'None'}
- Video: {video_url or 'None'}

Pre-Order URL: {preorder_link}
"""

    # Dispatch email
    provider = EmailProvider()
    send_result = provider.send(
        to_email=target_email,
        subject=subject,
        body_html=html_content,
        body_text=plain_text,
        from_name=f"{p_name} Launch Studio"
    )

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
            auto_del["enabled"] = True
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
            vc_doc = {
                "_id": doc_id,
                "project_id": doc_id,
                "campaign_kit": _sanitize_heavy_media(kit),
                "campaign_launched": True,
                "updated_at": datetime.utcnow().isoformat(),
            }
            vc_coll.replace_one({"_id": doc_id}, vc_doc, upsert=True)
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
_active_simulations: Dict[str, Dict[str, Any]] = {}


def get_simulation_status(project_id: str) -> Dict[str, Any]:
    return _active_simulations.get(project_id, {
        "status": "idle",
        "running": False,
        "logs": [],
        "completed": 0,
        "total": 0
    })


def stop_simulation(project_id: str) -> bool:
    if project_id in _active_simulations:
        _active_simulations[project_id]["cancelled"] = True
        return True
    return False


async def run_autonomous_campaign_simulation(
    project_id: str,
    recipient_email: Optional[str] = None,
    interval_seconds: int = 42,
    total_posts: Optional[int] = None
) -> Dict[str, Any]:
    """
    Autonomously executes scheduled email dispatch over ~5 minutes (interval default 42s).
    Dispatches Post 1, Post 2, Post 3, etc. to recipient_email in real-time.
    """
    sim_state = {
        "status": "starting",
        "running": True,
        "cancelled": False,
        "projectId": project_id,
        "recipient": recipient_email,
        "total": 0,
        "completed": 0,
        "intervalSeconds": interval_seconds,
        "startedAt": datetime.utcnow().isoformat(),
        "completedAt": None,
        "logs": []
    }
    _active_simulations[project_id] = sim_state

    try:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
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
                            {"creatorHandle": f"@{clean_target}"}
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
                raise ValueError(f"Project '{project_id}' not found in database")

            sql_kit = None
            if proj:
                if proj.validation_campaign and getattr(proj.validation_campaign, "campaign_kit", None):
                    sql_kit = proj.validation_campaign.campaign_kit
                elif proj.metadata_info:
                    sql_kit = (proj.metadata_info or {}).get("campaign_kit")
            raw_kit = (
                (mongo_proj.get("campaign_kit") or mongo_proj.get("campaignKit") or (mongo_proj.get("metadataInfo") or {}).get("campaign_kit") if mongo_proj else None)
                or sql_kit
                or {}
            )
            schedule = list(raw_kit.get("postingSchedule") or [])
            if not schedule:
                raise ValueError("No posting schedule found in campaign kit")
        finally:
            db.close()

        tasks_to_send = schedule[:total_posts] if total_posts else schedule
        total = len(tasks_to_send)
        sim_state["total"] = total
        sim_state["status"] = "running"

        logger.info(f"🚀 [Campaign Simulation] Starting 5-min autonomous dispatch for {project_id} ({total} posts, interval: {interval_seconds}s)")
    except Exception as setup_err:
        logger.error(f"❌ [Campaign Simulation] Setup error for {project_id}: {setup_err}")
        sim_state["status"] = "error"
        sim_state["running"] = False
        sim_state["error"] = str(setup_err)
        return sim_state


    for idx, task in enumerate(tasks_to_send):
        if sim_state.get("cancelled"):
            sim_state["status"] = "cancelled"
            sim_state["running"] = False
            logger.info(f"🛑 [Campaign Simulation] Cancelled by user for {project_id}")
            break

        db_run = SessionLocal()
        try:
            task_id = str(task.get("id"))
            task_title = task.get("title", f"Post {idx+1}")
            task_day = idx + 1

            log_entry = {
                "step": idx + 1,
                "total": total,
                "taskId": task_id,
                "title": task_title,
                "day": task_day,
                "status": "sending",
                "timestamp": datetime.utcnow().isoformat()
            }
            sim_state["logs"].append(log_entry)

            # Dispatch the email
            res = dispatch_campaign_post_email(
                db=db_run,
                project_id=project_id,
                task_id=task_id,
                recipient_email=recipient_email,
                caller="autonomous_simulation"
            )

            log_entry["status"] = "delivered"
            log_entry["messageId"] = res.get("messageId")
            log_entry["recipient"] = res.get("recipient")
            sim_state["completed"] = idx + 1

            logger.info(
                f"✅ [Campaign Simulation] Delivered Post {idx+1}/{total}: '{task_title}' (Day {task_day}) "
                f"to {res.get('recipient')} [MsgId: {res.get('messageId')}]"
            )
        except Exception as dispatch_err:
            logger.error(f"❌ [Campaign Simulation] Post {idx+1} dispatch error: {dispatch_err}")
            sim_state["logs"].append({
                "step": idx + 1,
                "taskId": str(task.get("id")),
                "status": "failed",
                "error": str(dispatch_err),
                "timestamp": datetime.utcnow().isoformat()
            })
        finally:
            db_run.close()

        # If not the last post, wait interval_seconds before sending the next one
        if idx < total - 1 and not sim_state.get("cancelled"):
            logger.info(f"⏳ [Campaign Simulation] Waiting {interval_seconds}s before next autonomous post dispatch...")
            await asyncio.sleep(interval_seconds)

    if not sim_state.get("cancelled"):
        sim_state["status"] = "completed"
        sim_state["running"] = False
        sim_state["completedAt"] = datetime.utcnow().isoformat()
        logger.info(f"🎉 [Campaign Simulation] Completed 5-minute autonomous dispatch for {project_id}!")

    return sim_state


def _evaluate_autonomous_delivery_for_project_data(
    project_id: str,
    raw_kit: Dict[str, Any],
    creator_email: Optional[str] = None,
    product_name: str = "Software Platform",
    db: Optional[Session] = None
) -> Optional[Dict[str, Any]]:
    """
    Evaluates a project for autonomous 24-hour post kit email dispatch.
    Enforces a strict opt-in check (auto_config['enabled'] == True) and
    a 24-hour (86,400 seconds) interval between dispatches.
    """
    if not isinstance(raw_kit, dict):
        return None

    auto_config = raw_kit.get("autonomousEmailDelivery")
    if not isinstance(auto_config, dict) or not auto_config.get("enabled"):
        return None

    # Must have a valid recipient email
    recipient = (
        auto_config.get("recipientEmail")
        or creator_email
        or raw_kit.get("creatorEmail")
        or raw_kit.get("creator_email")
    )
    if not recipient or "@" not in recipient:
        return None

    schedule = list(raw_kit.get("postingSchedule") or [])
    if not schedule:
        return None

    now = datetime.utcnow()
    # Check elapsed time from the most recent email dispatch across all tasks
    most_recent_sent = None
    for t in schedule:
        sent_str = t.get("lastEmailedAt")
        if sent_str:
            try:
                clean_str = str(sent_str).replace("Z", "+00:00").split("+")[0]
                sent_dt = datetime.fromisoformat(clean_str)
                if most_recent_sent is None or sent_dt > most_recent_sent:
                    most_recent_sent = sent_dt
            except Exception:
                pass

    REQUIRED_INTERVAL_SECONDS = 24 * 3600  # 86,400 seconds = 24 Hours

    if most_recent_sent is not None:
        elapsed_seconds = (now - most_recent_sent).total_seconds()
        if elapsed_seconds < REQUIRED_INTERVAL_SECONDS:
            remaining_hours = (REQUIRED_INTERVAL_SECONDS - elapsed_seconds) / 3600.0
            logger.info(
                f"[Autonomous 24H Dispatcher] Project '{project_id}': Post was emailed {elapsed_seconds/3600:.1f}h ago. "
                f"Next post kit due in {remaining_hours:.1f}h (24-hour cadence enforced)."
            )
            return None

    # Find the next pending un-emailed milestone task
    next_task = None
    for t in schedule:
        # A task is pending if not marked done, not emailed, and has no lastEmailedAt
        if not t.get("done") and not t.get("emailed") and not t.get("lastEmailedAt"):
            next_task = t
            break

    if next_task:
        task_day = next_task.get("day") or next_task.get("dayNumber") or 1
        task_title = next_task.get("title") or f"Post {task_day}"
        logger.info(
            f"[Autonomous 24H Dispatcher] 24-hour cadence reached: Auto-dispatching Day {task_day} "
            f"Post Kit ('{task_title}') for '{product_name}' to {recipient}"
        )
        return dispatch_campaign_post_email(
            db=db,
            project_id=project_id,
            task_id=str(next_task.get("id")),
            recipient_email=recipient,
            caller="autonomous_scheduler_24h"
        )
    else:
        logger.debug(f"[Autonomous 24H Dispatcher] Project '{project_id}': All scheduled milestone posts completed.")
        return None


def _evaluate_project_autonomous_delivery(db: Session, proj: CoLaunchProject):
    """Evaluates an in-memory or SQLite CoLaunchProject instance."""
    raw_kit = (
        (proj.validation_campaign.campaign_kit if proj.validation_campaign else None)
        or (proj.metadata_info or {}).get("campaign_kit")
        or {}
    )
    return _evaluate_autonomous_delivery_for_project_data(
        project_id=proj.id,
        raw_kit=raw_kit,
        creator_email=getattr(proj, "creator_email", None) or getattr(proj, "email", None),
        product_name=getattr(proj, "product_name", "Software Platform"),
        db=db
    )


def _evaluate_mongo_project_autonomous_delivery(m_doc: Dict[str, Any]):
    """Evaluates a MongoDB project document."""
    project_id = str(m_doc.get("_id") or m_doc.get("id"))
    
    # Guard: If project is still in Phase 1 planning/assets, never dispatch campaign posts
    current_phase = int(m_doc.get("current_phase") or m_doc.get("currentPhase") or 1)
    current_step = str(m_doc.get("current_step") or m_doc.get("currentStep") or "").lower()
    if current_phase == 1 and current_step in ("plan", "assets", "spec", ""):
        return None

    raw_kit = (
        m_doc.get("campaign_kit")
        or m_doc.get("campaignKit")
        or (m_doc.get("metadataInfo") or {}).get("campaign_kit")
        or (m_doc.get("metadataInfo") or {}).get("campaignKit")
        or {}
    )
    c_email = m_doc.get("creator_email") or m_doc.get("creatorEmail") or m_doc.get("email")
    p_name = m_doc.get("product_name") or m_doc.get("productName") or "Software Platform"

    return _evaluate_autonomous_delivery_for_project_data(
        project_id=project_id,
        raw_kit=raw_kit,
        creator_email=c_email,
        product_name=p_name,
        db=None
    )


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

    while _campaign_scheduler_running:
        try:
            mongo_evaluated_ids = set()
            # 1. Primary: Evaluate projects from MongoDB (active database)
            try:
                from app.mongodb import get_collection
                coll = get_collection("co_launch_projects")
                if coll is not None:
                    docs = list(coll.find({}))
                    for m_doc in docs:
                        pid = str(m_doc.get("_id") or m_doc.get("id"))
                        mongo_evaluated_ids.add(pid)
                        try:
                            _evaluate_mongo_project_autonomous_delivery(m_doc)
                        except Exception as m_err:
                            logger.debug(f"[Autonomous Scheduler] Mongo project {pid} eval notice: {m_err}")
            except Exception as mongo_err:
                logger.debug(f"[Autonomous Scheduler] Mongo loop notice: {mongo_err}")
        except Exception as e:
            logger.error(f"[Autonomous Scheduler] Loop tick error: {e}")

        await asyncio.sleep(check_interval_seconds)


def stop_campaign_schedule_loop():
    global _campaign_scheduler_running
    _campaign_scheduler_running = False
    logger.info("[Campaign Scheduler] Loop stopped.")

