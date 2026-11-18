# -*- coding: utf-8 -*-
"""
Campaign post-kit delivery (Phase 1 · Step 3).

Why this module exists: delivery state used to live inside the campaign kit, and the operator page
re-saves the whole kit on every edit, which wiped "enabled" flags and "already emailed" markers
(so days were skipped or re-sent). State now lives in its own collections:

  campaign_delivery    {_id: project_id, enabled, recipientEmail, timezone, sendHour, country, updatedAt}
  campaign_dispatches  {_id: "<pid>:task:<task_id>", kind: "task", project_id, task_id, day, sentAt, to, auto}
                       {_id: "<pid>:day:<YYYY-MM-DD>", kind: "day", project_id, localDate}   # one auto send per local day

The kit returned to the frontend gets these overlaid (see overlay_delivery_into_kit), so the UI always
shows the real state no matter what the page last saved.
"""

import base64
import html
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SEND_HOUR = 8  # 8 AM in the creator's own timezone
MAX_IMAGE_ATTACH_BYTES = 6 * 1024 * 1024
MAX_VIDEO_ATTACH_BYTES = 12 * 1024 * 1024

CRIMSON = "#c81e3c"
INK = "#0b0b0d"


# ── Collections ──────────────────────────────────────────────────────────────────────────────
def _coll(name: str):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def _tz(name: Optional[str]):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or "UTC")
    except Exception:
        return dt_timezone.utc


def local_now(tz_name: Optional[str]) -> datetime:
    return datetime.now(_tz(tz_name))


def get_delivery_config(project_id: str) -> Optional[Dict[str, Any]]:
    c = _coll("campaign_delivery")
    if c is None or not project_id:
        return None
    try:
        return c.find_one({"_id": str(project_id)})
    except Exception:
        return None


def save_delivery_config(project_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    c = _coll("campaign_delivery")
    clean = {
        "enabled": bool(cfg.get("enabled")),
        "recipientEmail": (cfg.get("recipientEmail") or "").strip(),
        "timezone": (cfg.get("timezone") or "UTC").strip(),
        "country": (cfg.get("country") or "").strip(),
        "sendHour": int(cfg.get("sendHour") if cfg.get("sendHour") is not None else DEFAULT_SEND_HOUR) % 24,
        "updatedAt": datetime.utcnow().isoformat(),
    }
    if c is not None:
        c.update_one({"_id": str(project_id)}, {"$set": clean, "$setOnInsert": {"createdAt": datetime.utcnow().isoformat()}}, upsert=True)
    return clean


def sent_task_log(project_id: str) -> Dict[str, Dict[str, Any]]:
    c = _coll("campaign_dispatches")
    if c is None:
        return {}
    try:
        return {str(d.get("task_id")): d for d in c.find({"project_id": str(project_id), "kind": "task"})}
    except Exception:
        return {}


def record_task_sent(project_id: str, task: Dict[str, Any], to_email: str, auto: bool, tz_name: Optional[str]) -> bool:
    """Log a task as sent. Returns True if this was the first time it was sent."""
    c = _coll("campaign_dispatches")
    if c is None:
        return True
    pid = str(project_id)
    tid = str(task.get("id"))
    first = c.find_one({"_id": f"{pid}:task:{tid}"}) is None
    now = datetime.utcnow().isoformat()
    c.update_one({"_id": f"{pid}:task:{tid}"}, {
        "$set": {"kind": "task", "project_id": pid, "task_id": tid, "day": task.get("day"),
                 "title": task.get("title"), "lastSentAt": now, "to": to_email, "auto": auto},
        "$setOnInsert": {"sentAt": now},
        "$inc": {"sendCount": 1},
    }, upsert=True)
    if first:
        # The first send of a new day's kit counts as that day's delivery (manual or automatic)
        local_date = local_now(tz_name).date().isoformat()
        try:
            c.update_one({"_id": f"{pid}:day:{local_date}"},
                         {"$setOnInsert": {"kind": "day", "project_id": pid, "localDate": local_date, "task_id": tid, "createdAt": now}},
                         upsert=True)
        except Exception:
            pass
    return first


def next_pending_task(schedule: List[Dict[str, Any]], sent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for t in schedule:
        if not t.get("done") and str(t.get("id")) not in sent:
            return t
    return None


def overlay_delivery_into_kit(project_id: str, kit: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay the durable delivery config + sent log onto a kit before it goes to the frontend."""
    if not isinstance(kit, dict) or not project_id:
        return kit
    cfg = get_delivery_config(project_id)
    sent = sent_task_log(project_id)
    schedule = kit.get("postingSchedule") if isinstance(kit.get("postingSchedule"), list) else []
    for t in schedule:
        log = sent.get(str(t.get("id")))
        if log:
            t["emailed"] = True
            t["lastEmailedAt"] = log.get("lastSentAt") or log.get("sentAt")
            t["firstEmailedAt"] = log.get("sentAt")
            t["lastEmailedTo"] = log.get("to")
        else:
            t["emailed"] = False
            t.pop("lastEmailedAt", None)
    if cfg:
        auto = dict(kit.get("autonomousEmailDelivery") or {})
        auto.update({
            "enabled": bool(cfg.get("enabled")),
            "recipientEmail": cfg.get("recipientEmail") or auto.get("recipientEmail"),
            "timezone": cfg.get("timezone"),
            "country": cfg.get("country"),
            "sendHour": cfg.get("sendHour", DEFAULT_SEND_HOUR),
            "preferredHour": cfg.get("sendHour", DEFAULT_SEND_HOUR),
            "cadence": "daily",
            "dispatchTime": f"Daily at {format_hour(cfg.get('sendHour', DEFAULT_SEND_HOUR))}",
            "dispatchSchedule": f"Daily at {format_hour(cfg.get('sendHour', DEFAULT_SEND_HOUR))} ({cfg.get('timezone') or 'UTC'})",
        })
        nxt = next_pending_task(schedule, sent)
        auto["nextTask"] = {"id": nxt.get("id"), "day": nxt.get("day"), "title": nxt.get("title"), "channel": nxt.get("channel")} if nxt else None
        auto["nextSendAt"] = _next_send_at_iso(project_id, cfg) if (cfg.get("enabled") and nxt) else None
        auto["sentCount"] = len(sent)
        kit["autonomousEmailDelivery"] = auto
        kit["creatorTimezone"] = cfg.get("timezone") or kit.get("creatorTimezone")
    return kit


def format_hour(h: Any) -> str:
    try:
        h = int(h) % 24
    except Exception:
        h = DEFAULT_SEND_HOUR
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:00 {suffix}"


def _next_send_at_iso(project_id: str, cfg: Dict[str, Any]) -> Optional[str]:
    from datetime import timedelta
    tz_name = cfg.get("timezone") or "UTC"
    now = local_now(tz_name)
    hour = int(cfg.get("sendHour", DEFAULT_SEND_HOUR)) % 24
    today_claimed = False
    c = _coll("campaign_dispatches")
    if c is not None:
        try:
            today_claimed = c.find_one({"_id": f"{project_id}:day:{now.date().isoformat()}"}) is not None
        except Exception:
            pass
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if today_claimed:
        target = target + timedelta(days=1)
    elif now >= target:
        target = now  # due now: goes out on the next scheduler tick
    return target.isoformat()


# ── Links & content helpers ──────────────────────────────────────────────────────────────────
def public_base_url() -> str:
    return (getattr(settings, "PUBLIC_APP_URL", "") or "https://colaunch-2.vercel.app").rstrip("/")


def project_slug(doc: Dict[str, Any]) -> str:
    raw = doc.get("slug") or doc.get("productName") or doc.get("product_name") or "launch"
    return re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-") or "launch"


def channel_kind(task: Dict[str, Any]) -> str:
    dk = (task.get("draftKey") or "").strip()
    if dk == "videoScript":
        return "video"
    if dk == "storySequence":
        return "story"
    if dk == "newsletterDraft":
        return "newsletter"
    if dk == "directMessageScript":
        return "dm"
    if dk == "announcementPost":
        return "post"
    ch = f"{task.get('channel') or ''} {task.get('title') or ''}".lower()
    if any(k in ch for k in ("video", "reel", "short", "tiktok", "youtube")) and "community" not in ch:
        return "video"
    if "story" in ch or "stories" in ch:
        return "story"
    if "newsletter" in ch or "email" in ch:
        return "newsletter"
    if "dm" in ch or "direct message" in ch:
        return "dm"
    return "post"


def ref_for(kind: str) -> str:
    return {"video": "tiktok_video", "story": "instagram_story", "newsletter": "newsletter", "dm": "dm_outreach"}.get(kind, "x_post")


def platform_actions(kind: str, caption: str, link: str, socials: Dict[str, str]) -> List[Tuple[str, str]]:
    """(label, url) buttons that open the right place to publish."""
    tweet = (caption or "").strip()
    if len(tweet) > 230:
        tweet = tweet[:227].rsplit(" ", 1)[0] + "…"
    x_intent = "https://twitter.com/intent/tweet?" + urllib.parse.urlencode({"text": f"{tweet}\n\n{link}"})
    ig = socials.get("instagram") or "https://www.instagram.com/"
    yt_studio = "https://studio.youtube.com/"
    tiktok = "https://www.tiktok.com/upload"
    if kind == "video":
        return [("Upload to YouTube Shorts", yt_studio), ("Post as Instagram Reel", ig), ("Upload to TikTok", tiktok)]
    if kind == "story":
        return [("Open Instagram Stories", ig), ("Open TikTok", tiktok)]
    if kind == "newsletter" or kind == "dm":
        return []
    return [("Post on X", x_intent), ("Open Instagram", ig), ("YouTube Community", yt_studio)]


def _split_sections(text: str, pattern: str) -> List[Tuple[str, str]]:
    parts = re.split(pattern, text or "", flags=re.IGNORECASE | re.MULTILINE)
    out = []
    if len(parts) <= 1:
        return out
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1].strip()))
    return out


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _nl2br(s: str) -> str:
    return _esc(s).replace("\n", "<br>")


# ── Attachments ──────────────────────────────────────────────────────────────────────────────
def _absolute(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("data:"):
        return url
    if url.startswith("http"):
        return url
    base = (getattr(settings, "BACKEND_URL", "") or "https://creator-forge-backend-jj1y.onrender.com").rstrip("/")
    return f"{base}/{url.lstrip('/')}"


def fetch_attachment(url: Optional[str], filename: str, mime: str, max_bytes: int) -> Optional[Dict[str, Any]]:
    url = _absolute(url)
    if not url:
        return None
    try:
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            data = base64.b64decode(b64)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "CoLaunch/1.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                length = int(resp.headers.get("Content-Length") or 0)
                if length and length > max_bytes:
                    return None
                data = resp.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            return None
        return {"filename": filename, "content": data, "mime": mime}
    except Exception as e:
        logger.info(f"[Post kit] Attachment skipped ({filename}): {e}")
        return None


# ── Email template ───────────────────────────────────────────────────────────────────────────
def build_post_kit_email(
    *,
    creator_name: str,
    product_name: str,
    product_tagline: str,
    task: Dict[str, Any],
    day_num: int,
    total_days: int,
    caption: str,
    image_url: Optional[str],
    video_url: Optional[str],
    video_thumb_url: Optional[str],
    link: str,
    socials: Dict[str, str],
    upcoming: List[Dict[str, Any]],
    accent: str = CRIMSON,
    send_label: str = "",
    attached: List[str] = None,
) -> Tuple[str, str]:
    """Return (html, text) for the creator's daily post kit."""
    kind = channel_kind(task)
    first = (creator_name or "there").split(" ")[0]
    title = task.get("title") or "Today's post"
    channel = task.get("channel") or {"video": "Short video", "story": "Instagram Stories", "newsletter": "Newsletter", "dm": "Direct messages"}.get(kind, "Social post")
    effort = task.get("effort") or "~10 mins"
    description = task.get("description") or ""
    attached = attached or []
    accent = accent if re.match(r"^#[0-9a-fA-F]{6}$", accent or "") else CRIMSON
    serif = "'Instrument Serif', Georgia, 'Times New Roman', serif"
    sans = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

    def label(txt: str) -> str:
        return f'<p style="margin:0 0 8px;font:700 11px/1.4 {sans};letter-spacing:1.2px;text-transform:uppercase;color:#71717a;">{_esc(txt)}</p>'

    def _is_light(hexcol: str) -> bool:
        n = int(hexcol[1:], 16)
        r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
        return (0.299 * r + 0.587 * g + 0.114 * b) > 160

    accent_ink = INK if _is_light(accent) else "#ffffff"

    def btn(text: str, url: str, primary: bool = False) -> str:
        bg = accent if primary else "#ffffff"
        fg = accent_ink if primary else INK
        bd = accent if primary else "#d4d4d8"
        return (f'<a href="{_esc(url)}" target="_blank" style="display:inline-block;margin:0 6px 8px 0;padding:12px 18px;'
                f'background:{bg};color:{fg};border:1px solid {bd};font:700 13px/1 {sans};text-decoration:none;">{_esc(text)}</a>')

    # Media block
    media = ""
    if video_url:
        thumb = _absolute(video_thumb_url) or _absolute(image_url)
        thumb_html = (f'<img src="{_esc(thumb)}" alt="Video preview" width="536" style="display:block;width:100%;max-width:536px;height:auto;border:0;">'
                      if thumb and not thumb.startswith("data:") else
                      f'<div style="padding:70px 0;background:{INK};color:#fff;text-align:center;font:700 15px {sans};">&#9654;&nbsp; Play video</div>')
        media += f'''
        <tr><td style="padding:0 32px 8px;">
          {label("Your video")}
          <a href="{_esc(_absolute(video_url))}" target="_blank" style="text-decoration:none;display:block;border:1px solid #e4e4e7;">{thumb_html}</a>
          <p style="margin:8px 0 0;font:13px/1.5 {sans};color:#52525b;">{"Attached to this email as an MP4. " if "video" in attached else ""}<a href="{_esc(_absolute(video_url))}" style="color:{accent};font-weight:700;">Download the video</a></p>
        </td></tr>'''
    if image_url and kind != "video":
        img_abs = _absolute(image_url)
        if img_abs and not img_abs.startswith("data:"):
            media += f'''
        <tr><td style="padding:0 32px 8px;">
          {label("Your graphic")}
          <img src="{_esc(img_abs)}" alt="Post graphic" width="536" style="display:block;width:100%;max-width:536px;height:auto;border:1px solid #e4e4e7;">
          <p style="margin:8px 0 0;font:13px/1.5 {sans};color:#52525b;">{"Attached to this email as a PNG. " if "image" in attached else ""}<a href="{_esc(img_abs)}" style="color:{accent};font-weight:700;">Download the graphic</a></p>
        </td></tr>'''

    # Copy block, shaped for the channel
    copy_html = ""
    text_copy = caption or description
    if kind == "newsletter":
        m = re.match(r"\s*subject\s*:\s*(.+)", caption or "", flags=re.IGNORECASE)
        subject = m.group(1).strip() if m else f"Something I'm building: {product_name}"
        body = re.sub(r"^\s*subject\s*:.*\n?", "", caption or "", count=1, flags=re.IGNORECASE).strip()
        copy_html = f'''
          {label("Newsletter to send")}
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;">
            <tr><td style="padding:12px 16px;background:#fafafa;border-bottom:1px solid #e4e4e7;font:13px/1.5 {sans};color:#52525b;">
              <strong style="color:{INK};">Subject:</strong> {_esc(subject)}</td></tr>
            <tr><td style="padding:18px 16px;font:15px/1.7 Georgia,serif;color:#27272a;">{_nl2br(body)}</td></tr>
          </table>'''
    elif kind == "story":
        frames = _split_sections(caption, r"^\s*(STORY\s*\d+[^\n]*)\n") or [("Story", caption)]
        rows = "".join(
            f'''<tr><td style="padding:12px 14px;border-bottom:1px solid #e4e4e7;vertical-align:top;width:34px;">
                  <span style="display:inline-block;width:26px;height:26px;line-height:26px;text-align:center;background:{accent};color:{accent_ink};font:700 12px {sans};">{i + 1}</span></td>
                <td style="padding:12px 14px 12px 0;border-bottom:1px solid #e4e4e7;font:14px/1.6 {sans};color:#27272a;">
                  <strong style="display:block;color:{INK};margin-bottom:2px;">{_esc(h)}</strong>{_nl2br(b)}</td></tr>'''
            for i, (h, b) in enumerate(frames[:6]))
        copy_html = f'{label("Story frames")}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;border-bottom:0;">{rows}</table>'
    elif kind == "video":
        beats = _split_sections(caption, r"^\s*((?:HOOK|PROBLEM|SOLUTION|DEMO|CTA)[^\n]*)\n") or [("Script", caption)]
        rows = "".join(
            f'''<tr><td style="padding:12px 14px;border-bottom:1px solid #e4e4e7;font:14px/1.6 {sans};color:#27272a;">
                  <strong style="display:block;color:{accent};font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-bottom:3px;">{_esc(h)}</strong>{_nl2br(b)}</td></tr>'''
            for h, b in beats[:6])
        copy_html = f'{label("Script to film")}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;border-bottom:0;">{rows}</table>'
    else:
        copy_html = f'''
          {label("Caption to post" if kind == "post" else "Message to send")}
          <div style="padding:18px;background:#fafafa;border:1px solid #e4e4e7;border-left:3px solid {accent};font:15px/1.65 {sans};color:#18181b;white-space:pre-wrap;">{_nl2br(text_copy)}</div>
          <p style="margin:8px 0 0;font:12px/1.5 {sans};color:#71717a;">Press and hold (or select) to copy.</p>'''

    actions = platform_actions(kind, caption, link, socials)
    actions_html = "".join(btn(t, u, i == 0) for i, (t, u) in enumerate(actions))

    social_row = " &nbsp;·&nbsp; ".join(
        f'<a href="{_esc(u)}" style="color:{INK};font-weight:700;text-decoration:none;">{n}</a>'
        for n, u in (("YouTube", socials.get("youtube")), ("Instagram", socials.get("instagram")), ("X", socials.get("x")), ("TikTok", socials.get("tiktok")))
        if u
    )

    up_rows = "".join(
        f'''<tr><td style="padding:10px 0;border-top:1px solid #f4f4f5;font:13px/1.4 {sans};color:#52525b;width:64px;">Day {_esc(t.get("day"))}</td>
             <td style="padding:10px 0;border-top:1px solid #f4f4f5;font:13px/1.4 {sans};color:{INK};font-weight:600;">{_esc(t.get("title"))}
             <span style="color:#a1a1aa;font-weight:400;">&nbsp;· {_esc(t.get("channel") or "")}</span></td></tr>'''
        for t in upcoming[:3])

    steps = {
        "video": ["Watch the video above or film your own with the script.", "Upload it as a Short, Reel or TikTok.", "Put the link in your description or bio."],
        "story": ["Post the frames in order.", "Add the poll and link stickers where marked.", "Use the tracking link on the link sticker."],
        "newsletter": ["Paste the subject and body into your email tool.", "Keep the link exactly as written.", "Send it to your list."],
        "dm": ["Pick 5 to 10 engaged followers.", "Personalise the first line for each.", "Send, and reply to anyone who asks a question."],
    }.get(kind, ["Copy the caption.", "Attach the graphic.", "Publish, and pin it if you can."])
    steps_html = "".join(
        f'<tr><td style="padding:6px 10px 6px 0;vertical-align:top;font:700 13px/1.5 {sans};color:{accent};width:18px;">{i + 1}</td>'
        f'<td style="padding:6px 0;font:14px/1.5 {sans};color:#3f3f46;">{_esc(s)}</td></tr>'
        for i, s in enumerate(steps))

    html_doc = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>Day {day_num}: {_esc(title)}</title></head>
<body style="margin:0;padding:0;background:#f4f4f5;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">Day {day_num} of {total_days} · {_esc(channel)} · about {_esc(effort)}. Everything is ready to post.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;"><tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e4e4e7;">
  <tr><td style="background:{INK};padding:22px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font:700 14px {sans};color:#ffffff;letter-spacing:.2px;"><span style="display:inline-block;width:10px;height:10px;background:{accent};margin-right:8px;"></span>{_esc(product_name)}</td>
      <td align="right" style="font:600 12px {sans};color:#a1a1aa;">Day {day_num} of {total_days}</td>
    </tr></table>
  </td></tr>
  <tr><td style="height:3px;background:{accent};line-height:3px;font-size:3px;">&nbsp;</td></tr>
  <tr><td style="padding:30px 32px 6px;">
    <p style="margin:0 0 10px;font:600 13px {sans};color:#71717a;">Hi {_esc(first)}, here's today's post{(" · " + _esc(send_label)) if send_label else ""}</p>
    <h1 style="margin:0 0 12px;font:400 34px/1.12 {serif};color:{INK};">{_esc(title)}</h1>
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      <td style="padding:5px 10px;background:{INK};color:#fff;font:700 11px {sans};letter-spacing:.6px;text-transform:uppercase;">{_esc(channel)}</td>
      <td style="width:6px;"></td>
      <td style="padding:5px 10px;border:1px solid #e4e4e7;color:#52525b;font:600 11px {sans};">{_esc(effort)}</td>
    </tr></table>
    {f'<p style="margin:16px 0 0;font:15px/1.6 {sans};color:#3f3f46;">{_esc(description)}</p>' if description else ""}
  </td></tr>
  <tr><td style="height:22px;"></td></tr>
  {media}
  <tr><td style="padding:16px 32px 4px;">{copy_html}</td></tr>
  <tr><td style="padding:22px 32px 6px;">
    {label("Publish")}
    {actions_html}
    {btn("Copy tracking link", link, not actions)}
    <p style="margin:6px 0 0;font:12px/1.5 {sans};color:#71717a;">Tracking link: <a href="{_esc(link)}" style="color:{accent};word-break:break-all;">{_esc(link)}</a></p>
  </td></tr>
  <tr><td style="padding:22px 32px 6px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fafafa;border:1px solid #e4e4e7;"><tr><td style="padding:16px 18px;">
      {label("Three steps")}
      <table role="presentation" cellpadding="0" cellspacing="0">{steps_html}</table>
    </td></tr></table>
  </td></tr>
  {f'<tr><td style="padding:22px 32px 6px;">{label("Coming up")}<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{up_rows}</table></td></tr>' if up_rows else ""}
  {f'<tr><td style="padding:18px 32px 0;font:13px/1.6 {sans};color:#52525b;">Your channels: {social_row}</td></tr>' if social_row else ""}
  <tr><td style="padding:28px 32px;">
    <p style="margin:0;font:13px/1.6 {sans};color:#71717a;border-top:1px solid #e4e4e7;padding-top:18px;">
      Sent by CoLaunch for {_esc(creator_name or "you")} · {_esc(product_name)}{(" — " + _esc(product_tagline)) if product_tagline else ""}.<br>
      Post kits arrive once a day while the campaign runs. Reply to this email if anything looks off.
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''

    text = "\n".join(filter(None, [
        f"Day {day_num} of {total_days}: {title}",
        f"{channel} · {effort}",
        description,
        "",
        "COPY:",
        caption or description,
        "",
        f"Tracking link: {link}",
        f"Video: {_absolute(video_url)}" if video_url else "",
        f"Graphic: {_absolute(image_url)}" if image_url and not str(image_url).startswith("data:") else "",
        "",
        "Publish: " + " | ".join(f"{t}: {u}" for t, u in actions) if actions else "",
    ]))
    return html_doc, text


# ── Scheduler tick ───────────────────────────────────────────────────────────────────────────
_migrated = False
_backoff: Dict[str, float] = {}


def _migrate_legacy_flags():
    """One-off: projects that had auto-delivery enabled inside the kit get a delivery record."""
    global _migrated
    if _migrated:
        return
    _migrated = True
    projects = _coll("co_launch_projects")
    delivery = _coll("campaign_delivery")
    if projects is None or delivery is None:
        return
    try:
        for d in projects.find({}, {"campaignKit": 1, "campaign_kit": 1, "creatorEmail": 1, "creator_email": 1}):
            kit = d.get("campaignKit") or d.get("campaign_kit") or {}
            auto = kit.get("autonomousEmailDelivery") if isinstance(kit, dict) else None
            if isinstance(auto, dict) and auto.get("enabled") and delivery.find_one({"_id": str(d["_id"])}) is None:
                save_delivery_config(str(d["_id"]), {
                    "enabled": True,
                    "recipientEmail": auto.get("recipientEmail") or d.get("creatorEmail") or d.get("creator_email"),
                    "timezone": auto.get("timezone") or kit.get("creatorTimezone") or "UTC",
                    "country": auto.get("country") or "",
                    "sendHour": DEFAULT_SEND_HOUR,
                })
    except Exception as e:
        logger.debug(f"[Post kit] legacy migration note: {e}")


def run_delivery_tick() -> int:
    """Send today's post kit for every project that is due. Returns the number of emails sent."""
    import time
    _migrate_legacy_flags()
    delivery = _coll("campaign_delivery")
    dispatches = _coll("campaign_dispatches")
    if delivery is None or dispatches is None:
        return 0
    sent_count = 0
    try:
        configs = list(delivery.find({"enabled": True}))
    except Exception:
        return 0
    for cfg in configs:
        pid = str(cfg["_id"])
        if _backoff.get(pid, 0) > time.time():
            continue
        tz_name = cfg.get("timezone") or "UTC"
        now = local_now(tz_name)
        if now.hour < int(cfg.get("sendHour", DEFAULT_SEND_HOUR)):
            continue
        day_id = f"{pid}:day:{now.date().isoformat()}"
        if dispatches.find_one({"_id": day_id}) is not None:
            continue
        try:
            from app.routers.projects import _find_mongo_project
            doc = _find_mongo_project(pid)
        except Exception:
            doc = None
        if not doc:
            continue
        kit = doc.get("campaignKit") or doc.get("campaign_kit") or (doc.get("metadataInfo") or {}).get("campaign_kit") or {}
        schedule = list(kit.get("postingSchedule") or [])
        task = next_pending_task(schedule, sent_task_log(pid))
        if not task:
            continue
        # Claim the day first so two workers can never send twice
        try:
            dispatches.insert_one({"_id": day_id, "kind": "day", "project_id": pid, "localDate": now.date().isoformat(),
                                   "task_id": str(task.get("id")), "createdAt": datetime.utcnow().isoformat()})
        except Exception:
            continue
        try:
            from app.services.autonomous_campaign_dispatcher import dispatch_campaign_post_email
            dispatch_campaign_post_email(project_id=pid, task_id=str(task.get("id")),
                                         recipient_email=cfg.get("recipientEmail") or None,
                                         caller="autonomous_daily")
            sent_count += 1
        except Exception as e:
            logger.warning(f"[Post kit] Daily send failed for {pid}: {e}")
            try:
                dispatches.delete_one({"_id": day_id})
            except Exception:
                pass
            _backoff[pid] = time.time() + 30 * 60  # retry in 30 minutes
    return sent_count
