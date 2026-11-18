from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings, BASE_DIR

fallback_sqlite_url = "sqlite:///:memory:"

def create_configured_engine(url: str):
    if "postgresql" in url or "postgres" in url:
        return create_engine(
            url,
            pool_size=20,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=60,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 10,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
            echo=False,
        )
    # In-memory volatile scratchpad only (primary database is MongoDB Atlas)
    return create_engine(
        fallback_sqlite_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )



def get_working_engine():
    db_url = settings.DATABASE_URL
    if db_url and ("postgres" in db_url or "postgresql" in db_url):
        try:
            eng = create_configured_engine(db_url)
            from sqlalchemy import text
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            return eng
        except Exception as _e:
            print(f"[DB INIT] PostgreSQL connection failed ({_e}). Falling back to in-memory database.")
            return create_configured_engine(fallback_sqlite_url)
    return create_configured_engine(db_url or fallback_sqlite_url)

engine = get_working_engine()


@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    pass

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    global engine, SessionLocal
    from app.models import creator, campaign, outreach, audit, project, niche, workflow_state  # noqa: F401 — registers models
    from sqlalchemy import text
    try:
        # Always run create_all to ensure all registered tables exist (idempotent)
        Base.metadata.create_all(bind=engine)

        # Migration: ensure campaign_kit column exists on validation_campaigns
        try:
            with engine.connect() as conn:
                is_pg = "postgresql" in str(engine.url) or "postgres" in str(engine.url)
                if is_pg:
                    conn.execute(text("ALTER TABLE validation_campaigns ADD COLUMN IF NOT EXISTS campaign_kit JSONB DEFAULT '{}'::jsonb"))
                else:
                    # SQLite: check if column exists
                    col_info = conn.execute(text("PRAGMA table_info(validation_campaigns)")).fetchall()
                    col_names = [c[1] for c in col_info]
                    if "campaign_kit" not in col_names:
                        conn.execute(text("ALTER TABLE validation_campaigns ADD COLUMN campaign_kit JSON DEFAULT '{}'"))
                conn.commit()
        except Exception as _mig_err:
            print(f"[DB INIT] Column migration notice: {_mig_err}")
    except Exception as e:
        print(f"[DB INIT] Database connection failed: {e}.")
        print("[DB INIT] Gracefully falling back to in-memory database to keep service online.")
        engine = create_configured_engine(fallback_sqlite_url)
        SessionLocal.configure(bind=engine)
        Base.metadata.create_all(bind=engine)
        try:
            with engine.connect() as conn:
                col_info = conn.execute(text("PRAGMA table_info(validation_campaigns)")).fetchall()
                col_names = [c[1] for c in col_info]
                if "campaign_kit" not in col_names:
                    conn.execute(text("ALTER TABLE validation_campaigns ADD COLUMN campaign_kit JSON DEFAULT '{}'"))
                conn.commit()
        except Exception:
            pass

    # MongoDB is the source of truth: rebuild the SQL working copy from it, then mirror every
    # committed change on the outreach tables back into MongoDB.
    try:
        from app.mongo_persistence import restore_from_mongo, register_write_through
        restore_from_mongo(engine)
        register_write_through()
    except Exception as _mp_err:
        print(f"[DB INIT] MongoDB persistence setup notice: {_mp_err}")

    # Auto-seed the 'default' campaign if missing
    from app.models.campaign import Campaign
    from app.models.niche import TargetNiche
    from app.models.creator import Creator
    from app.models.project import CoLaunchProject, ValidationCampaign
    from app.models.workflow_state import WorkflowState
    db = SessionLocal()
    try:
        default_campaign = db.query(Campaign).filter(Campaign.id == "default").first()
        if not default_campaign:
            print("[DB INIT] Seeding default campaign...")
            c = Campaign(
                id="default",
                name="Default Campaign",
                description="Default Creator Outreach Campaign",
                product_category="overall",
                status="active",
                daily_send_limit=10,
                require_human_approval=True,
                created_by="system"
            )
            db.add(c)
            db.commit()
            print("[DB INIT] Seeding default campaign completed.")
        
        # Auto-seed default target niches if missing
        niche_count = db.query(TargetNiche).count()
        if niche_count == 0:
            print("[DB INIT] Seeding default target niches in database...")
            DEFAULT_NICHES = [
                # Active defaults
                ("Tech", "tech", True, "14.2k"),
                ("Software", "tech", True, "9.8k"),
                ("SaaS", "tech", True, "6.4k"),
                ("Fintech", "business", True, "5.1k"),
                ("Productivity", "business", True, "11.3k"),
                # Available defaults
                ("AI Tools", "tech", False, "8.7k"),
                ("Creator Economy", "creative", False, "7.5k"),
                ("Gaming", "creative", False, "22.1k"),
                ("Fitness & Health", "lifestyle", False, "13.9k"),
                ("E-Commerce", "business", False, "8.2k"),
                ("Finance", "business", False, "6.9k"),
                ("Crypto & Web3", "business", False, "4.8k"),
                ("Design & Creative", "creative", False, "9.1k"),
                ("Education", "lifestyle", False, "10.5k"),
                ("Beauty & Lifestyle", "lifestyle", False, "16.7k"),
                ("Marketing", "business", False, "8.4k"),
            ]
            for name, cat, active, cnt in DEFAULT_NICHES:
                slug_id = name.lower().replace(" & ", "-").replace(" ", "-")
                db.add(TargetNiche(
                    id=slug_id,
                    name=name,
                    category=cat,
                    is_active=active,
                    count=cnt,
                ))
            db.commit()
            print("[DB INIT] Seeding default target niches completed.")

        db.commit()
    except Exception as e:
        print(f"[DB INIT] Warning: Failed to seed defaults: {e}")
        db.rollback()
    finally:
        db.close()


