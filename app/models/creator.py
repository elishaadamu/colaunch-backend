import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime,
    ForeignKey, Text, Enum as SAEnum, JSON, LargeBinary,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.utcnow()


class Creator(Base):
    __tablename__ = "creators"

    id = Column(String, primary_key=True, default=_uuid)
    handle = Column(String, nullable=False, index=True)
    platform = Column(
        SAEnum("instagram", "youtube", "tiktok", "twitter", "linkedin", "podcast", name="platform_enum"),
        nullable=False,
    )
    display_name = Column(String)
    bio = Column(Text)
    profile_url = Column(String)
    avatar_url = Column(String)
    follower_count = Column(Integer, default=0)
    niche = Column(JSON, default=list)          # list of niche tags
    location = Column(String)
    website = Column(String)
    email_public = Column(String)               # publicly listed email only
    status = Column(
        SAEnum(
            "discovered", "qualified", "disqualified",
            "in_review", "approved", "rejected", "suppressed", "contacted",
            "pitched", "partnered", "launched",
            name="creator_status_enum",
        ),
        default="discovered",
        index=True,
    )
    discovery_source = Column(String)           # how we found them
    discovery_notes = Column(Text)
    engagement_score = Column(Float)            # 0-10 computed quality score
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    # relationships
    metrics_snapshots = relationship("MetricsSnapshot", back_populates="creator", cascade="all, delete-orphan")
    content_samples = relationship("ContentSample", back_populates="creator", cascade="all, delete-orphan")
    analyses = relationship("Analysis", back_populates="creator", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="creator", cascade="all, delete-orphan")
    product_recommendations = relationship("ProductRecommendation", back_populates="creator", cascade="all, delete-orphan")
    decks = relationship("Deck", back_populates="creator", cascade="all, delete-orphan")
    outreach_messages = relationship("OutreachMessage", back_populates="creator")
    threads = relationship("Thread", back_populates="creator")
    suppression_entries = relationship("SuppressionList", back_populates="creator")
    post_suggestions = relationship("PostSuggestion", back_populates="creator", cascade="all, delete-orphan")
    partnerships = relationship("Partnership", back_populates="creator", cascade="all, delete-orphan")


class MetricsSnapshot(Base):
    __tablename__ = "metrics_snapshots"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    followers = Column(Integer, default=0)
    following = Column(Integer, default=0)
    posts_count = Column(Integer, default=0)
    avg_likes = Column(Float, default=0.0)
    avg_comments = Column(Float, default=0.0)
    avg_shares = Column(Float, default=0.0)
    avg_views = Column(Float, default=0.0)
    engagement_rate = Column(Float, default=0.0)      # (likes+comments) / followers
    engagement_quality_score = Column(Float, default=0.0)  # 0-10 adjusted score
    growth_rate_30d = Column(Float, default=0.0)      # % change over 30 days
    snapshot_date = Column(DateTime, default=_now)

    creator = relationship("Creator", back_populates="metrics_snapshots")


class ContentSample(Base):
    __tablename__ = "content_samples"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    platform = Column(String)
    content_url = Column(String)
    content_type = Column(
        SAEnum("post", "video", "reel", "story", "tweet", "short", name="content_type_enum"),
        default="post",
    )
    caption = Column(Text)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    views = Column(Integer, default=0)
    top_comments = Column(JSON, default=list)   # list of comment strings
    sentiment_score = Column(Float)             # -1 to 1
    topics = Column(JSON, default=list)         # extracted topics/themes
    posted_at = Column(DateTime)
    collected_at = Column(DateTime, default=_now)

    creator = relationship("Creator", back_populates="content_samples")


class Analysis(Base):
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    analysis_type = Column(
        SAEnum("engagement", "audience_demand", "brand_fit", "overall", name="analysis_type_enum"),
        default="overall",
    )
    engagement_quality_score = Column(Float)    # 0-10
    audience_demand_signals = Column(JSON)       # dict of demand signals
    content_themes = Column(JSON, default=list)
    brand_safety_score = Column(Float)          # 0-10
    recommended_niches = Column(JSON, default=list)
    audience_pain_points = Column(JSON, default=list)
    summary = Column(Text)
    raw_output = Column(Text)                   # full AI response
    model_used = Column(String)
    analyzed_at = Column(DateTime, default=_now)

    creator = relationship("Creator", back_populates="analyses")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    contact_type = Column(
        SAEnum(
            "email", "agency", "management", "pr_firm",
            "business_inquiry_form", "social_dm", name="contact_type_enum",
        ),
        nullable=False,
    )
    value = Column(String, nullable=False)       # email address or URL
    source = Column(String)                      # WHERE we found it (bio, linktree, etc.)
    is_public = Column(Boolean, default=True)    # must always be True — no private scraping
    is_verified = Column(Boolean, default=False)
    is_valid = Column(Boolean, default=True)
    validation_notes = Column(Text)
    is_suppressed = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=_now)
    last_verified_at = Column(DateTime)

    creator = relationship("Creator", back_populates="contacts")
    outreach_messages = relationship("OutreachMessage", back_populates="contact")


class ProductRecommendation(Base):
    __tablename__ = "product_recommendations"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    product_name = Column(String, nullable=False)
    product_category = Column(String)
    tagline = Column(String)
    description = Column(Text)
    target_audience = Column(Text)
    revenue_model = Column(String)
    revenue_potential = Column(String)          # e.g. "$500k-$2M ARR"
    rationale = Column(Text)
    confidence_score = Column(Float)            # 0-1
    status = Column(
        SAEnum("draft", "approved", "rejected", name="product_status_enum"),
        default="draft",
    )
    reviewed_by = Column(String)
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, default=_now)

    creator = relationship("Creator", back_populates="product_recommendations")
    decks = relationship("Deck", back_populates="product_recommendation")
    post_suggestions = relationship("PostSuggestion", back_populates="product_recommendation", cascade="all, delete-orphan")
    partnerships = relationship("Partnership", back_populates="product_recommendation", cascade="all, delete-orphan")


class Deck(Base):
    __tablename__ = "decks"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    product_recommendation_id = Column(String, ForeignKey("product_recommendations.id"))
    title = Column(String)
    slides = Column(JSON, default=list)          # list of {title, body, notes, type}
    version = Column(Integer, default=1)
    status = Column(
        SAEnum("draft", "finalized", "sent", name="deck_status_enum"),
        default="draft",
    )
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    creator = relationship("Creator", back_populates="decks")
    product_recommendation = relationship("ProductRecommendation", back_populates="decks")
    outreach_messages = relationship("OutreachMessage", back_populates="deck")


class PostSuggestion(Base):
    __tablename__ = "post_suggestions"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    product_recommendation_id = Column(String, ForeignKey("product_recommendations.id"), nullable=False)
    hook = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    platform = Column(String, nullable=False)    # tiktok, youtube, instagram, twitter, etc.
    status = Column(
        SAEnum("draft", "approved", "queued", "posted", name="post_status_enum"),
        default="draft",
        index=True,
    )
    scheduled_for = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    creator = relationship("Creator", back_populates="post_suggestions")
    product_recommendation = relationship("ProductRecommendation", back_populates="post_suggestions")


class Partnership(Base):
    __tablename__ = "partnerships"

    id = Column(String, primary_key=True, default=_uuid)
    creator_id = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    product_recommendation_id = Column(String, ForeignKey("product_recommendations.id"), nullable=False)
    equity_share = Column(Float, default=0.5)    # e.g., 0.50 means 50% split
    monthly_revenue = Column(Float, default=0.0)
    status = Column(
        SAEnum("negotiating", "contract_signed", "in_development", "launched", "paused", name="partnership_status_enum"),
        default="negotiating",
        index=True,
    )
    notes = Column(Text)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    creator = relationship("Creator", back_populates="partnerships")
    product_recommendation = relationship("ProductRecommendation", back_populates="partnerships")


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=_uuid)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)

    # Store JSON payloads of client-side localStorage state
    creator_data = Column(JSON, nullable=True)
    calendar_data = Column(JSON, nullable=True)
    launch_pack_data = Column(JSON, nullable=True)
    studio_data = Column(JSON, nullable=True)
    chat_data = Column(JSON, nullable=True)

    # Optional: user-consented AI API keys (encrypted at rest in future)
    ai_keys = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=_uuid)
    handle = Column(String, nullable=False, index=True)
    username = Column(String, nullable=True, index=True)
    role = Column(String, nullable=False) # 'user' or 'forge'
    content = Column(Text, nullable=False)
    actions = Column(JSON, nullable=True)
    timestamp_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_now)


class MediaImage(Base):
    __tablename__ = "media_images"

    filename = Column(String, primary_key=True)
    image_bytes = Column(LargeBinary, nullable=False)
    content_type = Column(String, default="image/png")
    created_at = Column(DateTime, default=_now)


