import app.ssl_patch  # Prevents macOS LibreSSL 2.8.3 TLS 1.3 session ticket double-free crash
from typing import Optional, List, Dict, Any, Union
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, Response, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
from pydantic import BaseModel

from app.config import settings
from app.database import init_db
from app.routers import (
    creators, discovery, outreach, campaigns, decks, suppression, analytics, audit,
    public_portal, content_calendar, autonomous, projects, workflow, upload, niches,
    support, meta
)
from app.routers import agent as agent_router
from app.routers import auth as auth_router

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Internal Creator Forge ops pipeline — not for public use",
)

# ── Compress JSON and text responses to save up to 80% outbound bandwidth ──
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ── CORS (allow app.co-launch.app + Vite dev server + Vercel frontend) ─────
_cors_origins = [
    "https://app.co-launch.app",
    "https://app.co-launch.app/",
    "http://app.co-launch.app",
    "https://co-launch.app",
    "https://co-launch.app/",
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "https://creator-forge-frontend.vercel.app",
    "https://creator-forge-frontend.vercel.app/",
    "https://colaunch-web.vercel.app",
    "https://colaunch-web.vercel.app/",
    "https://colaunch-2.vercel.app",
    "https://colaunch-2.vercel.app/",
]
if settings.FRONTEND_URL:
    clean_fe = settings.FRONTEND_URL.rstrip("/")
    if clean_fe not in _cors_origins:
        _cors_origins.extend([clean_fe, f"{clean_fe}/"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://((.*\.)?co-launch\.app|(.*\.)?vercel\.app)/?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

def get_cors_origin(request: Optional[Request] = None) -> str:
    """Helper to return dynamic matched CORS origin or canonical app domain."""
    if request:
        orig = request.headers.get("origin")
        if orig:
            return orig
    return settings.FRONTEND_URL or "https://app.co-launch.app"

import asyncio
import logging
logger = logging.getLogger("creator_forge")
from app.services.inbox_poller import start_poller_loop, stop_poller_loop
from app.services.support_inbox_poller import start_support_poller_loop, stop_support_poller_loop
from app.services.autonomous_outreach import (
    start_autonomous_scheduler_loop, stop_autonomous_scheduler_loop,
    start_followup_scheduler_loop, stop_followup_scheduler_loop,
)
from app.services.autonomous_campaign_dispatcher import (
    start_campaign_schedule_loop, stop_campaign_schedule_loop,
)

# ── Database init ────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    await asyncio.to_thread(init_db)
    # Ensure autonomous table is registered
    from app.models.autonomous_campaign import AutonomousCampaign
    # Start the IMAP poller loop with reduced frequency (300s) to minimize DB operations
    asyncio.create_task(start_poller_loop(interval_seconds=300))
    # Start the Customer Support IMAP reply monitor (every 15s)
    asyncio.create_task(start_support_poller_loop(interval_seconds=15))
    # Autonomous scheduler loops are disabled by default for human control
    if settings.ENABLE_AUTONOMOUS_BACKGROUND_SCHEDULER:
        logger.info("[Autonomous Scheduler] Background loops enabled via configuration.")
        asyncio.create_task(start_autonomous_scheduler_loop(interval_hours=24))
        asyncio.create_task(start_followup_scheduler_loop())
    else:
        logger.info("[Autonomous Scheduler] Background batch loops disabled (Human Mode active).")

    # Start the autonomous 24-hour campaign post kit dispatcher loop
    asyncio.create_task(start_campaign_schedule_loop(check_interval_seconds=60))

    # Initialize MongoDB connection and verify collections
    try:
        from app.mongodb import check_mongo_connection, seed_from_backup_if_empty
        mongo_info = check_mongo_connection()
        if mongo_info.get("status") == "connected":
            logger.info(f"[MongoDB] Cluster connected: {mongo_info.get('database')}. Syncing collections...")
            await asyncio.to_thread(seed_from_backup_if_empty)
        else:
            logger.info(f"[MongoDB] Status: {mongo_info.get('status')}")
    except Exception as _mongo_err:
        logger.warning(f"[MongoDB] Startup check notice: {_mongo_err}")

@app.on_event("shutdown")
def shutdown():
    stop_poller_loop()
    stop_support_poller_loop()
    stop_autonomous_scheduler_loop()
    stop_followup_scheduler_loop()
    stop_campaign_schedule_loop()


# ── Static files + templates (only mount if the directory exists) ─────────────
BASE_DIR = Path(__file__).resolve().parent.parent
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(parents=True, exist_ok=True)
(_static_dir / "generated").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

_frontend_static = BASE_DIR.parent / "frontend" / "static"
_template_dir = BASE_DIR.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(_template_dir)) if _template_dir.exists() else None


# ── API Routers ──────────────────────────────────────────────────────────────
app.include_router(creators.router)
app.include_router(discovery.router)
app.include_router(outreach.router)
app.include_router(campaigns.router)
app.include_router(decks.router)
app.include_router(suppression.router)
app.include_router(analytics.router)
app.include_router(audit.router)
app.include_router(agent_router.router)
app.include_router(public_portal.router)
app.include_router(content_calendar.router)
app.include_router(auth_router.router)
app.include_router(autonomous.router)
app.include_router(projects.router)
app.include_router(workflow.router)
app.include_router(upload.router)
app.include_router(niches.router)
app.include_router(support.router)
app.include_router(meta.router)



# ── MongoDB API endpoints ───────────────────────────────────────────────────
@app.get("/api/mongodb/status")
def mongodb_status_endpoint():
    """Returns MongoDB connectivity status, database name, and collection list."""
    from app.mongodb import check_mongo_connection
    return check_mongo_connection()


@app.post("/api/mongodb/sync")
def mongodb_sync_endpoint():
    """Reports MongoDB status (Atlas is single source of truth)."""
    from app.mongodb import check_mongo_connection
    status = check_mongo_connection()
    return {"status": "success", "message": "MongoDB Atlas is the single source of truth.", "details": status}
# ── Analytics alias (/api/analytics/summary used by ops CampaignStats) ──────
@app.get("/analytics/summary")
@app.get("/api/analytics/summary")
def analytics_summary_alias():
    """Ops dashboard analytics — counts across all pipeline stages directly from MongoDB."""
    from app.mongodb import get_collection
    try:
        c_coll = get_collection("creators")
        o_coll = get_collection("outreach_messages")
        r_coll = get_collection("replies")
        t_coll = get_collection("threads")

        total_scraped = c_coll.count_documents({}) if c_coll is not None else 0
        total_qualified = c_coll.count_documents({"status": "qualified"}) if c_coll is not None else 0
        total_sent = o_coll.count_documents({"status": "sent"}) if o_coll is not None else 0
        total_replies = r_coll.count_documents({}) if r_coll is not None else 0
        total_converted = t_coll.count_documents({"status": "converted"}) if t_coll is not None else 0

        return {
            "total_scraped":       total_scraped,
            "total_qualified":     total_qualified,
            "total_outreach_sent": total_sent,
            "total_replies":       total_replies,
            "total_interested":    0,
            "total_converted":     total_converted,
            "reply_rate":          round(total_replies / total_sent * 100, 1) if total_sent else 0,
            "open_rate":           0,
            "campaigns":           [],
        }
    except Exception as e:
        return {
            "total_scraped": 0,
            "total_qualified": 0,
            "total_outreach_sent": 0,
            "total_replies": 0,
            "total_interested": 0,
            "total_converted": 0,
            "reply_rate": 0,
            "open_rate": 0,
            "campaigns": [],
            "error": str(e),
        }


# ── UI Routes ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/login", response_class=HTMLResponse)
@app.get("/signup", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def index(request: Request):
    if templates is None:
        return HTMLResponse("<h1>Creator Forge API</h1><p>Visit <a href='/docs'>/docs</a> for the API.</p>")
    return templates.TemplateResponse("app.html", {"request": request})


# ── UI Routes (legacy Jinja2 — only active if frontend/templates dir exists) ──
def _tpl(request: Request, name: str, **ctx):
    if templates is None:
        return HTMLResponse(f"<p>API-only mode. Visit <a href='/docs'>/docs</a>.</p>", status_code=200)
    return templates.TemplateResponse(name, {"request": request, **ctx})

@app.get("/discovery",             response_class=HTMLResponse)
def view_discovery(request: Request):         return _tpl(request, "discovery.html")

@app.get("/leads",                 response_class=HTMLResponse)
def view_leads(request: Request):             return _tpl(request, "qualified_leads.html")

@app.get("/creators/{creator_id}", response_class=HTMLResponse)
def view_creator(request: Request, creator_id: str): return _tpl(request, "creator_detail.html", creator_id=creator_id)

@app.get("/review",                response_class=HTMLResponse)
def view_review(request: Request):            return _tpl(request, "review_queue.html")

@app.get("/outreach-editor",       response_class=HTMLResponse)
def view_outreach_editor(request: Request):   return _tpl(request, "outreach_editor.html")

@app.get("/send-queue",            response_class=HTMLResponse)
def view_send_queue(request: Request):        return _tpl(request, "send_queue.html")

@app.get("/inbox",                 response_class=HTMLResponse)
def view_inbox(request: Request):             return _tpl(request, "reply_inbox.html")

@app.get("/analytics",             response_class=HTMLResponse)
def view_analytics(request: Request):         return _tpl(request, "analytics.html")

@app.get("/audit",                 response_class=HTMLResponse)
def view_audit(request: Request):             return _tpl(request, "audit_log.html")

@app.get("/decks/{deck_id}/preview", response_class=HTMLResponse)
def view_deck(request: Request, deck_id: str): return _tpl(request, "deck_preview.html", deck_id=deck_id)



# ── Settings API ─────────────────────────────────────────────────────────────
@app.get("/api/settings")
def get_settings():
    """Return current runtime-editable settings (complete secrets for local workspace)."""
    return {
        "anthropic_api_key":  settings.ANTHROPIC_API_KEY,
        "openai_api_key":     settings.OPENAI_API_KEY,
        "gemini_api_key":     settings.GEMINI_API_KEY,
        "active_ai_provider": settings.ACTIVE_AI_PROVIDER,
        "apify_api_key":      settings.APIFY_API_KEY,
        "resend_api_key":     settings.RESEND_API_KEY,
        "sendgrid_api_key":   settings.SENDGRID_API_KEY,
        "brevo_api_key":      settings.BREVO_API_KEY,
        "from_email":         settings.FROM_EMAIL,
        "from_name":          settings.FROM_NAME,
        "google_email":       settings.GOOGLE_EMAIL,
        "google_app_password": settings.GOOGLE_APP_PASSWORD,
        "ai_model":           settings.AI_MODEL,
        "apify_configured":   bool(settings.APIFY_API_KEY),
        "email_configured":   bool(settings.RESEND_API_KEY or settings.SENDGRID_API_KEY or settings.BREVO_API_KEY or (settings.GOOGLE_EMAIL and settings.GOOGLE_APP_PASSWORD)),
        "ai_configured":      bool(settings.ANTHROPIC_API_KEY or settings.GEMINI_API_KEY or settings.OPENAI_API_KEY),
    }


@app.post("/api/settings")
def update_settings(body: dict):
    """Update runtime-editable settings and persist to .env."""
    from pathlib import Path as _Path
    import re as _re

    allowed = {
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key":    "OPENAI_API_KEY",
        "gemini_api_key":    "GEMINI_API_KEY",
        "active_ai_provider": "ACTIVE_AI_PROVIDER",
        "apify_api_key":     "APIFY_API_KEY",
        "resend_api_key":    "RESEND_API_KEY",
        "sendgrid_api_key":  "SENDGRID_API_KEY",
        "brevo_api_key":     "BREVO_API_KEY",
        "from_email":        "FROM_EMAIL",
        "from_name":         "FROM_NAME",
        "google_email":       "GOOGLE_EMAIL",
        "google_app_password": "GOOGLE_APP_PASSWORD",
    }
    updated = []
    env_updates = {}
    for field, attr in allowed.items():
        val = body.get(field)
        if val is not None:
            setattr(settings, attr, val)
            env_updates[attr] = val
            updated.append(field)

    if env_updates:
        from app.config import BASE_DIR
        env_path = BASE_DIR / ".env"
        existing = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        existing.update(env_updates)
        lines = ["# Creator Forge environment variables — auto-managed"]
        for k, v in existing.items():
            lines.append(f"{k}={v}")
        env_path.write_text("\n".join(lines) + "\n")

    return {"updated": updated}


# ── Fix bad avatar URLs in DB (one-time cleanup) ─────────────────────────────
@app.post("/api/admin/fix-avatars")
def fix_avatars():
    """Decode unicode-escaped avatar URLs already stored in the DB."""
    from app.database import get_db as _get_db
    from app.models.creator import Creator as _Creator
    from app.services.scraper import _clean_url
    db = next(_get_db())
    fixed = 0
    for c in db.query(_Creator).all():
        if c.avatar_url and ("\\u" in c.avatar_url or "\\/" in c.avatar_url):
            c.avatar_url = _clean_url(c.avatar_url)
            fixed += 1
    db.commit()
    return {"fixed": fixed}


# ── Avatar proxy (bypasses browser CORS on yt3.ggpht.com etc.) ───────────────
@app.get("/api/proxy/avatar")
def proxy_avatar(url: str):
    """Fetch a remote avatar image server-side and return it to avoid CORS blocks."""
    import httpx
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.youtube.com/",
    }
    try:
        r = httpx.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
        content_type = r.headers.get("content-type", "image/jpeg")
        return Response(
            content=r.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            },
        )
    except Exception:
        return Response(status_code=404)


@app.get("/api/proxy/youtube-feed")
def proxy_youtube_feed(channel_id: Optional[str] = None, handle: Optional[str] = None):
    """Fetch live YouTube channel RSS feed / videos server-side and return JSON."""
    from app.services.scraper import fetch_youtube_channel_videos
    target = handle or channel_id
    if not target:
        return {"success": False, "error": "channel_id or handle is required", "videos": []}
    videos = fetch_youtube_channel_videos(target, limit=12)
    return {
        "success": True,
        "target": target,
        "count": len(videos),
        "videos": videos,
        "recent_posts": videos,
        "recentPosts": videos
    }



# ── Signup Welcome Email API ──────────────────────────────────────────────────
class SignupEmailRequest(BaseModel):
    email: str
    username: str
    sendgrid_api_key: str = ""

@app.post("/api/auth/signup-email")
def send_signup_email(req: SignupEmailRequest, request: Request):
    """Send a successful account creation email using Google SMTP."""
    import logging
    from app.integrations.email_provider import email_provider

    if not email_provider.is_configured():
        logging.warning("Email provider not fully configured. Simulating welcome email success.")
        return {
            "status": "simulated",
            "message": "Email configurations not set. Onboarding welcome email simulation succeeded.",
            "recipient": req.email
        }

    # Determine base url (check env first, then dynamic origin, fallback to canonical subdomain)
    import os
    base_url = os.getenv("BASE_URL", "").rstrip('/')
    if not base_url:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if "localhost:8000" in base_url or "127.0.0.1:8000" in base_url or "creator-forge" in base_url:
                base_url = "https://app.co-launch.app"
        else:
            base_url = "https://app.co-launch.app"

    from app.services.email_template import render_welcome_signup_email
    body_html, body_text = render_welcome_signup_email(
        username=req.username,
        base_url=base_url
    )
    subject = "Welcome to CoLaunch — Your Creator Software Platform is Ready!"

    try:
        res = email_provider.send(
            to_email=req.email,
            subject=subject,
            body_html=body_html,
            body_text=body_text
        )
        return {"status": "sent", "message_id": res.get("message_id", "unknown")}
    except Exception as e:
        logging.error(f"Google SMTP sending error: {str(e)}")
        return {"status": "error", "message": f"SMTP delivery failed: {str(e)}"}


# ── Download Keys PDF API ─────────────────────────────────────────────────────
class DownloadKeysRequest(BaseModel):
    apify_token: str = ""
    youtube_api_key: str = ""
    gemini_api_key: str = ""
    together_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

@app.post("/api/settings/download-keys-pdf")
def download_keys_pdf(req: DownloadKeysRequest):
    """Generate a clean, structured PDF of the user's transient API keys."""
    from fastapi.responses import StreamingResponse
    import io
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    from datetime import datetime

    class StyledPDF(FPDF):
        def header(self):
            # Sleek slate banner
            self.set_fill_color(15, 23, 42)  # slate-900
            self.rect(0, 0, 210, 35, "F")
            
            # Header Title
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", size=16, style="B")
            self.set_xy(10, 10)
            self.cell(190, 8, text="CREATOR FORGE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
            
            # Header Subtitle
            self.set_font("Helvetica", size=9)
            self.set_text_color(148, 163, 184)  # slate-400
            self.cell(190, 5, text="SECURITY KEY BACKUP | CONFIDENTIAL", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
            
            self.set_y(45)

        def footer(self):
            self.set_y(-25)
            self.set_font("Helvetica", size=8, style="I")
            self.set_text_color(148, 163, 184)
            # Footer dividing line
            self.set_draw_color(226, 232, 240)
            self.line(10, self.get_y(), 200, self.get_y())
            self.set_y(self.get_y() + 2)
            
            self.cell(100, 10, text=f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} | Creator Forge Auth Layer", new_x=XPos.RIGHT, new_y=YPos.TOP, align="L")
            self.cell(90, 10, text=f"Page {self.page_no()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")

    pdf = StyledPDF()
    pdf.add_page()
    pdf.ln(5)
    
    # Intro/Security description
    pdf.set_text_color(71, 85, 105)  # slate-600
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(
        190, 5, 
        text="Security Notice: For your privacy and protection, Creator Forge enforces in-memory transient key storage. "
             "These keys are never stored in databases or local browser caches (localStorage). "
             "Keep this PDF backup secure. You can copy-paste these keys when launching the console in a new session."
    )
    pdf.ln(8)
    
    keys_data = [
        ("Apify Token", req.apify_token),
        ("YouTube API Key", req.youtube_api_key),
        ("Google Gemini Key", req.gemini_api_key),
        ("OpenAI Key", req.openai_api_key),
        ("Anthropic Key", req.anthropic_api_key),
        ("Together.ai Key", req.together_api_key)
    ]
    
    for label, val in keys_data:
        # Key Label
        pdf.set_text_color(15, 23, 42)  # slate-900
        pdf.set_font("Helvetica", size=10, style="B")
        pdf.cell(190, 6, text=label, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        # Styled Box container for the Key value
        pdf.set_fill_color(248, 250, 252)  # slate-50
        pdf.set_draw_color(226, 232, 240)  # slate-200
        pdf.set_text_color(30, 41, 59)     # slate-800
        pdf.set_font("Courier", size=9)
        
        display_val = val.strip() if val.strip() else "[Not Configured]"
        pdf.multi_cell(190, 8, text=display_val, border=1, fill=True)
        pdf.ln(4)
        
    pdf_bytes = bytes(pdf.output())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=creator_forge_api_keys.pdf"}
    )


# ── Authentication & Synchronization APIs ─────────────────────────────────────
from typing import Any, Dict, Optional
from app.database import get_db
from sqlalchemy.orm import Session

class SignupRequest(BaseModel):
    username: str
    email: Optional[str] = None
    password: Optional[str] = None
    recaptcha_token: Optional[str] = None
    creator_data: Optional[Dict[str, Any]] = None
    calendar_data: Optional[Dict[str, Any]] = None
    launch_pack_data: Optional[Dict[str, Any]] = None
    studio_data: Optional[Dict[str, Any]] = None
    chat_data: Optional[Dict[str, Any]] = None

class LoginRequest(BaseModel):
    identifier: str
    password: Optional[str] = ""

class SendCodeRequest(BaseModel):
    email: str

class VerifyCodeRequest(BaseModel):
    email: str
    code: str

class GoogleAuthRequest(BaseModel):
    code: Optional[str] = None
    access_token: Optional[str] = None
    credential: Optional[str] = None
    redirect_uri: Optional[str] = None

# Storage & verification for 6-digit email authentication codes
_AUTH_CODES: Dict[str, Dict[str, Any]] = {}

def store_auth_code(email: str, code: str, expires_in_seconds: int = 900):
    import time
    from datetime import datetime
    norm_email = email.strip().lower()
    exp = time.time() + expires_in_seconds
    _AUTH_CODES[norm_email] = {"code": code, "expires_at": exp}
    try:
        from app.mongodb import get_collection
        vc_coll = get_collection("verification_codes")
        if vc_coll is not None:
            vc_coll.update_one(
                {"email": norm_email},
                {"$set": {"email": norm_email, "code": code, "expires_at": exp, "updated_at": datetime.utcnow()}},
                upsert=True
            )
    except Exception as e:
        logger.warning(f"[Auth Code] MongoDB write notice: {e}")

def verify_and_consume_code(email: str, code: str) -> bool:
    import time
    norm_email = email.strip().lower()
    clean_code = str(code).strip()
    if not norm_email or not clean_code:
        return False

    # Check in-memory first
    mem = _AUTH_CODES.get(norm_email)
    if mem and time.time() <= mem.get("expires_at", 0) and str(mem.get("code")).strip() == clean_code:
        del _AUTH_CODES[norm_email]
        try:
            from app.mongodb import get_collection
            vc_coll = get_collection("verification_codes")
            if vc_coll is not None:
                vc_coll.delete_one({"email": norm_email})
        except Exception:
            pass
        return True

    # Check MongoDB collection
    try:
        from app.mongodb import get_collection
        vc_coll = get_collection("verification_codes")
        if vc_coll is not None:
            doc = vc_coll.find_one({"email": norm_email})
            if doc and time.time() <= doc.get("expires_at", 0) and str(doc.get("code")).strip() == clean_code:
                vc_coll.delete_one({"email": norm_email})
                if norm_email in _AUTH_CODES:
                    del _AUTH_CODES[norm_email]
                return True
    except Exception as e:
        logger.warning(f"[Auth Code] MongoDB lookup notice: {e}")

    return False


class SyncRequest(BaseModel):
    username: str
    creator_data: Optional[Dict[str, Any]] = None
    calendar_data: Optional[Dict[str, Any]] = None
    launch_pack_data: Optional[Dict[str, Any]] = None
    studio_data: Optional[Dict[str, Any]] = None
    chat_data: Optional[Dict[str, Any]] = None
    youtube_connection: Optional[Dict[str, Any]] = None
    meta_connection: Optional[Dict[str, Any]] = None
    accounts_platforms: Optional[Dict[str, Any]] = None
    community_data: Optional[Dict[str, Any]] = None
    marketing_data: Optional[Dict[str, Any]] = None
    products_data: Optional[Dict[str, Any]] = None

@app.post("/api/auth/signup")
def auth_signup(req: SignupRequest, request: Request):
    """Registers or updates a user profile solely in MongoDB Atlas."""
    from fastapi import HTTPException
    from app.mongodb import get_collection
    from datetime import datetime

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    norm_email = req.email.strip().lower() if (req.email and req.email.strip()) else None

    # Verify Google reCAPTCHA v3 token if provided
    if req.recaptcha_token:
        try:
            import urllib.request
            import urllib.parse
            import json
            import os
            secret = os.getenv("RECAPTCHA_SECRET_KEY", "6Ldo3xEuAAAAAOs-2VVkkcVnEJCvWTdqtDwGjOag")
            if secret:
                v_data = urllib.parse.urlencode({"secret": secret, "response": req.recaptcha_token}).encode()
                v_req = urllib.request.Request(
                    "https://www.google.com/recaptcha/api/siteverify",
                    data=v_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                with urllib.request.urlopen(v_req, timeout=5) as resp:
                    v_res = json.loads(resp.read().decode())
                    if not v_res.get("success"):
                        logger.warning(f"[reCAPTCHA v3 Notice] Siteverify response: {v_res}")
                    else:
                        logger.info(f"[reCAPTCHA v3] Verified interaction. Score: {v_res.get('score')}")
        except Exception as e:
            logger.warning(f"[reCAPTCHA v3 Exception] {e}")

    # Check unique constraints in MongoDB Atlas
    import re
    if norm_email:
        m_existing = u_coll.find_one({
            "email": {"$regex": f"^{re.escape(norm_email)}$", "$options": "i"}
        })
        if m_existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists. Please sign in instead.")

    m_user = u_coll.find_one({"username": req.username})
    now = datetime.utcnow()
    if m_user:
        # If user already registered with a password or email, reject duplicate registration
        if m_user.get("has_password") or m_user.get("password") or m_user.get("email"):
            raise HTTPException(status_code=400, detail="An account with this username or email already exists. Please sign in instead.")

        update_fields = {"updated_at": now}
        if norm_email is not None:
            update_fields["email"] = norm_email
        if req.password:
            update_fields["password"] = req.password
            update_fields["has_password"] = True
        if req.creator_data:
            update_fields["creator_data"] = req.creator_data
        if req.calendar_data:
            update_fields["calendar_data"] = req.calendar_data
        if req.launch_pack_data:
            update_fields["launch_pack_data"] = req.launch_pack_data
        if req.studio_data:
            update_fields["studio_data"] = req.studio_data
        if req.chat_data:
            update_fields["chat_data"] = req.chat_data
        u_coll.update_one({"username": req.username}, {"$set": update_fields})
        return {
            "status": "success",
            "username": req.username,
            "email": norm_email or m_user.get("email") or "",
            "creator_data": req.creator_data or m_user.get("creator_data"),
            "calendar_data": req.calendar_data or m_user.get("calendar_data"),
            "launch_pack_data": req.launch_pack_data or m_user.get("launch_pack_data"),
            "studio_data": req.studio_data or m_user.get("studio_data"),
            "chat_data": req.chat_data or m_user.get("chat_data")
        }

    # Insert new user profile document directly into MongoDB Atlas
    doc = {
        "username": req.username,
        "email": norm_email or "",
        "password": req.password or "",
        "has_password": bool(req.password),
        "creator_data": req.creator_data or {},
        "calendar_data": req.calendar_data or {},
        "launch_pack_data": req.launch_pack_data or {},
        "studio_data": req.studio_data or {},
        "chat_data": req.chat_data or {},
        "created_at": now,
        "updated_at": now
    }
    u_coll.insert_one(doc)

    return {
        "status": "success",
        "username": req.username,
        "email": norm_email or "",
        "creator_data": req.creator_data,
        "calendar_data": req.calendar_data,
        "launch_pack_data": req.launch_pack_data,
        "studio_data": req.studio_data,
        "chat_data": req.chat_data
    }

@app.options("/api/auth/login")
def auth_login_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request):
    from fastapi import HTTPException
    from app.mongodb import get_collection
    import re

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    ident = req.identifier.strip()
    ident_lower = ident.lower()

    # Query MongoDB Atlas user_profiles (exclusive single source of truth)
    u_coll = get_collection("user_profiles")
    m_user = None
    if u_coll is not None:
        m_user = u_coll.find_one({
            "$or": [
                {"email": ident_lower},
                {"username": {"$regex": f"^{re.escape(ident)}$", "$options": "i"}},
                {"username": ident}
            ]
        })

    # If not found in user_profiles, check if registered in creators collection
    if not m_user:
        c_coll = get_collection("creators")
        if c_coll is not None:
            c_doc = c_coll.find_one({
                "$or": [
                    {"email": ident_lower},
                    {"contactEmail": ident_lower},
                    {"handle": {"$regex": f"^{re.escape(ident)}$", "$options": "i"}}
                ]
            })
            if c_doc:
                m_user = {
                    "username": c_doc.get("handle", ident).replace("@", ""),
                    "email": (c_doc.get("email") or c_doc.get("contactEmail") or "").strip().lower(),
                    "password": c_doc.get("password") or "",
                    "creator_data": c_doc,
                    "has_password": bool(c_doc.get("password"))
                }

    has_pwd = bool(m_user and m_user.get("password"))
    user_email = (m_user.get("email") if m_user else None) or (ident if "@" in ident else "")

    if m_user and not has_pwd:
        return JSONResponse(
            status_code=200,
            content={
                "status": "no_password_set",
                "email": user_email,
                "message": "No password is set for this account. Please use an authentication code sent to your email to log in."
            },
            headers=cors_headers
        )

    # If user provided email without password or requested code login
    if not req.password and (m_user or "@" in ident):
        return JSONResponse(
            status_code=200,
            content={
                "status": "code_required",
                "email": user_email,
                "message": "Please authenticate using the email verification code."
            },
            headers=cors_headers
        )

    # Verify password against MongoDB user
    if m_user:
        stored_pwd = m_user.get("password")
        if stored_pwd and stored_pwd == req.password:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "username": m_user.get("username"),
                    "email": m_user.get("email") or "",
                    "creator_data": m_user.get("creator_data"),
                    "calendar_data": m_user.get("calendar_data"),
                    "launch_pack_data": m_user.get("launch_pack_data"),
                    "studio_data": m_user.get("studio_data"),
                    "chat_data": m_user.get("chat_data"),
                    "ai_keys": m_user.get("ai_keys"),
                    "youtube_connection": m_user.get("youtube_connection"),
                    "meta_connection": m_user.get("meta_connection"),
                    "accounts_platforms": m_user.get("accounts_platforms"),
                    "has_password": True
                },
                headers=cors_headers
            )

    return JSONResponse(
        status_code=400,
        content={"status": "error", "detail": "Invalid email/username or password credentials."},
        headers=cors_headers
    )


@app.options("/api/auth/send-code")
def auth_send_code_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/auth/send-code")
def auth_send_code(req: SendCodeRequest, request: Request):
    """Sends a 6-digit authentication code to the user's email for passwordless sign-in and email verification."""
    from app.integrations.email_provider import email_provider
    from app.mongodb import get_collection
    import random
    import time

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    email = req.email.strip().lower()
    if not email or "@" not in email:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "A valid email address is required."},
            headers=cors_headers
        )

    # Generate 6-digit verification code
    code = f"{random.randint(100000, 999999)}"
    store_auth_code(email, code, expires_in_seconds=900)

    display_name = "Creator"
    u_coll = get_collection("user_profiles")
    if u_coll is not None:
        m_user = u_coll.find_one({"email": email})
        if m_user:
            display_name = (m_user.get("creator_data") or {}).get("name") or m_user.get("username") or "Creator"

    import os
    base_url = os.getenv("BASE_URL", "https://app.co-launch.app").rstrip('/')

    from app.services.email_template import render_otp_verification_email
    body_html, body_text = render_otp_verification_email(
        code=code,
        display_name=display_name,
        base_url=base_url
    )
    subject = f"Your CoLaunch Verification Code: {code}"

    try:
        email_provider.send_email(
            to_email=email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            display_name="CoLaunch Auth"
        )
        logger.info(f"[Auth Code] Code successfully sent to {email}")
    except Exception as e:
        logger.warning(f"[Auth Code] Email send warning: {e}")

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": f"Authentication code sent to {email}",
            "email": email
        },
        headers=cors_headers
    )


@app.options("/api/auth/verify-code")
def auth_verify_code_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/auth/verify-code")
def auth_verify_code(req: VerifyCodeRequest, request: Request):
    """Verifies a single-use 6-digit email code and restores workspace state directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    from datetime import datetime

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    email = req.email.strip().lower()
    code = req.code.strip()

    if not verify_and_consume_code(email, code):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Incorrect or expired verification code. Please request a fresh code and try again."},
            headers=cors_headers
        )

    # Valid code! Query MongoDB Atlas user_profiles
    u_coll = get_collection("user_profiles")
    m_user = None
    if u_coll is not None:
        m_user = u_coll.find_one({"email": email})
        if m_user:
            u_coll.update_one({"_id": m_user["_id"]}, {"$set": {"email_verified": True, "verified_at": datetime.utcnow()}})

    username = (m_user.get("username") if m_user else None) or email.split("@")[0]
    creator_data = (m_user.get("creator_data") if m_user else None) or {}
    calendar_data = (m_user.get("calendar_data") if m_user else None)
    launch_pack_data = (m_user.get("launch_pack_data") if m_user else None)
    studio_data = (m_user.get("studio_data") if m_user else None)
    chat_data = (m_user.get("chat_data") if m_user else None)
    ai_keys = (m_user.get("ai_keys") if m_user else None)
    has_password = bool(m_user and m_user.get("password"))
    youtube_connection = (m_user.get("youtube_connection") if m_user else None)
    meta_connection = (m_user.get("meta_connection") if m_user else None)
    accounts_platforms = (m_user.get("accounts_platforms") if m_user else None)

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "username": username,
            "email": email,
            "email_verified": True,
            "creator_data": creator_data,
            "calendar_data": calendar_data,
            "launch_pack_data": launch_pack_data,
            "studio_data": studio_data,
            "chat_data": chat_data,
            "ai_keys": ai_keys,
            "youtube_connection": youtube_connection,
            "meta_connection": meta_connection,
            "accounts_platforms": accounts_platforms,
            "logged_in_via_code": True,
            "has_password": has_password
        },
        headers=cors_headers
    )


@app.options("/api/auth/google")
def auth_google_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/auth/google")
async def auth_google(req: GoogleAuthRequest, request: Request):
    """Authenticates a user via Google Identity Services or Google OAuth 2.0."""
    from app.services.youtube_service import get_google_client_id, get_google_client_secret
    from app.mongodb import get_collection
    import re
    import time
    import httpx

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    email = None
    name = None
    picture = None
    google_id = None

    # 1. Google Credential (JWT token from Google One Tap or Google Sign-In)
    if req.credential:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={req.credential}")
                data = resp.json()
                if "error" not in data and data.get("email"):
                    email = data.get("email").strip().lower()
                    name = data.get("name") or email.split("@")[0]
                    picture = data.get("picture")
                    google_id = data.get("sub")
        except Exception as e:
            logger.warning(f"[Google Auth] Credential verification failed: {e}")

    # 2. Access Token from token client
    elif req.access_token:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {req.access_token}"}
                )
                data = resp.json()
                if "error" not in data and data.get("email"):
                    email = data.get("email").strip().lower()
                    name = data.get("name") or email.split("@")[0]
                    picture = data.get("picture")
                    google_id = data.get("sub")
        except Exception as e:
            logger.warning(f"[Google Auth] Userinfo fetch failed: {e}")

    # 3. Authorization Code from redirect or code client
    elif req.code:
        try:
            client_id = get_google_client_id()
            client_secret = get_google_client_secret()
            redirect_uri = req.redirect_uri or "postmessage"
            token_payload = {
                "code": req.code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post("https://oauth2.googleapis.com/token", data=token_payload)
                token_data = resp.json()
                acc_token = token_data.get("access_token")
                if acc_token:
                    userinfo_resp = await client.get(
                        "https://www.googleapis.com/oauth2/v3/userinfo",
                        headers={"Authorization": f"Bearer {acc_token}"}
                    )
                    user_info = userinfo_resp.json()
                    email = (user_info.get("email") or "").strip().lower()
                    name = user_info.get("name") or email.split("@")[0]
                    picture = user_info.get("picture")
                    google_id = user_info.get("sub")
        except Exception as e:
            logger.warning(f"[Google Auth] Code exchange failed: {e}")

    if not email:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Could not authenticate with Google. Please try again."},
            headers=cors_headers
        )

    # Lookup user in MongoDB Atlas user_profiles
    u_coll = get_collection("user_profiles")
    m_user = None
    if u_coll is not None:
        m_user = u_coll.find_one({
            "$or": [
                {"email": email},
                {"google_id": google_id}
            ]
        })

    # Fallback to creators collection
    if not m_user:
        c_coll = get_collection("creators")
        if c_coll is not None:
            c_doc = c_coll.find_one({
                "$or": [
                    {"email": email},
                    {"contactEmail": email}
                ]
            })
            if c_doc:
                m_user = {
                    "username": c_doc.get("handle", name).replace("@", ""),
                    "email": email,
                    "creator_data": c_doc,
                    "has_password": bool(c_doc.get("password"))
                }

    # If new user, create their profile immediately
    is_existing_user = bool(m_user)
    has_completed_onboarding = False
    if m_user:
        c_data = m_user.get("creator_data") or {}
        has_completed_onboarding = bool(
            c_data.get("blueprint") or
            c_data.get("productName") or
            m_user.get("calendar_data") or
            m_user.get("onboarding_completed")
        )

    if not m_user:
        clean_user = re.sub(r"[^a-zA-Z0-9_]", "", (name or email.split("@")[0]).lower()) or "creator"
        new_profile = {
            "username": clean_user,
            "email": email,
            "google_id": google_id,
            "name": name,
            "avatar": picture,
            "has_password": False,
            "creator_data": {
                "name": name,
                "email": email,
                "avatar": picture,
                "handle": f"@{clean_user}",
                "auth_provider": "google"
            },
            "created_at": time.time()
        }
        if u_coll is not None:
            try:
                u_coll.insert_one(new_profile)
            except Exception as e:
                logger.warning(f"Could not persist new Google profile: {e}")
        m_user = new_profile

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "username": m_user.get("username"),
            "email": m_user.get("email") or email,
            "creator_data": m_user.get("creator_data"),
            "calendar_data": m_user.get("calendar_data"),
            "launch_pack_data": m_user.get("launch_pack_data"),
            "studio_data": m_user.get("studio_data"),
            "chat_data": m_user.get("chat_data"),
            "ai_keys": m_user.get("ai_keys"),
            "youtube_connection": m_user.get("youtube_connection"),
            "meta_connection": m_user.get("meta_connection"),
            "accounts_platforms": m_user.get("accounts_platforms"),
            "has_password": bool(m_user.get("has_password")),
            "logged_in_via_google": True,
            "is_existing_user": is_existing_user,
            "has_completed_onboarding": has_completed_onboarding
        },
        headers=cors_headers
    )


@app.post("/api/auth/sync")
def auth_sync(req: SyncRequest):
    """Syncs session state directly to MongoDB Atlas user_profiles."""
    from fastapi import HTTPException
    from app.mongodb import get_collection

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    update_fields = {}
    if req.creator_data is not None: update_fields["creator_data"] = req.creator_data
    if req.calendar_data is not None: update_fields["calendar_data"] = req.calendar_data
    if req.launch_pack_data is not None: update_fields["launch_pack_data"] = req.launch_pack_data
    if req.studio_data is not None: update_fields["studio_data"] = req.studio_data
    if req.chat_data is not None: update_fields["chat_data"] = req.chat_data
    if req.youtube_connection is not None: update_fields["youtube_connection"] = req.youtube_connection
    if req.meta_connection is not None: update_fields["meta_connection"] = req.meta_connection
    if req.accounts_platforms is not None: update_fields["accounts_platforms"] = req.accounts_platforms
    if req.community_data is not None: update_fields["community_data"] = req.community_data
    if req.marketing_data is not None: update_fields["marketing_data"] = req.marketing_data
    if req.products_data is not None: update_fields["products_data"] = req.products_data

    clean_u = (req.username or "").strip()
    if update_fields and clean_u:
        res = u_coll.update_many(
            {"$or": [
                {"username": clean_u},
                {"username": clean_u.lower()},
                {"email": clean_u.lower()}
            ]},
            {"$set": update_fields}
        )
        if res.matched_count == 0:
            u_coll.update_one({"username": clean_u}, {"$set": update_fields}, upsert=True)

    return {"status": "success"}


# ── Dedicated Dashboard MongoDB Persistence Endpoints ─────────────────────────

class DashboardSaveRequest(BaseModel):
    username: str
    handle: Optional[str] = None
    section: str  # 'calendar', 'studio', 'community', 'marketing', 'products', 'accounts', 'creator', 'all'
    data: Dict[str, Any]


@app.options("/api/dashboard/save")
def options_dashboard_save(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept, X-Requested-With",
            "Access-Control-Max-Age": "86400"
        }
    )


@app.options("/api/dashboard/data")
def options_dashboard_data(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept, X-Requested-With",
            "Access-Control-Max-Age": "86400"
        }
    )


@app.get("/api/dashboard/data")
def get_dashboard_data(username: Optional[str] = None, handle: Optional[str] = None, request: Request = None):
    """Retrieves all dashboard sections directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    clean_u = (username or "").strip()
    clean_h = (handle or "").strip().lower().lstrip("@")

    query = []
    if clean_u:
        query.extend([
            {"username": clean_u},
            {"username": clean_u.lower()},
            {"email": clean_u.lower()}
        ])
    if clean_h and clean_h != "default":
        query.extend([
            {"creator_data.handle": f"@{clean_h}"},
            {"creator_data.handle": clean_h},
            {"username": clean_h}
        ])

    m_user = None
    if query:
        m_user = u_coll.find_one({"$or": query})

    if not m_user:
        return {
            "status": "success",
            "username": clean_u,
            "creator_data": {},
            "calendar_data": {},
            "studio_data": {},
            "community_data": {},
            "marketing_data": {},
            "products_data": {},
            "youtube_connection": {},
            "meta_connection": {},
            "accounts_platforms": {}
        }

    return {
        "status": "success",
        "username": m_user.get("username", clean_u),
        "email": m_user.get("email", ""),
        "creator_data": m_user.get("creator_data") or {},
        "calendar_data": m_user.get("calendar_data") or {},
        "studio_data": m_user.get("studio_data") or {},
        "community_data": m_user.get("community_data") or {},
        "marketing_data": m_user.get("marketing_data") or {},
        "products_data": m_user.get("products_data") or {},
        "youtube_connection": m_user.get("youtube_connection") or {},
        "meta_connection": m_user.get("meta_connection") or {},
        "accounts_platforms": m_user.get("accounts_platforms") or {}
    }


@app.post("/api/dashboard/save")
def save_dashboard_data(req: DashboardSaveRequest, request: Request = None):
    """Saves a specific dashboard section directly to MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    clean_u = (req.username or "").strip()
    clean_h = (req.handle or "").strip().lower().lstrip("@")

    update_fields = {}
    sec = req.section.lower().strip()
    if sec == "calendar":
        update_fields["calendar_data"] = req.data
    elif sec == "studio":
        update_fields["studio_data"] = req.data
    elif sec == "community":
        update_fields["community_data"] = req.data
    elif sec == "marketing":
        update_fields["marketing_data"] = req.data
    elif sec == "products":
        update_fields["products_data"] = req.data
    elif sec == "accounts":
        update_fields["accounts_platforms"] = req.data
    elif sec == "creator":
        update_fields["creator_data"] = req.data
    elif sec == "all":
        for k, v in req.data.items():
            update_fields[k] = v
    else:
        update_fields[f"{sec}_data"] = req.data

    if not clean_u and not clean_h:
        raise HTTPException(status_code=400, detail="Username or handle is required")

    match_query = []
    if clean_u:
        match_query.extend([
            {"username": clean_u},
            {"username": clean_u.lower()},
            {"email": clean_u.lower()}
        ])
    if clean_h and clean_h != "default":
        match_query.extend([
            {"creator_data.handle": f"@{clean_h}"},
            {"creator_data.handle": clean_h},
            {"username": clean_h}
        ])

    res = u_coll.update_many({"$or": match_query}, {"$set": update_fields})
    if res.matched_count == 0:
        u_coll.update_one({"username": clean_u or clean_h}, {"$set": update_fields}, upsert=True)

    return {"status": "success", "section": sec}


# ── Dedicated Chat Persistence Endpoints ─────────────────────────────────────

class SaveChatMessageRequest(BaseModel):
    handle: str
    username: Optional[str] = None
    role: str
    content: str
    actions: Optional[List[str]] = None
    timestamp: Optional[Union[float, int, str]] = None

class SyncChatHistoryRequest(BaseModel):
    handle: str
    username: Optional[str] = None
    messages: List[Dict[str, Any]]

@app.get("/api/chat/messages")
def get_chat_messages(handle: str, username: Optional[str] = None, db: Session = Depends(get_db)):
    import time
    from app.mongodb import get_collection
    from app.models.creator import ChatMessageRecord
    from app.mongodb import get_collection

    clean_handle = handle.strip().replace("@", "").lower()
    messages = []
    seen_keys = set()

    def _norm_ts(val):
        if val is None:
            return 0
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return str(val)

    # 1. Query MongoDB chat_messages
    c_coll = get_collection("chat_messages")
    if c_coll is not None:
        try:
            query = {"$or": [{"handle": clean_handle}, {"handle": handle.strip()}]}
            if username:
                query["$or"].append({"username": username.strip()})
            cursor = c_coll.find(query).sort("timestamp", 1)
            for doc in cursor:
                doc_id = str(doc.get("_id", doc.get("id", "")))
                role = doc.get("role", "forge")
                content = (doc.get("content") or doc.get("text") or "").strip()
                ts = doc.get("timestamp", doc.get("timestamp_ms", doc.get("created_at")))
                dedup_key = f"{role}:{content}:{_norm_ts(ts)}"
                if dedup_key not in seen_keys and content:
                    seen_keys.add(dedup_key)
                    messages.append({
                        "id": doc_id,
                        "role": role,
                        "content": content,
                        "actions": doc.get("actions"),
                        "timestamp": ts,
                        "created_at": doc.get("created_at")
                    })
        except Exception as e:
            print(f"[Chat API] MongoDB fetch error: {e}")

    # 2. Query SQLite ChatMessageRecord as fallback/supplement
    try:
        query = db.query(ChatMessageRecord).filter(
            (ChatMessageRecord.handle == clean_handle) | (ChatMessageRecord.handle == handle.strip())
        )
        if username:
            query = query.filter((ChatMessageRecord.username == username.strip()) | (ChatMessageRecord.handle == clean_handle))
        records = query.order_by(ChatMessageRecord.created_at.asc()).all()
        for r in records:
            ts = r.timestamp_ms or (r.created_at.timestamp() * 1000 if r.created_at else None)
            content = (r.content or "").strip()
            dedup_key = f"{r.role}:{content}:{_norm_ts(ts)}"
            if dedup_key not in seen_keys and content:
                seen_keys.add(dedup_key)
                messages.append({
                    "id": r.id,
                    "role": r.role,
                    "content": content,
                    "actions": r.actions,
                    "timestamp": ts,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                })
    except Exception as e:
        print(f"[Chat API] SQLite fetch error: {e}")

    # 3. If messages is still empty, check user profile's chat_data in MongoDB Atlas
    if not messages and username:
        try:
            u_coll = get_collection("user_profiles")
            if u_coll is not None:
                m_u = u_coll.find_one({"username": username})
                if m_u and m_u.get("chat_data"):
                    for k, v in m_u["chat_data"].items():
                        if clean_handle in k and isinstance(v, list):
                            messages = v
                            break
        except Exception:
            pass

    messages.sort(key=lambda m: _norm_ts(m.get("timestamp") or 0))

    return {"status": "success", "handle": clean_handle, "messages": messages}

@app.post("/api/chat/messages")
def save_chat_message(req: SaveChatMessageRequest, db: Session = Depends(get_db)):
    import time
    import uuid
    from datetime import datetime
    from app.mongodb import get_collection
    from app.models.creator import ChatMessageRecord

    clean_handle = req.handle.strip().replace("@", "").lower()
    now_dt = datetime.utcnow()
    now_iso = now_dt.isoformat()
    now_ms = time.time() * 1000
    ts = req.timestamp if req.timestamp is not None else now_ms

    msg_id = str(uuid.uuid4())
    doc = {
        "id": msg_id,
        "handle": clean_handle,
        "username": req.username,
        "role": req.role,
        "content": req.content,
        "actions": req.actions,
        "timestamp": ts,
        "created_at": now_iso
    }

    # 1. MongoDB Atlas
    c_coll = get_collection("chat_messages")
    if c_coll is not None:
        try:
            c_coll.insert_one(doc.copy())
        except Exception as e:
            print(f"[Chat API] MongoDB insert error: {e}")

    # 2. SQLite
    try:
        rec = ChatMessageRecord(
            id=msg_id,
            handle=clean_handle,
            username=req.username,
            role=req.role,
            content=req.content,
            actions=req.actions,
            timestamp_ms=float(ts) if isinstance(ts, (int, float)) else now_ms,
            created_at=now_dt
        )
        db.add(rec)
        db.commit()
    except Exception as e:
        print(f"[Chat API] SQLite insert error: {e}")

    return {"status": "success", "message": doc}

@app.post("/api/chat/sync")
def sync_chat_history(req: SyncChatHistoryRequest, db: Session = Depends(get_db)):
    import time
    import uuid
    from datetime import datetime
    from app.mongodb import get_collection
    from app.models.creator import ChatMessageRecord
    from app.mongodb import get_collection

    clean_handle = req.handle.strip().replace("@", "").lower()
    now_ms = time.time() * 1000
    now_iso = datetime.utcnow().isoformat()

    c_coll = get_collection("chat_messages")
    if c_coll is not None and req.messages:
        try:
            # Upsert each message into MongoDB
            for m in req.messages:
                content = m.get("content") or m.get("text") or ""
                if not content:
                    continue
                role = m.get("role", "forge")
                ts = m.get("timestamp") or m.get("timestamp_ms") or now_ms
                m_id = str(m.get("id") or uuid.uuid4())
                c_coll.update_one(
                    {"handle": clean_handle, "role": role, "content": content},
                    {"$set": {
                        "id": m_id,
                        "handle": clean_handle,
                        "username": req.username,
                        "role": role,
                        "content": content,
                        "actions": m.get("actions"),
                        "timestamp": ts,
                        "created_at": m.get("created_at") or now_iso
                    }},
                    upsert=True
                )
        except Exception as e:
            print(f"[Chat API] Bulk sync MongoDB error: {e}")

    # Also update user profile chat_data in MongoDB Atlas if username provided
    if req.username:
        try:
            u_coll = get_collection("user_profiles")
            if u_coll is not None:
                u_coll.update_one(
                    {"username": req.username},
                    {"$set": {f"chat_data.forge_{clean_handle}_chat_messages": req.messages}}
                )
        except Exception as e:
            print(f"[Chat API] MongoDB user profile chat_data sync error: {e}")

    return {"status": "success", "count": len(req.messages)}


# ── AI Keys Save/Load (user-consented persistence) ────────────────────────────

class SaveAiKeysRequest(BaseModel):
    username: str
    ai_keys: Dict[str, str]  # { geminiKey, togetherKey, nvidiaKey }

class DeleteAiKeysRequest(BaseModel):
    username: str

@app.post("/api/auth/save-ai-keys")
def save_ai_keys(req: SaveAiKeysRequest):
    """Save AI API keys directly to MongoDB Atlas user_profiles."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database unavailable")

    res = u_coll.update_one({"username": req.username}, {"$set": {"ai_keys": req.ai_keys}})
    if res.matched_count == 0:
        # Create minimal profile if not present
        u_coll.insert_one({"username": req.username, "ai_keys": req.ai_keys})

    return {"status": "success", "message": "AI keys saved securely to MongoDB Atlas."}

@app.post("/api/auth/delete-ai-keys")
def delete_ai_keys(req: DeleteAiKeysRequest):
    """Remove saved AI keys from MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database unavailable")

    u_coll.update_one({"username": req.username}, {"$unset": {"ai_keys": ""}})
    return {"status": "success", "message": "AI keys removed."}

@app.get("/api/auth/load-ai-keys/{username}")
def load_ai_keys(username: str):
    """Load saved AI keys directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database unavailable")

    user = u_coll.find_one({"username": username})
    if not user:
        return {"status": "success", "ai_keys": None}

    return {"status": "success", "ai_keys": user.get("ai_keys")}


@app.get("/api/auth/profile/{username}")
def get_user_profile(username: str):
    """Retrieve full user profile data for session synchronization directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException
    from datetime import datetime

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    clean_u = (username or "").strip()
    m_user = u_coll.find_one({
        "$or": [
            {"username": clean_u},
            {"username": clean_u.lower()},
            {"email": clean_u.lower()}
        ]
    })

    # Auto-initialize admin/creatorforgeweb profile if not present
    if not m_user:
        admin_email = os.getenv("ADMIN_EMAIL", "creatorforgeweb@gmail.com")
        if clean_u.lower() in ("creatorforgeweb", "admin") or clean_u.lower() == admin_email.split("@")[0].lower() or clean_u.lower() == admin_email.lower():
            doc = {
                "username": "creatorforgeweb",
                "email": admin_email,
                "password": "",
                "creator_data": {},
                "calendar_data": {},
                "launch_pack_data": {},
                "studio_data": {},
                "ai_keys": {},
                "email_verified": True,
                "created_at": datetime.utcnow()
            }
            u_coll.update_one({"username": "creatorforgeweb"}, {"$set": doc}, upsert=True)
            return {
                "status": "success",
                "username": "creatorforgeweb",
                "email": admin_email,
                "creator_data": {},
                "calendar_data": {},
                "launch_pack_data": {},
                "studio_data": {},
                "ai_keys": {}
            }

    if not m_user:
        return {
            "status": "success",
            "username": clean_u,
            "email": "",
            "creator_data": None,
            "calendar_data": None,
            "launch_pack_data": None,
            "studio_data": None,
            "chat_data": None,
            "ai_keys": None,
            "youtube_connection": None,
            "meta_connection": None,
            "accounts_platforms": None,
            "community_data": None,
            "marketing_data": None,
            "products_data": None
        }

    return {
        "status": "success",
        "username": m_user.get("username", clean_u),
        "email": m_user.get("email", ""),
        "email_verified": m_user.get("email_verified", False),
        "creator_data": m_user.get("creator_data"),
        "calendar_data": m_user.get("calendar_data"),
        "launch_pack_data": m_user.get("launch_pack_data"),
        "studio_data": m_user.get("studio_data"),
        "chat_data": m_user.get("chat_data"),
        "ai_keys": m_user.get("ai_keys"),
        "youtube_connection": m_user.get("youtube_connection"),
        "meta_connection": m_user.get("meta_connection"),
        "accounts_platforms": m_user.get("accounts_platforms"),
        "community_data": m_user.get("community_data"),
        "marketing_data": m_user.get("marketing_data"),
        "products_data": m_user.get("products_data")
    }


class UpdateProfileRequest(BaseModel):
    username: str
    email: Optional[str] = None
    password: Optional[str] = None
    verification_code: Optional[str] = None
    creator_data: Optional[Dict[str, Any]] = None


@app.options("/api/auth/update-profile")
def update_profile_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/auth/update-profile")
def update_profile(req: UpdateProfileRequest, request: Request):
    """
    Update or register profile email, password, and creator_data strictly in MongoDB Atlas.
    Requires email verification with an auth code before an email can be added or updated.
    """
    from fastapi import HTTPException
    from app.integrations.email_provider import email_provider
    from app.mongodb import get_collection
    import re
    import time
    from datetime import datetime

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    u_coll = get_collection("user_profiles")
    if u_coll is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    norm_uname = req.username.strip()
    norm_email = req.email.strip().lower() if (req.email and req.email.strip()) else None

    # Search existing user in MongoDB Atlas strictly by verified email or unique username
    or_clauses = []
    if norm_email:
        or_clauses.append({"email": norm_email})
    if norm_uname and norm_uname not in ["creator", "default", "creatorforgeweb", ""]:
        or_clauses.extend([{"username": norm_uname}, {"username": norm_uname.lower()}])
    m_user = u_coll.find_one({"$or": or_clauses}) if or_clauses else None

    old_email = ((m_user.get("email") if m_user else "") or "").strip().lower()
    is_email_provided = bool(norm_email)
    is_email_new_or_changing = is_email_provided and (norm_email != old_email or not (m_user and m_user.get("email_verified")))

    # ── EMAIL VERIFICATION CODE REQUIREMENT ──
    # When a user adds or changes an email, it MUST be verified with auth code before being successful!
    if is_email_new_or_changing:
        code_input = (req.verification_code or "").strip()
        if not code_input:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "detail": "Email verification required. Please enter the 6-digit authentication code sent to your email."
                },
                headers=cors_headers
            )
        if not verify_and_consume_code(norm_email, code_input):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "detail": "Invalid or expired verification code. Please request a new code and try again."
                },
                headers=cors_headers
            )

    now = datetime.utcnow()

    # ── CASE 1: USER DOES NOT EXIST YET -> REGISTER CREATOR AS OFFICIAL ACCOUNT ──
    if not m_user:
        clean_username = norm_uname
        if not clean_username or clean_username in ["creator", "default", "creatorforgeweb"]:
            if req.creator_data and req.creator_data.get("handle"):
                clean_username = re.sub(r"[^a-zA-Z0-9_-]", "", req.creator_data["handle"].replace("@", ""))
            elif norm_email and "@" in norm_email:
                clean_username = norm_email.split("@")[0]
            else:
                clean_username = f"creator_{int(time.time())}"

        pwd = req.password.strip() if req.password else ""
        doc = {
            "username": clean_username,
            "email": norm_email or "",
            "email_verified": True if is_email_provided else False,
            "verified_at": now if is_email_provided else None,
            "password": pwd,
            "has_password": bool(pwd),
            "creator_data": req.creator_data or {},
            "created_at": now,
            "updated_at": now
        }
        u_coll.insert_one(doc)

        # Update creators collection in MongoDB Atlas
        c_coll = get_collection("creators")
        if c_coll is not None and req.creator_data and req.creator_data.get("handle"):
            h = req.creator_data["handle"]
            c_coll.update_many(
                {"$or": [{"handle": h}, {"handle": h.replace("@", "")}]},
                {"$set": {"email": norm_email, "contactEmail": norm_email, "email_verified": True}}
            )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "username": clean_username,
                "email": norm_email or "",
                "email_verified": True if is_email_provided else False,
                "has_password": bool(pwd),
                "registered": True
            },
            headers=cors_headers
        )

    # ── CASE 2: USER EXISTS -> UPDATE ATTRIBUTES ──
    target_username = m_user.get("username", norm_uname)
    mongo_updates = {"updated_at": now}
    new_email = norm_email

    if new_email and new_email != old_email:
        # Verify email conflict
        conflict = u_coll.find_one({"email": new_email, "username": {"$ne": target_username}})
        if conflict:
            raise HTTPException(status_code=400, detail="Email address already registered by another account")

        mongo_updates["email"] = new_email
        mongo_updates["email_verified"] = True
        mongo_updates["verified_at"] = now

        # Security alert to old address if legitimate
        is_dummy_old_email = (
            not old_email or 
            old_email == "creatorforgeweb@gmail.com" or 
            old_email.endswith("@example.com")
        )
        if not is_dummy_old_email:
            try:
                subject = "CoLaunch - Security Alert: Email Address Changed"
                body_text = f"Hello {target_username},\n\nThe email address associated with your CoLaunch account was updated from {old_email} to {new_email}."
                body_html = f"<p>Hello <strong>{target_username}</strong>, your account email was updated to <strong>{new_email}</strong>.</p>"
                email_provider.send_email(to_email=old_email, subject=subject, body_html=body_html, body_text=body_text)
            except Exception as e:
                logger.warning(f"Email change notification error: {e}")
    elif is_email_provided and is_email_new_or_changing:
        mongo_updates["email_verified"] = True
        mongo_updates["verified_at"] = now

    if req.password and req.password.strip():
        pwd = req.password.strip()
        mongo_updates["password"] = pwd
        mongo_updates["has_password"] = True

    if req.creator_data is not None:
        mongo_updates["creator_data"] = req.creator_data

    # Commit strictly to MongoDB Atlas user_profiles
    u_coll.update_one({"_id": m_user["_id"]}, {"$set": mongo_updates})

    # Update creators collection in MongoDB Atlas
    c_coll = get_collection("creators")
    if c_coll is not None and (new_email or req.creator_data):
        h = (req.creator_data or {}).get("handle") or (m_user.get("creator_data") or {}).get("handle")
        if h:
            c_set = {}
            if new_email:
                c_set["email"] = new_email
                c_set["contactEmail"] = new_email
                c_set["email_verified"] = True
            if c_set:
                c_coll.update_many(
                    {"$or": [{"handle": h}, {"handle": h.replace("@", "")}]},
                    {"$set": c_set}
                )

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "username": target_username,
            "email": new_email or old_email,
            "email_verified": True if (new_email or m_user.get("email_verified")) else False,
            "has_password": bool(m_user.get("password") or (req.password and req.password.strip()))
        },
        headers=cors_headers
    )


# ── Admin Control APIs ────────────────────────────────────────────────────────
@app.options("/api/admin/users")
def list_admin_users_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.get("/api/admin/users")
def list_admin_users(request: Request):
    """List all registered users directly and exclusively from MongoDB Atlas user_profiles."""
    from app.mongodb import get_collection
    import json
    import traceback
    import re

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    out = []
    seen = set()

    # 1. Fetch all registered users from MongoDB Atlas user_profiles
    u_coll = get_collection("user_profiles")
    if u_coll is not None:
        try:
            docs = list(u_coll.find({}))
            for u in docs:
                try:
                    raw_id = str(u.get("_id", "")).strip()
                    uname = u.get("username")
                    email = (u.get("email") or "").strip().lower()

                    c_data = u.get("creator_data")
                    if isinstance(c_data, str):
                        try:
                            c_data = json.loads(c_data)
                        except Exception:
                            c_data = {}
                    elif not isinstance(c_data, dict):
                        c_data = {}

                    if not email and isinstance(c_data, dict):
                        email = (c_data.get("email") or c_data.get("contactEmail") or "").strip().lower()

                    if not uname or uname in ("default", "creator"):
                        if email and "@" in email:
                            uname = email.split("@")[0]
                        elif raw_id and not raw_id.startswith("ObjectId"):
                            uname = raw_id
                        elif c_data.get("handle"):
                            uname = c_data["handle"].replace("@", "")
                        elif u.get("name"):
                            uname = u["name"]
                        else:
                            uname = "creator"

                    # Deduplication key: prefer distinct email, or unique username
                    key = email or (uname.lower() if uname and uname not in ("default", "creator") else raw_id)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    if email:
                        seen.add(email)
                    if uname and uname not in ("default", "creator"):
                        seen.add(uname.lower())

                    has_ai_keys = False
                    ai_keys = u.get("ai_keys")
                    if isinstance(ai_keys, dict):
                        has_ai_keys = any(bool(v) for v in ai_keys.values())

                    created = u.get("created_at")
                    created_str = created.isoformat() if hasattr(created, "isoformat") else str(created) if created else None
                    if not created_str and "_id" in u and hasattr(u["_id"], "generation_time"):
                        try:
                            created_str = u["_id"].generation_time.isoformat()
                        except Exception:
                            pass

                    platform = c_data.get("platform") or u.get("platform") or ("youtube" if (c_data.get("youtube_connected") or c_data.get("channel_title")) else "creator")
                    handle = c_data.get("handle") or c_data.get("name") or c_data.get("channel_title") or u.get("name") or uname
                    if isinstance(handle, str) and handle.startswith("@"):
                        handle = handle[1:]

                    out.append({
                        "username": uname,
                        "email": email,
                        "email_verified": u.get("email_verified", bool(email)),
                        "platform": platform or "creator",
                        "handle": handle or uname,
                        "has_ai_keys": has_ai_keys,
                        "has_password": bool(u.get("password") or u.get("has_password") or u.get("hashed_password")),
                        "created_at": created_str
                    })
                except Exception as doc_err:
                    logger.error(f"[Admin Users] Error parsing document: {doc_err}")
        except Exception as e:
            logger.error(f"Error fetching MongoDB Atlas user_profiles: {e}\n{traceback.format_exc()}")

    # 2. Also check if any creator in 'creators' collection was registered with email & password
    c_coll = get_collection("creators")
    if c_coll is not None:
        try:
            for c in c_coll.find({"$or": [{"email": {"$exists": True, "$ne": ""}}, {"contactEmail": {"$exists": True, "$ne": ""}}]}):
                try:
                    c_email = (c.get("email") or c.get("contactEmail") or "").strip().lower()
                    if not c_email or c_email in seen:
                        continue
                    c_handle = c.get("handle") or c.get("name") or c_email.split("@")[0]
                    c_uname = re.sub(r"[^a-zA-Z0-9_-]", "", c_handle.replace("@", "")) or c_email.split("@")[0]
                    key = c_email or c_uname.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    seen.add(c_email)

                    out.append({
                        "username": c_uname,
                        "email": c_email,
                        "email_verified": c.get("email_verified", True),
                        "platform": c.get("platform") or "youtube",
                        "handle": c_handle.replace("@", "") if isinstance(c_handle, str) else str(c_handle),
                        "has_ai_keys": False,
                        "has_password": bool(c.get("password") or c.get("has_password")),
                        "created_at": c.get("created_at").isoformat() if hasattr(c.get("created_at"), "isoformat") else str(c.get("created_at")) if c.get("created_at") else None
                    })
                except Exception:
                    pass
        except Exception:
            pass

    # Sort users newest first
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return JSONResponse(
        status_code=200,
        content={"status": "success", "users": out},
        headers=cors_headers
    )


@app.options("/api/admin/users/{username}")
def delete_admin_user_options(username: str, request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.delete("/api/admin/users/{username}")
def delete_admin_user(username: str, request: Request):
    """Delete a user profile from MongoDB Atlas."""
    from app.mongodb import get_collection
    from fastapi import HTTPException

    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    deleted = False
    u_coll = get_collection("user_profiles")
    if u_coll is not None:
        res = u_coll.delete_many({"$or": [{"username": username}, {"email": username.lower()}, {"_id": username}]})
        if res.deleted_count > 0:
            deleted = True

    c_coll = get_collection("creators")
    if c_coll is not None:
        try:
            c_coll.update_many(
                {"$or": [{"email": username.lower()}, {"contactEmail": username.lower()}, {"handle": username}]},
                {"$unset": {"email": "", "contactEmail": "", "password": "", "has_password": ""}}
            )
        except Exception:
            pass

    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return JSONResponse(
        status_code=200,
        content={"status": "success", "message": f"User {username} deleted."},
        headers=cors_headers
    )


@app.get("/api/admin/system-stats")
def get_system_stats(db: Session = Depends(get_db)):
    """Return database stats and row counts directly from MongoDB Atlas."""
    from app.mongodb import get_mongo_db, get_collection
    
    db_size_bytes = 0
    try:
        mdb = get_mongo_db()
        stats = mdb.command("dbstats")
        db_size_bytes = stats.get("dataSize", 0) + stats.get("indexSize", 0)
    except Exception:
        pass

    def _c_count(col_name):
        try:
            c = get_collection(col_name)
            return c.count_documents({}) if c is not None else 0
        except Exception:
            return 0

    users_count = _c_count("user_profiles")
    creators_count = _c_count("creators")
    recs_count = _c_count("product_recommendations")
    campaigns_count = _c_count("campaigns")
    outreach_count = _c_count("outreach_messages")
    suppression_count = _c_count("suppression_list")
    audit_logs_count = _c_count("audit_logs")
    projects_count = _c_count("co_launch_projects")

    return {
        "status": "success",
        "database": "MongoDB Atlas",
        "db_size": f"{db_size_bytes / 1024:.1f} KB",
        "users_count": users_count,
        "creators_count": creators_count,
        "recs_count": recs_count,
        "campaigns_count": campaigns_count,
        "outreach_count": outreach_count,
        "suppression_count": suppression_count,
        "audit_logs_count": audit_logs_count,
        "projects_count": projects_count,
    }


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


# ── Static media directory mount ──────────────────────────────────────────────
from pathlib import Path
MEDIA_DIR = Path(__file__).resolve().parent.parent / "static" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/static/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


class SaveMediaRequest(BaseModel):
    image_data: str


@app.post("/api/media/save")
def save_media_image(req: SaveMediaRequest, db: Session = Depends(get_db)):
    import uuid
    import time
    from fastapi import HTTPException
    from app.models.creator import MediaImage
    
    # Ensure media directory exists
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    
    try:
        data = req.image_data.strip()
        if not data:
            raise HTTPException(status_code=400, detail="Empty image data")
            
        ext = "png"
        file_bytes = None
        
        if data.startswith("data:"):
            # It's base64
            if "," in data:
                header, data_str = data.split(",", 1)
            else:
                header = ""
                data_str = data
                
            # Determine extension
            if "jpeg" in header or "jpg" in header:
                ext = "jpg"
            elif "webp" in header:
                ext = "webp"
            elif "gif" in header:
                ext = "gif"
                
            import base64
            file_bytes = base64.b64decode(data_str)
        elif data.startswith("http://") or data.startswith("https://"):
            # It's a remote URL
            url_lower = data.lower()
            if ".jpg" in url_lower or ".jpeg" in url_lower:
                ext = "jpg"
            elif ".webp" in url_lower:
                ext = "webp"
            elif ".gif" in url_lower:
                ext = "gif"
                
            import urllib.request
            # Download the image
            u_req = urllib.request.Request(data, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(u_req, timeout=15) as response:
                file_bytes = response.read()
        else:
            raise HTTPException(status_code=400, detail="Invalid image data format. Must be base64 data-URI or HTTP URL.")
            
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Could not read image bytes")
            
        from app.integrations.cloudinary_service import upload_media_to_cloudinary
        cld_res = upload_media_to_cloudinary(
            file_data=file_bytes,
            public_id=f"concept_{int(time.time())}_{uuid.uuid4().hex[:8]}",
            folder="creator_forge/concepts",
            resource_type="image"
        )
        if cld_res.get("success") and cld_res.get("secure_url"):
            return {"url": cld_res["secure_url"]}
        raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {cld_res.get('error')}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to save image: {str(e)}")


@app.get("/api/static/media/{filename}")
def serve_media_image(filename: str, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from app.models.creator import MediaImage
    
    filepath = MEDIA_DIR / filename
    if filepath.exists():
        return FileResponse(str(filepath))
        
    # Check database
    db_image = db.query(MediaImage).filter(MediaImage.filename == filename).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="Image not found")
        
    return Response(content=db_image.image_bytes, media_type=db_image.content_type)


# ── YouTube Direct OAuth & Social Publishing Endpoints ─────────────────────
@app.get("/api/youtube/auth-url")
def get_youtube_auth_url_endpoint(redirect_uri: Optional[str] = None, state: Optional[str] = None):
    """Generates the Google OAuth 2.0 authorization URL for connecting a YouTube Channel."""
    from app.services.youtube_service import generate_youtube_auth_url
    try:
        url = generate_youtube_auth_url(redirect_uri=redirect_uri, state=state)
        return {"status": "success", "authUrl": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.options("/api/youtube/exchange-token")
def exchange_youtube_token_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/youtube/exchange-token")
def exchange_youtube_token_endpoint(payload: dict, request: Request):
    """Exchanges Google authorization code for persistent tokens and retrieves channel profile."""
    from app.services.youtube_service import (
        exchange_code_for_tokens,
        fetch_channel_profile,
        save_youtube_connection
    )
    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    code = payload.get("code")
    redirect_uri = payload.get("redirect_uri")
    identifier = payload.get("identifier") or payload.get("email") or payload.get("username") or ""
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")

    if not code:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Authorization code is required"},
            headers=cors_headers
        )

    try:
        token_data = exchange_code_for_tokens(
            code, 
            redirect_uri=redirect_uri,
            client_id=client_id,
            client_secret=client_secret
        )
        access_token = token_data.get("access_token")
        channel_profile = fetch_channel_profile(access_token)
        connection = save_youtube_connection(identifier, token_data, channel_profile)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "connected": True,
                "channel": connection.get("channel"),
                "connected_at": connection.get("connected_at"),
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": token_data.get("refresh_token"),
                    "expires_in": token_data.get("expires_in")
                }
            },
            headers=cors_headers
        )
    except Exception as e:
        logger.error(f"[YouTube OAuth] Token exchange error: {e}")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(e)},
            headers=cors_headers
        )


@app.get("/api/youtube/status")
def get_youtube_status_endpoint(
    identifier: Optional[str] = None,
    email: Optional[str] = None,
    username: Optional[str] = None,
    request: Request = None
):
    """Returns the YouTube connection status strictly for the requested user."""
    from app.services.youtube_service import get_youtube_connection
    target_id = (email or username or identifier or "").strip()
    if not target_id or target_id == "default":
        return {"connected": False, "channel": None}
    try:
        conn = get_youtube_connection(target_id)
        return conn
    except Exception as e:
        return {"connected": False, "channel": None, "error": str(e)}


@app.options("/api/youtube/videos")
def options_youtube_videos(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.get("/api/youtube/videos")
def get_youtube_videos_endpoint(
    identifier: Optional[str] = None,
    email: Optional[str] = None,
    username: Optional[str] = None,
    limit: int = 10,
    request: Request = None
):
    """Returns real uploaded videos strictly for the connected YouTube channel of this user."""
    from app.services.youtube_service import get_connected_channel_videos
    origin = get_cors_origin(request) if request else "*"
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    target_id = (email or username or identifier or "").strip()
    if not target_id or target_id == "default":
        return JSONResponse(
            status_code=200,
            content={"status": "success", "videos": [], "count": 0},
            headers=cors_headers
        )
    try:
        videos = get_connected_channel_videos(target_id, limit=limit)
        return JSONResponse(
            status_code=200,
            content={"status": "success", "count": len(videos), "videos": videos},
            headers=cors_headers
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "videos": [], "detail": str(e)},
            headers=cors_headers
        )


@app.options("/api/youtube/publish")
def publish_youtube_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/youtube/publish")
def publish_to_youtube_endpoint(payload: dict, request: Request):
    """Directly publishes or schedules a post to the authenticated YouTube channel."""
    from app.services.youtube_service import publish_youtube_post
    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    identifier = payload.get("identifier", "default")
    title = payload.get("title", "New YouTube Video")
    description = payload.get("description", "")
    privacy = payload.get("privacy", "public")
    tags = payload.get("tags", [])
    video_url = payload.get("video_url")
    video_base64 = payload.get("video_base64")
    publish_at = payload.get("publish_at")
    is_short = payload.get("is_short", False)
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")

    try:
        res = publish_youtube_post(
            identifier=identifier,
            title=title,
            description=description,
            privacy=privacy,
            tags=tags,
            video_url=video_url,
            video_base64=video_base64,
            publish_at=publish_at,
            is_short=is_short,
            access_token=access_token,
            refresh_token=refresh_token
        )
        return JSONResponse(
            status_code=200,
            content={"status": "success", "result": res},
            headers=cors_headers
        )
    except Exception as e:
        logger.error(f"[YouTube Publish API] Failed: {e}")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(e)},
            headers=cors_headers
        )


@app.options("/api/youtube/disconnect")
def disconnect_youtube_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.delete("/api/youtube/disconnect")
def disconnect_youtube_endpoint(
    identifier: Optional[str] = None,
    email: Optional[str] = None,
    username: Optional[str] = None,
    request: Request = None
):
    """Disconnects the authenticated YouTube account strictly for this user."""
    from app.services.youtube_service import disconnect_youtube
    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    target_id = (email or username or identifier or "").strip()
    try:
        if target_id and target_id != "default":
            disconnect_youtube(target_id)
        return JSONResponse(
            status_code=200,
            content={"status": "success", "disconnected": True},
            headers=cors_headers
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e)},
            headers=cors_headers
        )


# ── Community Broadcast Dispatch Endpoint ───────────────────────────────────

class CommunityBroadcastPayload(BaseModel):
    handle: Optional[str] = "creator"
    creator_name: Optional[str] = "Creator"
    product_name: Optional[str] = "Community"
    subject: str
    message: str
    recipients: List[Any] = []
    reply_to: Optional[str] = None
    from_name: Optional[str] = None


@app.options("/api/community/broadcast")
def community_broadcast_options(request: Request):
    origin = get_cors_origin(request)
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )


@app.post("/api/community/broadcast")
def community_broadcast_endpoint(req: CommunityBroadcastPayload, request: Request):
    """
    Delivers a real broadcast email to community members using configured email providers
    (Brevo Port 443 HTTPS / Google SMTP / Resend / SendGrid).
    """
    origin = get_cors_origin(request)
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    if not req.subject or not req.subject.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Subject line is required."},
            headers=cors_headers
        )

    if not req.message or not req.message.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Message body is required."},
            headers=cors_headers
        )

    from app.integrations.email_provider import email_provider

    # Extract and normalize recipient email addresses
    valid_recipients = []
    seen = set()

    for item in req.recipients or []:
        raw_email = ""
        r_name = ""
        if isinstance(item, dict):
            raw_email = (item.get("email") or item.get("contactEmail") or "").strip()
            r_name = (item.get("name") or "").strip()
        elif isinstance(item, str):
            raw_email = item.strip()

        norm = raw_email.lower()
        if not norm or "@" not in norm or "." not in norm.split("@")[-1]:
            continue
        # Avoid standard mock dummy addresses
        if norm.endswith("@example.com") or norm in ("sarah@lin.dev", "alex@rivera.io"):
            continue
        if norm not in seen:
            seen.add(norm)
            valid_recipients.append({"email": norm, "name": r_name})

    # If reply_to is provided and no valid recipients were in the list, use reply_to as target
    if not valid_recipients and req.reply_to and "@" in req.reply_to:
        norm = req.reply_to.strip().lower()
        if norm not in seen:
            seen.add(norm)
            valid_recipients.append({"email": norm, "name": req.creator_name or "Creator"})

    if not valid_recipients:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "detail": "No valid recipient email addresses found. Please enter or select at least one recipient email."
            },
            headers=cors_headers
        )

    from app.services.email_template import format_luxury_html_email, _get_frontend_base

    sender_display = req.from_name or f"{req.creator_name} · {req.product_name}"
    subject_line = req.subject.strip()
    raw_message = req.message.strip()

    # Resolve live Member Dashboard destination URL for this creator
    clean_handle = (req.handle or "").lstrip("@").strip() or "creator"
    frontend_base = _get_frontend_base() or "https://app.co-launch.app"
    if "creator-forge-frontend.vercel.app" in frontend_base or not frontend_base:
        frontend_base = "https://app.co-launch.app"
    portal_url = f"{frontend_base}/member/{clean_handle}"

    # Universal placeholder replacement: replace any bracketed CTA link placeholder with the live member portal link
    raw_message = re.sub(
        r'\[Link to your documentation/GitHub/blog post here\]',
        portal_url,
        raw_message,
        flags=re.IGNORECASE
    )
    raw_message = re.sub(
        r'\[Link to Documentation/GitHub/Landing Page\]',
        portal_url,
        raw_message,
        flags=re.IGNORECASE
    )
    raw_message = re.sub(
        r'\[(?:Link to [^\]]+|[^\]]*(?:link|url|github|documentation|blog|landing|waitlist|page|here)[^\]]*)\]',
        portal_url,
        raw_message,
        flags=re.IGNORECASE
    )

    body_html = format_luxury_html_email(
        body_text=raw_message,
        subject=subject_line,
        creator_name=req.creator_name or "Creator"
    )

    successful = []
    failed = []
    errors = []

    for r in valid_recipients:
        target_email = r["email"]
        try:
            email_provider.send(
                to_email=target_email,
                subject=subject_line,
                body_html=body_html,
                body_text=raw_message,
                from_name=sender_display,
                reply_to=req.reply_to
            )
            successful.append(target_email)
            logger.info(f"[Broadcast] Successfully delivered to {target_email}")
        except Exception as err:
            err_msg = str(err)
            logger.error(f"[Broadcast] Failed delivery to {target_email}: {err_msg}")
            failed.append({"email": target_email, "error": err_msg})
            errors.append(f"{target_email}: {err_msg}")

    # Persist broadcast log in MongoDB if available
    try:
        from app.mongodb import get_mongo_db
        db = get_mongo_db()
        if db is not None:
            b_coll = db["community_broadcasts"]
            b_coll.insert_one({
                "handle": req.handle,
                "subject": subject_line,
                "message": raw_message,
                "creator_name": req.creator_name,
                "product_name": req.product_name,
                "sent_count": len(successful),
                "failed_count": len(failed),
                "recipients": successful,
                "sent_at": datetime.utcnow()
            })
    except Exception as db_err:
        logger.warning(f"[Broadcast] Failed to log broadcast in MongoDB: {db_err}")

    if not successful and failed:
        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "detail": f"Failed to deliver email: {'; '.join(errors[:3])}",
                "failed": failed,
                "sent_count": 0
            },
            headers=cors_headers
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": f"Broadcast delivered to {len(successful)} inbox(es) successfully.",
            "sent_count": len(successful),
            "failed_count": len(failed),
            "recipients": successful,
            "errors": errors if failed else []
        },
        headers=cors_headers
    )



