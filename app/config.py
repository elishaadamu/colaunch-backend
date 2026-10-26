import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present (no external deps needed)
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    try:
        _content = _env_path.read_text(encoding="utf-8-sig")
    except Exception:
        _content = _env_path.read_text()
    for _line in _content.splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ[_k.strip()] = _v.strip()

class Settings:
    APP_NAME: str = "Creator Forge Internal Ops"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # Database (In-memory SQL cache with zero files on disk if PostgreSQL not configured)
    _db_url = os.getenv("DATABASE_URL", "sqlite:///:memory:")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    if _db_url.startswith("postgresql://") and not _db_url.startswith("postgresql+"):
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            try:
                import pg8000  # noqa: F401
                _db_url = _db_url.replace("postgresql://", "postgresql+pg8000://", 1)
            except ImportError:
                print("[CONFIG] Neither psycopg2 nor pg8000 installed. Using in-memory store.")
                _db_url = "sqlite:///:memory:"
    DATABASE_URL: str = _db_url

    # MongoDB (Primary Cloud Document Database)
    DEFAULT_MONGO_URI: str = "mongodb+srv://creatorforgeweb_db_user:radLPHdUYy7g3tGB@cluster0.nnewamw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    MONGODB_URI: str = os.getenv("MONGODB_URI", os.getenv("MONGO_URL", DEFAULT_MONGO_URI)).strip() or DEFAULT_MONGO_URI
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "creator_forge").strip() or "creator_forge"



    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-in-production-internal-ops-only")

    # Safety Controls
    DAILY_SEND_LIMIT_DEFAULT: int = int(os.getenv("DAILY_SEND_LIMIT", "10"))
    AUTO_SEND_ENABLED: bool = False   # Never auto-send; always require human approval
    ENABLE_AUTONOMOUS_BACKGROUND_SCHEDULER: bool = os.getenv("ENABLE_AUTONOMOUS_BACKGROUND_SCHEDULER", "false").lower() in ("true", "1", "yes")
    MIN_FOLLOWERS_THRESHOLD: int = 100_000
    MIN_ENGAGEMENT_SCORE: float = 3.0  # minimum quality score (0-10)

    # Follow-up Scheduler
    # FOLLOWUP_CHECK_INTERVAL_HOURS: how often the background loop checks for eligible threads.
    #   Testing: 1  |  Production: 1 (checking hourly is fine; the DELAY controls when follow-ups fire)
    FOLLOWUP_CHECK_INTERVAL_HOURS: int = int(os.getenv("FOLLOWUP_CHECK_INTERVAL_HOURS", "1"))
    # FOLLOWUP_DELAY_HOURS: minimum time after the original outreach before a follow-up is sent.
    #   Testing: 1 hour  |  Production: set to 168 (7 days) or leave per-campaign followup_delay_days
    FOLLOWUP_DELAY_HOURS: int = int(os.getenv("FOLLOWUP_DELAY_HOURS", "1"))

    # AI
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    TOGETHER_API_KEY: str = os.getenv("TOGETHER_API_KEY", "")
    APIFY_API_KEY: str = os.getenv("APIFY_API_KEY", "")
    APIFY_TIKTOK_ACTOR: str = os.getenv("APIFY_TIKTOK_ACTOR", "0FXVyOXXEmdGcV88a")
    APIFY_INSTAGRAM_ACTOR: str = os.getenv("APIFY_INSTAGRAM_ACTOR", "dSCLg0C3YEZ83HzYX")
    APIFY_YOUTUBE_ACTOR: str = os.getenv("APIFY_YOUTUBE_ACTOR", "67Q6fmd8iedTVcCwY")
    ACTIVE_AI_PROVIDER: str = os.getenv("ACTIVE_AI_PROVIDER", "openai")
    AI_MODEL: str = "gpt-4o"

    # Email / Outreach (HTTPS APIs & Google SMTP)
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
    BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
    FROM_EMAIL: str = os.getenv("FROM_EMAIL", "creatorforgeweb@gmail.com")
    FROM_NAME: str = os.getenv("FROM_NAME", "Creator Partnerships Team")
    GOOGLE_EMAIL: str = os.getenv("GOOGLE_EMAIL", "")
    GOOGLE_APP_PASSWORD: str = os.getenv("GOOGLE_APP_PASSWORD", "")
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "creatorforgeweb@gmail.com")
    RECIPIENT_EMAIL: str = os.getenv("RECIPIENT_EMAIL", "adamsfair12@gmail.com")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "https://creator-forge-frontend.vercel.app")
    BACKEND_URL: str = os.getenv("BACKEND_URL", "https://creator-forge-backend-jj1y.onrender.com")

    # =========================================================================
    # STUDIO BRANDING & LOGO (Easily customizable for emails and public pages)
    # You can change the logo URL or studio name here or via .env variables:
    # =========================================================================
    STUDIO_NAME: str = os.getenv("STUDIO_NAME", "CoLaunch")
    STUDIO_TAGLINE: str = os.getenv("STUDIO_TAGLINE", "Venture Studio & Co-Launch Incubation")
    # Default studio logo (High resolution SVG/PNG mark). Change to your custom logo URL anytime:
    STUDIO_LOGO_URL: str = os.getenv(
        "STUDIO_LOGO_URL",
        "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=240&auto=format&fit=crop&q=80"
    )

    # Integration stubs
    YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")
    INSTAGRAM_ACCESS_TOKEN: str = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    TIKTOK_API_KEY: str = os.getenv("TIKTOK_API_KEY", "")
    
    # Cloudinary Integration (Images, Videos, Media)
    CLOUDINARY_CLOUD_NAME: str = os.getenv("CLOUDINARY_CLOUD_NAME", "axk6onmw")
    CLOUDINARY_API_KEY: str = os.getenv("CLOUDINARY_API_KEY", "343558271749965")
    CLOUDINARY_API_SECRET: str = os.getenv("CLOUDINARY_API_SECRET", "")
    CLOUDINARY_URL: str = os.getenv("CLOUDINARY_URL", "")

    # Hunter.io Email Finder & Verifier
    HUNTER_API_KEY: str = os.getenv("HUNTER_API_KEY", "2d8f925fa200e614d9bafa7ed28e2e25c0d2d71b")

settings = Settings()

# Direct convenience exports for easy editing & imports
STUDIO_NAME = settings.STUDIO_NAME
STUDIO_TAGLINE = settings.STUDIO_TAGLINE
STUDIO_LOGO_URL = settings.STUDIO_LOGO_URL

