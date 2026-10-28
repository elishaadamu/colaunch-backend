import app.ssl_patch  # Prevents macOS LibreSSL 2.8.3 TLS 1.3 session ticket double-free crash
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
from pydantic import BaseModel

from app.config import settings
from app.database import init_db
from app.routers import (
    creators, discovery, outreach, campaigns, decks, suppression, analytics, audit,
    public_portal, content_calendar, autonomous, projects, workflow, upload, niches
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

# ── CORS (allow Vite dev server + Vercel frontend) ───────────────────────────
_cors_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "https://creator-forge-frontend.vercel.app",
    "https://creator-forge-frontend.vercel.app/",
    "https://colaunch-web.vercel.app",
    "https://colaunch-web.vercel.app/",
]
if settings.FRONTEND_URL:
    clean_fe = settings.FRONTEND_URL.rstrip("/")
    if clean_fe not in _cors_origins:
        _cors_origins.extend([clean_fe, f"{clean_fe}/"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app/?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

import asyncio
import logging
logger = logging.getLogger("creator_forge")
from app.services.inbox_poller import start_poller_loop, stop_poller_loop
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

    # Determine base url (check env first, then dynamic origin, fallback to vercel URL)
    import os
    base_url = os.getenv("BASE_URL", "").rstrip('/')
    if not base_url:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if "localhost:8000" in base_url or "127.0.0.1:8000" in base_url:
                base_url = "https://creator-forge-frontend.vercel.app"
        else:
            base_url = "https://creator-forge-frontend.vercel.app"

    subject = "Welcome to Creator Forge!"
    body_text = (
        f"Welcome to the Forge, {req.username}!\n\n"
        "Your secure creator sandbox is ready. Launch the console to start blueprinting, scheduling, and generating your next-generation content pipeline.\n\n"
        "Features:\n"
        "- Automated Content Calendar: Generate strategically aligned content drafts in one click.\n"
        "- Monetization Blueprints: Analyze stats and auto-generate pitch decks.\n"
        "- Privacy-Enforced Sandbox: Your API keys and tokens remain local and secure.\n\n"
        f"Launch Console: {base_url}/login"
    )
    body_html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 32px 24px; border: 1px solid #1f2937; border-radius: 20px; background-color: #050505; color: #f3f4f6; box-shadow: 0 20px 40px rgba(0,0,0,0.8);">
        <!-- Logo Header -->
        <div style="text-align: center; margin-bottom: 32px; border-bottom: 1px solid #1f2937; padding-bottom: 24px;">
            <h1 style="color: #ffffff; font-size: 26px; font-weight: 800; margin: 0; letter-spacing: -0.03em; text-transform: uppercase;">CREATOR FORGE</h1>
            <p style="color: rgba(255,255,255,0.4); font-size: 12px; margin: 6px 0 0 0; letter-spacing: 0.15em; text-transform: uppercase; font-weight: 600;">Operator Console Active</p>
        </div>

        <!-- Body -->
        <div style="line-height: 1.6; font-size: 14px; color: #d1d5db; margin-bottom: 32px;">
            <p style="font-size: 18px; color: #ffffff; font-weight: 700; margin-top: 0; margin-bottom: 12px; letter-spacing: -0.02em;">Welcome to the Forge, <strong>{req.username}</strong>.</p>
            <p style="margin-top: 0; margin-bottom: 24px; color: rgba(255,255,255,0.7); font-size: 14px;">
                Your secure creator sandbox is fully provisioned. Step into your console to start blueprinting, scheduling, and generating your next-generation content pipeline.
            </p>

            <!-- Feature highlights -->
            <div style="margin-bottom: 32px;">
                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 14px; margin-bottom: 12px;">
                    <span style="font-size: 16px; margin-right: 6px;">📅</span>
                    <strong style="color: #ffffff; font-size: 13px;">Automated Content Calendar</strong>
                    <p style="margin: 4px 0 0 0; font-size: 12px; color: rgba(255,255,255,0.45);">Generate 7-day strategically aligned multi-platform content drafts in one click.</p>
                </div>
                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 14px; margin-bottom: 12px;">
                    <span style="font-size: 16px; margin-right: 6px;">💡</span>
                    <strong style="color: #ffffff; font-size: 13px;">Monetization Blueprints</strong>
                    <p style="margin: 4px 0 0 0; font-size: 12px; color: rgba(255,255,255,0.45);">Analyze channel stats to discover optimal digital products and auto-generate pitch decks.</p>
                </div>
                <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 12px; padding: 14px; margin-bottom: 12px;">
                    <span style="font-size: 16px; margin-right: 6px;">🔒</span>
                    <strong style="color: #ffffff; font-size: 13px;">Privacy-Enforced Sandbox</strong>
                    <p style="margin: 4px 0 0 0; font-size: 12px; color: rgba(255,255,255,0.45);">Your platform tokens and API credentials remain secure, private, and local to you.</p>
                </div>
            </div>

            <!-- Button -->
            <div style="text-align: center; margin-bottom: 8px;">
                <a href="{base_url}/login" target="_blank" style="display: inline-block; background-color: #ffffff; color: #000000; font-weight: 700; text-decoration: none; padding: 12px 28px; border-radius: 100px; font-size: 13px;">
                    Launch Console
                </a>
            </div>
        </div>

        <!-- Footer -->
        <div style="border-top: 1px solid #1f2937; padding-top: 24px; text-align: center;">
            <p style="font-size: 11px; color: rgba(255,255,255,0.3); margin: 0; line-height: 1.5;">
                To protect your digital identity, all platform API credentials are loaded strictly in your browser's active memory. They are never written to database tables or persistent storage.
            </p>
        </div>
    </div>
    """

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
    email: str
    password: str
    creator_data: Optional[Dict[str, Any]] = None
    calendar_data: Optional[Dict[str, Any]] = None
    launch_pack_data: Optional[Dict[str, Any]] = None
    studio_data: Optional[Dict[str, Any]] = None

class LoginRequest(BaseModel):
    identifier: str
    password: str

class SyncRequest(BaseModel):
    username: str
    creator_data: Optional[Dict[str, Any]] = None
    calendar_data: Optional[Dict[str, Any]] = None
    launch_pack_data: Optional[Dict[str, Any]] = None
    studio_data: Optional[Dict[str, Any]] = None

@app.post("/api/auth/signup")
def auth_signup(req: SignupRequest, db: Session = Depends(get_db)):
    from app.models.creator import UserProfile
    from fastapi import HTTPException
    from app.mongodb import get_collection

    # Check unique constraints in MongoDB Atlas & DB
    u_coll = get_collection("user_profiles")
    if u_coll is not None:
        m_existing = u_coll.find_one({"$or": [{"username": req.username}, {"email": req.email}]})
        if m_existing:
            raise HTTPException(status_code=400, detail="Username or email already registered")

    existing = db.query(UserProfile).filter(
        (UserProfile.username == req.username) | (UserProfile.email == req.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    user = UserProfile(
        username=req.username,
        email=req.email,
        password=req.password,
        creator_data=req.creator_data,
        calendar_data=req.calendar_data,
        launch_pack_data=req.launch_pack_data,
        studio_data=req.studio_data
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Persist directly into MongoDB Atlas user_profiles
    if u_coll is not None:
        try:
            u_coll.replace_one(
                {"username": req.username},
                {
                    "_id": req.username,
                    "username": req.username,
                    "email": req.email,
                    "password": req.password,
                    "creator_data": req.creator_data,
                    "calendar_data": req.calendar_data,
                    "launch_pack_data": req.launch_pack_data,
                    "studio_data": req.studio_data
                },
                upsert=True
            )
        except Exception:
            pass

    return {
        "status": "success",
        "username": user.username,
        "email": user.email,
        "creator_data": user.creator_data,
        "calendar_data": user.calendar_data,
        "launch_pack_data": user.launch_pack_data,
        "studio_data": user.studio_data
    }

@app.post("/api/auth/login")
def auth_login(req: LoginRequest, db: Session = Depends(get_db)):
    from app.models.creator import UserProfile
    from fastapi import HTTPException
    from app.mongodb import get_collection

    user = db.query(UserProfile).filter(
        (UserProfile.username == req.identifier) | (UserProfile.email == req.identifier)
    ).first()

    if not user:
        u_coll = get_collection("user_profiles")
        if u_coll is not None:
            m_user = u_coll.find_one({"$or": [{"username": req.identifier}, {"email": req.identifier}]})
            if m_user and m_user.get("password") == req.password:
                user = UserProfile(
                    username=m_user.get("username"),
                    email=m_user.get("email"),
                    password=m_user.get("password"),
                    creator_data=m_user.get("creator_data"),
                    calendar_data=m_user.get("calendar_data"),
                    launch_pack_data=m_user.get("launch_pack_data"),
                    studio_data=m_user.get("studio_data"),
                    ai_keys=m_user.get("ai_keys")
                )
                db.add(user)
                db.commit()
                db.refresh(user)

    if not user or user.password != req.password:
        raise HTTPException(status_code=400, detail="Invalid username/email or password credentials.")

    return {
        "status": "success",
        "username": user.username,
        "email": user.email,
        "creator_data": user.creator_data,
        "calendar_data": user.calendar_data,
        "launch_pack_data": user.launch_pack_data,
        "studio_data": user.studio_data,
        "ai_keys": user.ai_keys  # Return saved AI keys if user consented
    }

@app.post("/api/auth/sync")
def auth_sync(req: SyncRequest, db: Session = Depends(get_db)):
    from app.models.creator import UserProfile
    from fastapi import HTTPException
    from app.mongodb import get_collection

    user = db.query(UserProfile).filter(UserProfile.username == req.username).first()
    if not user:
        u_coll = get_collection("user_profiles")
        if u_coll is not None:
            m_user = u_coll.find_one({"username": req.username})
            if m_user:
                user = UserProfile(
                    username=m_user.get("username"),
                    email=m_user.get("email"),
                    password=m_user.get("password")
                )
                db.add(user)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if req.creator_data is not None:
        user.creator_data = req.creator_data
    if req.calendar_data is not None:
        user.calendar_data = req.calendar_data
    if req.launch_pack_data is not None:
        user.launch_pack_data = req.launch_pack_data
    if req.studio_data is not None:
        user.studio_data = req.studio_data

    db.commit()

    # Sync to MongoDB Atlas user_profiles
    u_coll = get_collection("user_profiles")
    if u_coll is not None:
        try:
            update_fields = {}
            if req.creator_data is not None: update_fields["creator_data"] = req.creator_data
            if req.calendar_data is not None: update_fields["calendar_data"] = req.calendar_data
            if req.launch_pack_data is not None: update_fields["launch_pack_data"] = req.launch_pack_data
            if req.studio_data is not None: update_fields["studio_data"] = req.studio_data
            if update_fields:
                u_coll.update_one({"username": req.username}, {"$set": update_fields}, upsert=True)
        except Exception:
            pass

    return {"status": "success"}


# ── AI Keys Save/Load (user-consented persistence) ────────────────────────────

class SaveAiKeysRequest(BaseModel):
    username: str
    ai_keys: Dict[str, str]  # { geminiKey, togetherKey, nvidiaKey }

class DeleteAiKeysRequest(BaseModel):
    username: str

@app.post("/api/auth/save-ai-keys")
def save_ai_keys(req: SaveAiKeysRequest, db: Session = Depends(get_db)):
    """Save AI API keys to the database (user explicitly consented)."""
    from app.models.creator import UserProfile
    from fastapi import HTTPException

    user = db.query(UserProfile).filter(UserProfile.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.ai_keys = req.ai_keys
    db.commit()
    return {"status": "success", "message": "AI keys saved securely."}

@app.post("/api/auth/delete-ai-keys")
def delete_ai_keys(req: DeleteAiKeysRequest, db: Session = Depends(get_db)):
    """Remove saved AI keys from database."""
    from app.models.creator import UserProfile
    from fastapi import HTTPException

    user = db.query(UserProfile).filter(UserProfile.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.ai_keys = None
    db.commit()
    return {"status": "success", "message": "AI keys removed from database."}

@app.get("/api/auth/load-ai-keys/{username}")
def load_ai_keys(username: str, db: Session = Depends(get_db)):
    """Load saved AI keys for a user (only returns if previously consented)."""
    from app.models.creator import UserProfile
    from fastapi import HTTPException

    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "success", "ai_keys": user.ai_keys}


@app.get("/api/auth/profile/{username}")
def get_user_profile(username: str, db: Session = Depends(get_db)):
    """Retrieve full user profile data for session synchronization."""
    from app.models.creator import UserProfile
    from app.mongodb import get_collection
    from datetime import datetime

    user = db.query(UserProfile).filter(UserProfile.username == username).first()

    # Fallback: Query MongoDB Atlas user_profiles
    if not user:
        try:
            u_coll = get_collection("user_profiles")
            if u_coll is not None:
                m_user = u_coll.find_one({"$or": [{"username": username}, {"email": username}]})
                if m_user:
                    user = UserProfile(
                        username=m_user.get("username", username),
                        email=m_user.get("email", f"{username}@gmail.com"),
                        password=m_user.get("password", ""),
                        creator_data=m_user.get("creator_data"),
                        calendar_data=m_user.get("calendar_data"),
                        launch_pack_data=m_user.get("launch_pack_data"),
                        studio_data=m_user.get("studio_data"),
                        ai_keys=m_user.get("ai_keys")
                    )
                    db.add(user)
                    db.commit()
                    db.refresh(user)
        except Exception as e:
            logger.warning(f"[Profile] MongoDB lookup notice: {e}")

    # Auto-initialize admin/creatorforgeweb profile if not present
    if not user:
        admin_email = os.getenv("ADMIN_EMAIL", "creatorforgeweb@gmail.com")
        clean_u = (username or "").lower().strip()
        if clean_u in ("creatorforgeweb", "admin") or clean_u == admin_email.split("@")[0].lower() or clean_u == admin_email.lower():
            try:
                user = UserProfile(
                    username="creatorforgeweb",
                    email=admin_email,
                    password="",
                    creator_data={},
                    calendar_data={},
                    launch_pack_data={},
                    studio_data={},
                    ai_keys={}
                )
                db.add(user)
                db.commit()
                db.refresh(user)

                u_coll = get_collection("user_profiles")
                if u_coll is not None:
                    u_coll.update_one(
                        {"username": "creatorforgeweb"},
                        {"$set": {
                            "username": "creatorforgeweb",
                            "email": admin_email,
                            "creator_data": {},
                            "calendar_data": {},
                            "launch_pack_data": {},
                            "studio_data": {},
                            "ai_keys": {},
                            "updated_at": datetime.utcnow().isoformat()
                        }},
                        upsert=True
                    )
            except Exception as e:
                logger.warning(f"[Profile] Auto-init notice: {e}")

    if not user:
        # Return empty profile rather than 404 to allow client to sync
        return {
            "status": "success",
            "username": username,
            "email": "",
            "creator_data": None,
            "calendar_data": None,
            "launch_pack_data": None,
            "studio_data": None,
            "ai_keys": None
        }

    return {
        "status": "success",
        "username": user.username,
        "email": user.email,
        "creator_data": user.creator_data,
        "calendar_data": user.calendar_data,
        "launch_pack_data": user.launch_pack_data,
        "studio_data": user.studio_data,
        "ai_keys": user.ai_keys
    }



class UpdateProfileRequest(BaseModel):
    username: str
    email: str


@app.post("/api/auth/update-profile")
def update_profile(req: UpdateProfileRequest, db: Session = Depends(get_db)):
    """Update profile email on DB and send alert if changed."""
    from app.models.creator import UserProfile
    from fastapi import HTTPException
    from app.integrations.email_provider import email_provider
    import logging

    user = db.query(UserProfile).filter(UserProfile.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_email = req.email.strip().lower()
    old_email = user.email.strip().lower()

    if new_email != old_email:
        # Check if email is already taken
        existing = db.query(UserProfile).filter(UserProfile.email == new_email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email address already registered by another account")

        user.email = new_email
        db.commit()

        # Send alert to OLD email address
        subject = "Creator Forge - Security Alert: Email Address Changed"
        body_text = f"Hello {user.username},\n\nThe email address associated with your Creator Forge account has been successfully changed from {old_email} to {new_email}.\n\nIf you did not authorize this change, please contact support immediately."
        body_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 24px; border: 1px solid #1f2937; border-radius: 16px; background-color: #0b0b0b; color: #f3f4f6; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
            <div style="text-align: center; margin-bottom: 24px; border-bottom: 1px solid #1f2937; padding-bottom: 16px;">
                <h1 style="color: #ffffff; font-size: 24px; font-weight: 800; margin: 0; letter-spacing: -0.025em; text-transform: uppercase;">CREATOR FORGE</h1>
                <p style="color: #ef4444; font-size: 12px; margin: 6px 0 0 0; text-transform: uppercase; letter-spacing: 0.1em; font-weight: bold;">Security Alert</p>
            </div>
            <div style="line-height: 1.6; font-size: 14px; color: #d1d5db;">
                <p style="font-size: 16px; color: #ffffff;">Hello <strong>{user.username}</strong>,</p>
                <p>The email address associated with your secure operator account has been changed:</p>
                <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 16px; margin: 16px 0; font-family: monospace; font-size: 13px;">
                    <span style="color: #ef4444;">- Old: {old_email}</span><br>
                    <span style="color: #10b981;">+ New: {new_email}</span>
                </div>
                <p style="color: #9ca3af; font-size: 12px;">If you did not make this change, please contact security immediately.</p>
            </div>
        </div>
        """
        try:
            email_provider.send(
                to_email=old_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text
            )
        except Exception as e:
            logging.error(f"Failed to send email change notification to {old_email}: {str(e)}")

    return {"status": "success", "email": new_email}


# ── Admin Control APIs ────────────────────────────────────────────────────────
@app.get("/api/admin/users")
def list_admin_users(db: Session = Depends(get_db)):
    """List all registered users for admin control panel."""
    from app.models.creator import UserProfile
    users = db.query(UserProfile).order_by(UserProfile.created_at.desc()).all()
    out = []
    for u in users:
        platform = None
        handle = None
        if u.creator_data and isinstance(u.creator_data, dict):
            platform = u.creator_data.get("platform")
            handle = u.creator_data.get("handle")
        
        has_ai_keys = False
        if u.ai_keys and isinstance(u.ai_keys, dict):
            has_ai_keys = any(bool(v) for v in u.ai_keys.values())

        out.append({
            "username": u.username,
            "email": u.email,
            "platform": platform,
            "handle": handle,
            "has_ai_keys": has_ai_keys,
            "created_at": u.created_at.isoformat() if u.created_at else None
        })
    return {"status": "success", "users": out}


@app.delete("/api/admin/users/{username}")
def delete_admin_user(username: str, db: Session = Depends(get_db)):
    """Delete a user profile."""
    from app.models.creator import UserProfile
    from fastapi import HTTPException
    user = db.query(UserProfile).filter(UserProfile.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"status": "success", "message": f"User {username} deleted."}


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


