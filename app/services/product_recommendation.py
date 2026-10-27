"""
Module 3 — Product/Business Recommendation.

Generates tailored product/business recommendations for a creator
based on their niche, audience pain points, and demand signals.
"""
import json
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.creator import Analysis, Creator, ProductRecommendation
from app.services import audit as audit_svc


def _calculate_follower_revenue_guidance(followers: int) -> str:
    f = max(50, followers or 0)
    if f < 5_000:
        return f"strictly scale to a micro-audience of {f:,} followers, e.g. $4k-$20k ARR"
    elif f < 25_000:
        return f"strictly scale to a growing audience of {f:,} followers, e.g. $20k-$75k ARR"
    elif f < 100_000:
        return f"strictly scale to an established audience of {f:,} followers, e.g. $60k-$220k ARR"
    elif f < 500_000:
        return f"strictly scale to a mid-tier audience of {f:,} followers, e.g. $200k-$800k ARR"
    elif f < 2_000_000:
        return f"strictly scale to a macro audience of {f:,} followers, e.g. $600k-$2.5M ARR"
    else:
        return f"strictly scale to a mega audience of {f:,} followers, e.g. $2M-$10M+ ARR"


def _calibrate_revenue_for_followers(arg1, category: str = "course", arg2 = None) -> str:
    # Allow calling as either (followers, category, raw_rev) or (raw_rev, category, followers)
    if isinstance(arg1, (int, float)):
        followers = int(arg1)
        raw_rev = str(arg2 or "")
    elif isinstance(arg2, (int, float)):
        followers = int(arg2)
        raw_rev = str(arg1 or "")
    else:
        try:
            followers = int(arg1)
            raw_rev = str(arg2 or "")
        except (ValueError, TypeError):
            followers = 0
            raw_rev = str(arg1 or "")

    f = max(50, followers or 0)
    cat = (category or "").lower()

    if any(k in cat for k in ["course", "saas", "academy", "masterclass"]):
        low_mult = 0.55
        high_mult = 2.20
    elif any(k in cat for k in ["community", "membership", "hub"]):
        low_mult = 0.40
        high_mult = 1.65
    elif any(k in cat for k in ["app", "mobile"]):
        low_mult = 0.32
        high_mult = 1.35
    else:
        low_mult = 0.20
        high_mult = 0.90

    low_arr = int(f * low_mult)
    high_arr = int(f * high_mult)

    if f < 2_000:
        low_arr = max(2_000, low_arr)
        high_arr = max(8_000, high_arr)
    elif f < 10_000:
        low_arr = max(6_000, low_arr)
        high_arr = max(28_000, high_arr)
    elif f < 50_000:
        low_arr = max(22_000, low_arr)
        high_arr = max(95_000, high_arr)
    elif f < 250_000:
        low_arr = max(65_000, low_arr)
        high_arr = max(260_000, high_arr)
    elif f < 1_000_000:
        low_arr = max(160_000, low_arr)
        high_arr = max(700_000, high_arr)
    elif f < 5_000_000:
        low_arr = max(550_000, low_arr)
        high_arr = max(2_400_000, high_arr)
    else:
        low_arr = max(1_800_000, low_arr)
        high_arr = max(9_000_000, high_arr)

    def _fmt(val: int) -> str:
        if val >= 1_000_000:
            m = val / 1_000_000
            return f"${m:.1f}M".replace(".0M", "M")
        if val >= 1_000:
            k = val / 1_000
            return f"${k:.0f}k"
        return f"${val}"

    # Check if raw_rev has reasonable numbers already that align with the tier
    if raw_rev and ("$" in raw_rev or "k" in raw_rev.lower() or "m" in raw_rev.lower()):
        nums = re.findall(r"\$?\s*([\d\.]+\s*[kmbKMB]?)", raw_rev)
        if len(nums) >= 2:
            def _p(s):
                s = s.lower().replace("$", "").strip()
                if "m" in s: return float(s.replace("m","")) * 1_000_000
                if "k" in s: return float(s.replace("k","")) * 1_000
                if "b" in s: return float(s.replace("b","")) * 1_000_000_000
                try: return float(s)
                except: return 0
            n1 = _p(nums[0])
            n2 = _p(nums[1])
            # If AI estimated roughly within 0.25x - 3.5x of our benchmark, accept AI's estimate
            if n1 > 0 and n2 > 0 and 0.25 * low_arr <= n1 <= 3.5 * high_arr:
                return f"{_fmt(int(n1))}-{_fmt(int(n2))}"

    return f"{_fmt(low_arr)}-{_fmt(high_arr)}"


def _build_product_prompt(creator: Creator, analysis: Optional[Analysis]) -> str:
    demand_signals = ""
    pain_points = ""
    if analysis:
        demand_signals = json.dumps(analysis.audience_demand_signals or {}, indent=2)
        pain_points = "\n".join(f"  - {p}" for p in (analysis.audience_pain_points or []))

    f_count = creator.follower_count or 0
    rev_guidance = _calculate_follower_revenue_guidance(f_count)

    return f"""You are a product strategist helping identify the BEST business/product to build for a creator.

Creator Profile:
- Name: {creator.display_name} (@{creator.handle}) on {creator.platform}
- Followers: {f_count:,}
- Niche: {', '.join(creator.niche or []) or 'unknown'}
- Bio: {creator.bio or 'N/A'}

Audience Demand Signals:
{demand_signals or "No analysis available — use niche knowledge."}

Audience Pain Points:
{pain_points or "Unknown"}

Generate the TOP 3 product/business recommendations for this creator.
Return a JSON array with exactly 3 items, each with:
{{
  "product_name": "<name>",
  "product_category": "<category: course|community|app|physical_product|saas|coaching|newsletter|other>",
  "tagline": "<one-line tagline>",
  "description": "<2-3 sentence description>",
  "target_audience": "<who this serves>",
  "revenue_model": "<how it makes money>",
  "revenue_potential": "<estimated realistic revenue range that {rev_guidance}>",
  "rationale": "<why this fits this creator specifically>",
  "confidence_score": <float 0-1>
}}

Return ONLY valid JSON array. Rank by confidence_score descending."""


def generate_recommendations(
    db: Session,
    creator_id: str,
    actor: str = "system",
    custom_keys: Optional[dict] = None,
) -> list[ProductRecommendation]:
    creator = db.get(Creator, creator_id)
    if not creator:
        raise ValueError("Creator not found")

    analysis = (
        db.query(Analysis)
        .filter(Analysis.creator_id == creator_id)
        .order_by(Analysis.analyzed_at.desc())
        .first()
    )

    # Try to load user profile API keys if actor is a valid username
    user_keys = custom_keys
    if not user_keys and actor and actor not in ("system", "internal", "agent", "public_user"):
        from app.models.creator import UserProfile
        try:
            user = db.query(UserProfile).filter(UserProfile.username == actor).first()
            if user and user.ai_keys:
                user_keys = user.ai_keys
        except Exception as e:
            print(f"Error loading user profile keys for {actor}: {e}")

    has_gemini = settings.GEMINI_API_KEY or (user_keys.get("geminiKey") if user_keys else None)
    has_openai = settings.OPENAI_API_KEY or (user_keys.get("openaiKey") if user_keys else None)
    has_anthropic = settings.ANTHROPIC_API_KEY or (user_keys.get("anthropicKey") if user_keys else None)

    if not has_gemini and not has_openai and not has_anthropic:
        raise ValueError("AI API keys not configured. Please add an API key (GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY) in Settings.")

    prompt = _build_product_prompt(creator, analysis)
    try:
        from app.services.llm import call_llm
        raw = call_llm(prompt=prompt, max_tokens=2500, api_keys=user_keys)
    except Exception as e:
        raise ValueError(f"AI recommendation failed: {str(e)}")

    if not raw:
        raise ValueError("AI recommendation returned an empty response.")

    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        items = json.loads(match.group()) if match else []

    recs = []
    for item in items:
        calibrated_rev = _calibrate_revenue_for_followers(
            item.get("revenue_potential", ""),
            item.get("product_category", "other"),
            creator.follower_count or 0
        )
        rec = ProductRecommendation(
            creator_id=creator_id,
            product_name=item.get("product_name", "Unnamed Product"),
            product_category=item.get("product_category", "other"),
            tagline=item.get("tagline", ""),
            description=item.get("description", ""),
            target_audience=item.get("target_audience", ""),
            revenue_model=item.get("revenue_model", ""),
            revenue_potential=calibrated_rev,
            rationale=item.get("rationale", ""),
            confidence_score=float(item.get("confidence_score", 0.5)),
            status="draft",
        )
        db.add(rec)
        recs.append(rec)

    db.commit()
    for r in recs:
        db.refresh(r)

    audit_svc.log(
        db, action="product_recommendations_generated", entity_type="creator",
        entity_id=creator_id, actor=actor,
        details={"count": len(recs)},
    )
    return recs


def approve_recommendation(
    db: Session, rec_id: str, reviewer: str, notes: str = None
) -> ProductRecommendation:
    rec = db.get(ProductRecommendation, rec_id)
    if not rec:
        raise ValueError("Recommendation not found")
    rec.status = "approved"
    rec.reviewed_by = reviewer
    rec.reviewed_at = datetime.utcnow()
    db.commit()
    from app.models.audit import Review
    review = Review(
        entity_type="product_recommendation",
        entity_id=rec_id,
        reviewer=reviewer,
        decision="approved",
        notes=notes,
    )
    db.add(review)
    db.commit()
    return rec
