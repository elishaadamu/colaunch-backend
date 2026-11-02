# -*- coding: utf-8 -*-
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.project import (
    CoLaunchProject, ValidationPlan, ValidationCampaign,
    CreatorCampaignTask, ValidationTelemetry, ValidationGateDecision
)
from app.models.creator import Creator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


# Pydantic Schemas
class CreateProjectRequest(BaseModel):
    id: Optional[str] = None
    creatorId: Optional[str] = None
    creatorHandle: Optional[str] = None
    creatorName: Optional[str] = None
    creatorAvatar: Optional[str] = None
    creatorEmail: Optional[str] = None
    niche: Optional[Any] = None
    followers: Optional[Any] = None
    productName: Optional[str] = "New Co-Launch Venture"
    productTagline: Optional[str] = None
    product_tagline: Optional[str] = None
    diyFee: Optional[float] = None
    diy_fee: Optional[float] = None
    diyPassPrice: Optional[float] = None
    hasCustomFee: Optional[bool] = None
    has_custom_fee: Optional[bool] = None
    targetAudience: Optional[str] = None
    customer: Optional[str] = None
    problem: Optional[str] = None
    keyFeatures: Optional[List[str]] = None
    pricing: Optional[str] = None
    revenueModel: Optional[str] = None
    presaleTarget: Optional[float] = 12500.0
    selectedConcept: Optional[Dict[str, Any]] = None
    mockup: Optional[Dict[str, Any]] = None
    campaign_kit: Optional[Dict[str, Any]] = None
    campaignKit: Optional[Dict[str, Any]] = None
    recentPosts: Optional[List[Dict[str, Any]]] = None
    recent_posts: Optional[List[Dict[str, Any]]] = None
    videos: Optional[List[Dict[str, Any]]] = None
    channelUrl: Optional[str] = None
    channelDescription: Optional[str] = None
    creatorBio: Optional[str] = None
    brandColor: Optional[str] = None
    brand_color: Optional[str] = None
    colorTheme: Optional[str] = None
    color_theme: Optional[str] = None
    portalLinkSent: Optional[bool] = False
    skipCreatorEmail: Optional[bool] = False


class UpdatePlanRequest(BaseModel):
    customer: Optional[str] = None
    problem: Optional[str] = None
    offer: Optional[str] = None
    pricing: Optional[str] = None
    test_method: Optional[str] = None
    period: Optional[str] = "14 days"
    threshold: Optional[str] = "$5,000 in presales within 14 days"
    target_revenue: Optional[float] = 5000.0
    status: Optional[str] = "ready"


class UpdateCampaignRequest(BaseModel):
    product_assets: Optional[Dict[str, Any]] = None
    productAssets: Optional[Dict[str, Any]] = None
    infrastructure: Optional[Dict[str, Any]] = None
    research_survey: Optional[Dict[str, Any]] = None
    researchSurvey: Optional[Dict[str, Any]] = None
    review_status: Optional[str] = None # 'draft', 'approved', 'launched'
    reviewStatus: Optional[str] = None
    campaign_kit: Optional[Dict[str, Any]] = None
    campaignKit: Optional[Dict[str, Any]] = None
    creator_tasks: Optional[List[Dict[str, Any]]] = None
    creatorTasks: Optional[List[Dict[str, Any]]] = None
    campaign_launched: Optional[bool] = None
    campaignLaunched: Optional[bool] = None



class UpdateTaskRequest(BaseModel):
    status: Optional[str] = None # 'pending', 'today', 'completed', 'skipped'
    content_draft: Optional[str] = None
    cta_text: Optional[str] = None
    tracking_link: Optional[str] = None


class AddReservationRequest(BaseModel):
    name: str
    email: str
    amount: float
    tier: Optional[str] = "Founding Member"
    channel: Optional[str] = "instagram"


class GateDecisionRequest(BaseModel):
    decision: str # 'pass_to_phase2', 'iterate_validation', 'kill_project'
    notes: Optional[str] = None


class TrackVisitRequest(BaseModel):
    slug: Optional[str] = None
    projectId: Optional[str] = None
    clientId: Optional[str] = None
    fingerprint: Optional[str] = None
    channel: Optional[str] = "Direct / Other"
    ref: Optional[str] = None
    path: Optional[str] = None
    isNewVisitor: Optional[bool] = None


class SendCampaignPostEmailRequest(BaseModel):
    taskId: Optional[str] = None
    day: Optional[int] = None
    recipientEmail: Optional[str] = None
    includeAssets: bool = True
    caller: Optional[str] = None


class ToggleAutonomousDeliveryRequest(BaseModel):
    enabled: bool
    recipientEmail: Optional[str] = None
    preferredHour: Optional[int] = 0
    timezone: Optional[str] = None
    country: Optional[str] = None


class StartCampaignSimulationRequest(BaseModel):
    recipientEmail: Optional[str] = None
    intervalSeconds: Optional[int] = 42
    totalPosts: Optional[int] = None



def _sync_proj_to_mongo(proj_data: Dict[str, Any]):
    """Mirror project data directly into MongoDB co_launch_projects collection."""
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None and proj_data and "id" in proj_data:
            doc = dict(proj_data)
            doc["_id"] = doc["id"]
            coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
    except Exception as e:
        logger.debug(f"[MongoDB] Project sync notice: {e}")


def _format_project_response(proj: CoLaunchProject) -> Dict[str, Any]:
    plan = proj.validation_plan
    campaign = proj.validation_campaign
    telemetry = proj.telemetry
    tasks = [
        {
            "id": t.id,
            "dayNumber": t.day_number,
            "channel": t.channel,
            "title": t.task_title,
            "content": t.content_draft,
            "cta": t.cta_text,
            "trackingLink": t.tracking_link,
            "mediaPrompt": t.media_prompt,
            "status": t.status,
            "completedAt": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in (proj.creator_tasks or [])
    ]
    gate_decisions = [
        {
            "id": g.id,
            "decision": g.decision,
            "targetRevenue": g.target_revenue,
            "achievedRevenue": g.achieved_revenue,
            "backersCount": g.backers_count,
            "conversionRate": g.conversion_rate,
            "gateStatus": g.gate_status,
            "notes": g.gate_notes,
            "decidedAt": g.decided_at.isoformat() if g.decided_at else None,
        }
        for g in (proj.gate_decisions or [])
    ]

    raw_meta = proj.metadata_info
    if isinstance(raw_meta, str):
        try:
            meta_dict = json.loads(raw_meta)
        except Exception:
            meta_dict = {}
    elif isinstance(raw_meta, dict):
        meta_dict = dict(raw_meta)
    else:
        meta_dict = {}

    # Check whether this project has an explicit custom fee override
    has_custom_fee = bool(meta_dict.get("hasCustomFee") is True or meta_dict.get("has_custom_fee") is True)

    fee_candidate = None
    if has_custom_fee:
        fee_candidate = meta_dict.get("diy_fee")
        if fee_candidate is None:
            fee_candidate = meta_dict.get("diyFee")
        if fee_candidate is None:
            fee_candidate = meta_dict.get("diyPassPrice")
        if fee_candidate is None and meta_dict.get("diy_subscription"):
            sub = meta_dict.get("diy_subscription")
            if isinstance(sub, dict):
                fee_candidate = sub.get("amount")
        if fee_candidate is None and meta_dict.get("diySubscription"):
            sub = meta_dict.get("diySubscription")
            if isinstance(sub, dict):
                fee_candidate = sub.get("amount")

    resolved_fee = None
    if has_custom_fee and fee_candidate is not None:
        try:
            resolved_fee = float(fee_candidate)
        except (ValueError, TypeError):
            resolved_fee = None

    if resolved_fee is None:
        # Check global workflow state for default pass fee (source of truth)
        try:
            from app.models.workflow_state import WorkflowState
            from app.database import SessionLocal
            with SessionLocal() as s:
                ws = s.get(WorkflowState, "default")
                if ws and ws.extra_state:
                    wf_fee = ws.extra_state.get("default_pass_price") or ws.extra_state.get("cobuilder_pass_price")
                    if wf_fee is not None:
                        resolved_fee = float(wf_fee)
                if resolved_fee is None and ws and getattr(ws, "default_pass_price", None) is not None:
                    resolved_fee = float(ws.default_pass_price)
        except Exception:
            pass

    if resolved_fee is None:
        try:
            from app.mongodb import get_collection
            coll = get_collection("workflow_states")
            if coll is not None:
                doc = coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]})
                if doc:
                    wf_fee = doc.get("default_pass_price") or doc.get("cobuilder_pass_price")
                    if wf_fee is None and doc.get("extra_state"):
                        wf_fee = doc["extra_state"].get("default_pass_price") or doc["extra_state"].get("cobuilder_pass_price")
                    if wf_fee is not None:
                        resolved_fee = float(wf_fee)
        except Exception:
            pass

    if resolved_fee is None:
        resolved_fee = 199.0
        has_custom_fee = False

    meta_dict["diy_fee"] = resolved_fee
    meta_dict["diyFee"] = resolved_fee
    meta_dict["diyPassPrice"] = resolved_fee
    meta_dict["hasCustomFee"] = has_custom_fee

    return {
        "id": proj.id,
        "creatorId": proj.creator_id,
        "creatorHandle": proj.creator_handle,
        "creatorName": proj.creator_name,
        "creatorAvatar": proj.creator_avatar,
        "creatorEmail": proj.creator_email,
        "niche": proj.niche,
        "followers": proj.followers,
        "productName": proj.product_name,
        "productTagline": proj.product_tagline,
        "targetAudience": proj.target_audience,
        "pricing": proj.pricing,
        "revenueModel": proj.revenue_model,
        "currentPhase": proj.current_phase,
        "currentStep": proj.current_step,
        "status": proj.status,
        "presaleTarget": proj.presale_target,
        "currentPresales": proj.current_presales,
        "visitors": proj.visitors,
        "conversionRate": proj.conversion_rate,
        "portalToken": proj.portal_token,
        "portalLinkSent": proj.portal_link_sent,
        "portalLinkSentTo": proj.portal_link_sent_to,
        "portalLinkSentAt": proj.portal_link_sent_at.isoformat() if proj.portal_link_sent_at else None,
        "selectedConcept": proj.selected_concept,
        "metadataInfo": meta_dict,
        "projectFiles": meta_dict.get("project_files", []),
        "messages": meta_dict.get("messages", []),
        "activityLogs": meta_dict.get("activity_logs", []),
        "adminActivity": meta_dict.get("activity_logs", []),
        "aiActivity": meta_dict.get("activity_logs", []),
        "reservations": (telemetry.reservations if telemetry else []) or [],
        "buildPlanApproved": bool(meta_dict.get("buildPlanApproved") or meta_dict.get("build_plan_approved")),
        "buildCompleted": bool(meta_dict.get("buildCompleted") or meta_dict.get("build_completed") or meta_dict.get("mvpBuildDone") or (proj.current_step in ["beta", "gate"]) or (proj.current_phase or 1) > 2),
        "mvpBuildDone": bool(meta_dict.get("mvpBuildDone") or meta_dict.get("mvp_build_done") or meta_dict.get("buildCompleted") or (proj.current_step in ["beta", "gate"]) or (proj.current_phase or 1) > 2),
        "betaTestingCompleted": bool(meta_dict.get("betaTestingCompleted") or meta_dict.get("beta_testing_completed") or meta_dict.get("betaApproved") or (proj.current_step in ["gate"]) or (proj.current_phase or 1) > 2),
        "betaApproved": bool(meta_dict.get("betaApproved") or meta_dict.get("beta_approved") or meta_dict.get("betaTestingCompleted") or (proj.current_step in ["gate"]) or (proj.current_phase or 1) > 2),
        "p1Complete": bool(meta_dict.get("p1Complete") or (proj.current_phase or 1) > 1),
        "phase1Passed": bool(meta_dict.get("phase1Passed") or (proj.current_phase or 1) > 1),
        "p2Complete": bool(meta_dict.get("p2Complete") or (proj.current_phase or 1) > 2),
        "phase2Passed": bool(meta_dict.get("phase2Passed") or (proj.current_phase or 1) > 2),
        "mvpBuildPlan": meta_dict.get("mvp_build_plan") or meta_dict.get("mvpBuildPlan"),
        "engineeringTasks": meta_dict.get("engineering_tasks") or meta_dict.get("engineeringTasks", []),
        "qaResults": meta_dict.get("qa_results") or meta_dict.get("qaResults"),
        "betaFeedback": meta_dict.get("beta_feedback") or meta_dict.get("betaFeedback", []),
        "feedbackClusters": meta_dict.get("feedback_clusters") or meta_dict.get("feedbackClusters") or (telemetry.feedback_clusters if telemetry else []) or [],
        "experimentsData": (
            meta_dict.get("experimentsData") or
            meta_dict.get("experiments_data") or
            meta_dict.get("experiments") or
            ({"experiments": telemetry.experiments, "performanceAudit": meta_dict.get("performanceAudit")} if (telemetry and telemetry.experiments) else None)
        ),
        "readinessReport": meta_dict.get("readiness_report") or meta_dict.get("readinessReport"),
        "appliedPatches": meta_dict.get("applied_patches") or meta_dict.get("appliedPatches", []),
        "mvpVersion": meta_dict.get("mvp_version") or meta_dict.get("mvpVersion", "v1.0.0-MVP"),
        "launchStrategy": meta_dict.get("launch_strategy") or meta_dict.get("launchStrategy"),
        "creatorAssets": meta_dict.get("creator_assets") or meta_dict.get("creatorAssets"),
        "launchTelemetry": meta_dict.get("launch_telemetry") or meta_dict.get("launchTelemetry"),
        "channelStats": meta_dict.get("channel_stats") or meta_dict.get("channelStats", []),
        "launchManagerData": meta_dict.get("launch_manager_data") or meta_dict.get("launchManagerData"),
        "dispatchedActions": meta_dict.get("dispatched_actions") or meta_dict.get("dispatchedActions", []),
        "launchReport": meta_dict.get("launch_report") or meta_dict.get("launchReport"),
        "launchStatus": meta_dict.get("launch_status") or meta_dict.get("launchStatus", "PREP"),
        "productInfrastructure": meta_dict.get("product_infrastructure") or meta_dict.get("productInfrastructure"),
        "diySubscription": meta_dict.get("diy_subscription") or meta_dict.get("diySubscription"),
        "isDIY": bool(meta_dict.get("is_diy") or meta_dict.get("isDIY") or ((meta_dict.get("diy_subscription") or {}).get("active") if isinstance(meta_dict.get("diy_subscription"), dict) else False) or ((meta_dict.get("diySubscription") or {}).get("active") if isinstance(meta_dict.get("diySubscription"), dict) else False)),
        "diyOfferStatus": meta_dict.get("diy_offer_status") or meta_dict.get("diyOfferStatus") or "offer_sent",
        "diyOfferSentAt": meta_dict.get("diy_offer_sent_at") or meta_dict.get("diyOfferSentAt"),
        "diyFee": resolved_fee,
        "diyPassPrice": resolved_fee,
        "hasCustomFee": has_custom_fee,
        "recentPosts": meta_dict.get("recent_posts") or meta_dict.get("recentPosts") or meta_dict.get("videos") or [],
        "videos": meta_dict.get("recent_posts") or meta_dict.get("recentPosts") or meta_dict.get("videos") or [],
        "channelUrl": meta_dict.get("channel_url") or meta_dict.get("channelUrl") or (f"https://www.youtube.com/@{proj.creator_handle.lstrip('@')}" if proj.creator_handle else None),
        "channelDescription": meta_dict.get("channel_description") or meta_dict.get("channelDescription") or "",
        "currentPresales": float(proj.current_presales or 0.0),
        "visitors": int(proj.visitors or 0),
        "conversionRate": float(proj.conversion_rate or 0.0),
        "createdAt": proj.created_at.isoformat() if proj.created_at else None,
        "updatedAt": proj.updated_at.isoformat() if proj.updated_at else None,
        # Step 1
        "validationPlan": {
            "id": plan.id,
            "customer": plan.customer,
            "problem": plan.problem,
            "offer": plan.offer,
            "pricing": plan.pricing,
            "testMethod": plan.test_method,
            "period": plan.period,
            "threshold": plan.threshold,
            "targetRevenue": plan.target_revenue,
            "status": plan.status,
        } if plan else None,
        # Step 2
        "validationCampaign": {
            "id": campaign.id,
            "productAssets": campaign.product_assets,
            "infrastructure": campaign.infrastructure,
            "researchSurvey": campaign.research_survey,
            "campaignKit": (
                (campaign.campaign_kit if campaign and getattr(campaign, "campaign_kit", None) else None) or
                (proj.metadata_info or {}).get("campaign_kit") or
                (proj.metadata_info or {}).get("campaignKit") or
                (campaign.product_assets.get("campaign_kit") if campaign and isinstance(campaign.product_assets, dict) else None)
            ),
            "reviewStatus": campaign.review_status,
            "approvedAt": campaign.approved_at.isoformat() if campaign.approved_at else None,
        } if campaign else None,
        "campaignKit": (
            (campaign.campaign_kit if campaign and getattr(campaign, "campaign_kit", None) else None) or
            (proj.metadata_info or {}).get("campaign_kit") or
            (proj.metadata_info or {}).get("campaignKit") or
            (campaign.product_assets.get("campaign_kit") if campaign and isinstance(campaign.product_assets, dict) else None) or
            (campaign.product_assets.get("campaignKit") if campaign and isinstance(campaign.product_assets, dict) else None) or
            (campaign.product_assets if campaign and isinstance(campaign.product_assets, dict) and campaign.product_assets.get("postingSchedule") else None)
        ),
        "campaignLaunched": bool(
            (campaign and campaign.campaign_kit and isinstance(campaign.campaign_kit, dict) and bool(
                campaign.campaign_kit.get("postingSchedule") or
                campaign.campaign_kit.get("announcementPost") or
                campaign.campaign_kit.get("storySequence") or
                campaign.campaign_kit.get("videoScript") or
                campaign.campaign_kit.get("newsletterDraft")
            )) or
            (proj.metadata_info and isinstance(proj.metadata_info, dict) and bool(
                (proj.metadata_info.get("campaign_kit") or {}).get("postingSchedule") or
                (proj.metadata_info.get("campaign_kit") or {}).get("announcementPost")
            ))
        ),
        "campaignAssetsGenerated": bool(
            (campaign and campaign.campaign_kit and isinstance(campaign.campaign_kit, dict) and bool(
                campaign.campaign_kit.get("postingSchedule") or
                campaign.campaign_kit.get("announcementPost") or
                campaign.campaign_kit.get("storySequence") or
                campaign.campaign_kit.get("videoScript") or
                campaign.campaign_kit.get("newsletterDraft")
            )) or
            (proj.metadata_info and isinstance(proj.metadata_info, dict) and bool(
                (proj.metadata_info.get("campaign_kit") or {}).get("postingSchedule") or
                (proj.metadata_info.get("campaign_kit") or {}).get("announcementPost")
            ))
        ),
        "surveyData": campaign.research_survey if campaign else None,
        "surveyResponses": (
            (campaign.research_survey.get("responses") if campaign and isinstance(campaign.research_survey, dict) else None) or
            (proj.metadata_info or {}).get("survey_responses") or
            (proj.metadata_info or {}).get("surveyResponses") or
            []
        ),
        "surveyAnalysis": (
            (campaign.research_survey.get("analysis") if campaign and isinstance(campaign.research_survey, dict) else None) or
            (proj.metadata_info or {}).get("survey_analysis") or
            (proj.metadata_info or {}).get("surveyAnalysis") or
            None
        ),
        "infrastructure": campaign.infrastructure if campaign else None,
        # Step 3
        "creatorTasks": tasks,
        # Step 4
        "telemetry": {
            "id": telemetry.id,
            "visitors": telemetry.visitors,
            "views": telemetry.views,
            "ctr": telemetry.ctr,
            "signups": telemetry.signups,
            "presalesCount": telemetry.presales_count,
            "presalesRevenue": telemetry.presales_revenue,
            "conversionRate": telemetry.conversion_rate,
            "reservations": telemetry.reservations or [],
            "channelAttribution": telemetry.channel_attribution or {},
            "experiments": telemetry.experiments or [],
            "feedbackClusters": (proj.metadata_info or {}).get("feedback_clusters") or (telemetry.feedback_clusters if telemetry else []) or [],
        } if telemetry else None,
        # Step 5
        "gateDecisions": gate_decisions,
    }
    _sync_proj_to_mongo(res)
    return res


class RecordPreorderRequest(BaseModel):
    projectId: Optional[str] = None
    slug: Optional[str] = None
    creatorHandle: Optional[str] = None
    name: str
    email: str
    amount: float
    tier: Optional[str] = "Founding Pass"
    paymentMethod: Optional[str] = "Stripe"
    channel: Optional[str] = "direct"
    txId: Optional[str] = None


class RecordSurveyResponseRequest(BaseModel):
    projectId: Optional[str] = None
    slug: Optional[str] = None
    creatorHandle: Optional[str] = None
    name: Optional[str] = None
    respondentName: Optional[str] = None
    email: Optional[str] = None
    respondentEmail: Optional[str] = None
    rating: Optional[int] = None
    intentScore: Optional[int] = None
    answers: Optional[Dict[str, Any]] = None
    submittedAt: Optional[str] = None



class LogActivityRequest(BaseModel):
    action: str
    details: Optional[str] = None
    category: Optional[str] = "admin_action"
    step: Optional[str] = "plan"
    phase: Optional[int] = 1


def parse_concept_pricing_details(pricing_str: str) -> Dict[str, Any]:
    """
    Intelligently extracts founding price, deposit price, starter price, and tiers
    from concept pricing strings like:
      - '$59/mo Membership • $199 One-time Annual Access'
      - '$19/mo Starter • $49/mo Pro'
      - '$29/mo Membership • $299 Lifetime Access'
      - '$49/mo Membership • $149/mo VIP Mastermind'
      - '$89'
    """
    if not pricing_str:
        return {
            "founding_price": 99,
            "deposit_price": 19,
            "starter_price": 29,
            "pro_price": 79,
            "pricing_tiers": [
                {"name": "Founding Member Pass", "price": 99, "period": "lifetime"},
                {"name": "Starter Plan", "price": 29, "period": "month"},
                {"name": "Pro Builder", "price": 79, "period": "month"}
            ],
            "pricing_config": {"foundingPrice": 99, "depositPrice": 19}
        }

    import re
    raw = str(pricing_str).replace(",", "").strip()
    segments = [s.strip() for s in re.split(r"[•|;]", raw) if s.strip()]
    extracted = []
    for s in segments:
        m = re.search(r"\$(\d+)", s)
        if m:
            price = int(m.group(1))
            is_monthly = bool(re.search(r"/mo|per month|monthly", s, re.IGNORECASE))
            is_annual = bool(re.search(r"annual|one-time|lifetime|access|pass|year|/yr", s, re.IGNORECASE))
            is_vip = bool(re.search(r"vip|mastermind|founding", s, re.IGNORECASE))
            name = re.sub(r"\$(\d+)", "", s).replace("/", " ").strip()
            name = re.sub(r"^\s*(?:mo|per month)\s*", "", name, flags=re.IGNORECASE).strip()
            name = re.sub(r"\s+", " ", name)
            if not name or name.lower() in ["plan", "tier"]:
                name = "Monthly Membership" if is_monthly else ("Founding Annual Pass" if is_annual else "Standard Plan")
            elif is_monthly and not any(w in name.lower() for w in ["monthly", "month", "/mo"]):
                name = f"{name} (Monthly)"
            extracted.append({
                "raw": s,
                "price": price,
                "name": name,
                "is_monthly": is_monthly,
                "is_annual": is_annual,
                "is_vip": is_vip,
                "period": "month" if is_monthly else ("annual" if is_annual else "one-time")
            })

    if not extracted:
        all_prices = [int(p) for p in re.findall(r"\$(\d+)", raw)]
        for idx, p in enumerate(all_prices):
            extracted.append({
                "raw": f"${p}",
                "price": p,
                "name": "Starter Plan" if idx == 0 else "Founding Tier",
                "is_monthly": False,
                "is_annual": idx > 0,
                "is_vip": False,
                "period": "one-time"
            })

    if not extracted:
        digits = re.search(r"(\d+)", raw)
        base = int(digits.group(1)) if digits else 99
        dep = max(9, int(round(base * 0.2)))
        return {
            "founding_price": base,
            "deposit_price": dep,
            "starter_price": max(9, int(round(base * 0.3))),
            "pro_price": max(base, int(round(base * 0.7))),
            "pricing_tiers": [{"name": "Founding Member Pass", "price": base, "period": "lifetime"}],
            "pricing_config": {"foundingPrice": base, "depositPrice": dep}
        }

    annual_or_one_time = next((t for t in extracted if t["is_annual"] or t["is_vip"]), None)
    monthly = next((t for t in extracted if t["is_monthly"]), None)

    if len(extracted) == 1:
        founding_price = extracted[0]["price"]
        starter_price = extracted[0]["price"] if extracted[0]["is_monthly"] else max(9, int(round(founding_price * 0.3)))
        pro_price = max(starter_price * 2, int(round(founding_price * 0.7)))
    elif annual_or_one_time and monthly:
        founding_price = annual_or_one_time["price"]
        starter_price = monthly["price"]
        pro_price = founding_price
    else:
        sorted_tiers = sorted(extracted, key=lambda x: x["price"])
        lowest = sorted_tiers[0]
        highest = sorted_tiers[-1]
        starter_price = lowest["price"]
        if highest["price"] >= 80:
            founding_price = highest["price"]
        else:
            founding_price = max(highest["price"] * 2, 89)
        pro_price = highest["price"]

    deposit_price = max(9, int(round(founding_price * 0.2)))

    pricing_tiers = []
    for t in extracted:
        t_name = t["name"]
        if t["price"] == founding_price and not any(k in t_name.lower() for k in ["founding", "annual", "lifetime", "pass"]):
            t_name = f"Founding Pass ({t_name})"
        pricing_tiers.append({
            "name": t_name,
            "price": t["price"],
            "period": t["period"]
        })

    return {
        "founding_price": founding_price,
        "deposit_price": deposit_price,
        "starter_price": starter_price,
        "pro_price": pro_price,
        "pricing_tiers": pricing_tiers,
        "pricing_config": {"foundingPrice": founding_price, "depositPrice": deposit_price}
    }


def _normalize_mongo_project_dict(d: Dict[str, Any], default_fee: float = 50.0) -> Dict[str, Any]:
    if not d:
        return d
    d.pop("_id", None)

    # 1. Normalize pricing / DIY pass price
    cur_fee = d.get("diyFee") if d.get("diyFee") is not None else d.get("diyPassPrice")
    if cur_fee is None and isinstance(d.get("metadataInfo"), dict):
        cur_fee = d["metadataInfo"].get("diy_fee") if d["metadataInfo"].get("diy_fee") is not None else d["metadataInfo"].get("diyFee")
        if cur_fee is None:
            cur_fee = d["metadataInfo"].get("diyPassPrice")
    fee_to_use = cur_fee if cur_fee is not None else default_fee
    d["diyFee"] = fee_to_use
    d["diyPassPrice"] = fee_to_use
    if isinstance(d.get("metadataInfo"), dict):
        d["metadataInfo"]["diy_fee"] = fee_to_use
        d["metadataInfo"]["diyFee"] = fee_to_use
        d["metadataInfo"]["diyPassPrice"] = fee_to_use

    # 1b. Normalize brand color & theme
    brand_col = d.get("brandColor") or d.get("brand_color") or (d.get("selectedConcept") or {}).get("brandColor") or "#c81e3c"
    color_thm = d.get("colorTheme") or d.get("color_theme") or (d.get("selectedConcept") or {}).get("colorTheme") or "crimson"
    d["brandColor"] = brand_col
    d["brand_color"] = brand_col
    d["colorTheme"] = color_thm
    d["color_theme"] = color_thm

    # 2. Extract & link campaign kit if missing or incomplete
    meta = d.get("metadata_info") or d.get("metadataInfo") or {}
    kit = d.get("campaignKit") or d.get("campaign_kit") or meta.get("campaign_kit") or meta.get("campaignKit")

    if not kit or not isinstance(kit, dict) or not kit.get("postingSchedule"):
        try:
            from app.mongodb import get_collection
            vc_coll = get_collection("validation_campaigns")
            p_id = d.get("id")
            if p_id:
                vc = vc_coll.find_one({"$or": [{"project_id": p_id}, {"id": f"vc_{p_id}"}, {"id": p_id}]})
                if vc:
                    kit = vc.get("campaign_kit") or vc.get("campaignKit") or kit
        except Exception:
            pass

    if kit and isinstance(kit, dict):
        schedule = kit.get("postingSchedule")
        if isinstance(schedule, list):
            for idx, t in enumerate(schedule):
                t["day"] = idx + 1
                t["dayNumber"] = idx + 1
                t["day_number"] = idx + 1
                t["milestoneNumber"] = idx + 1
                t["spacingNotice"] = f"Day {idx + 1} of {len(schedule)}"
        
        # Standardize autonomousEmailDelivery configuration
        auto_del = kit.get("autonomousEmailDelivery")
        if not auto_del or not isinstance(auto_del, dict):
            auto_del = {}
        target_recip = (
            auto_del.get("recipientEmail")
            or d.get("creatorEmail")
            or d.get("creator_email")
            or d.get("email")
            or ""
        )
        if target_recip:
            auto_del["recipientEmail"] = target_recip
        if "enabled" not in auto_del:
            auto_del["enabled"] = False
            auto_del["intervalHours"] = 24
            auto_del["intervalSeconds"] = 86400
            auto_del["cadence"] = "24_hours"
            auto_del["dispatchTime"] = "Every 24 Hours"
        else:
            auto_del["enabled"] = bool(auto_del.get("enabled"))
        kit["autonomousEmailDelivery"] = auto_del

        d["campaignKit"] = kit
        d["campaign_kit"] = kit
        if "validationCampaign" in d and isinstance(d["validationCampaign"], dict):
            d["validationCampaign"]["campaignKit"] = kit
            d["validationCampaign"]["campaign_kit"] = kit
        if not d.get("creatorTasks") and schedule:
            d["creatorTasks"] = schedule

    # 3. Synchronize naming aliases
    p_name = d.get("productName") or d.get("title") or d.get("product_name")
    if p_name:
        d["productName"] = p_name
        d["title"] = p_name
        d["product_name"] = p_name
    d["slug"] = (d.get("slug") or p_name or "product").lower().replace(" ", "-").replace("'", "")

    # 4. Synchronize & format pricing string from creator's selected concept
    sel_c = (
        d.get("selectedConcept") or
        d.get("selected_concept") or
        (d.get("metadataInfo") or {}).get("selectedConcept") or
        (d.get("metadata_info") or {}).get("selected_concept")
    )
    if not sel_c and isinstance(d.get("concepts"), list):
        sel_c = next((c for c in d["concepts"] if isinstance(c, dict) and c.get("selected")), None)
        if not sel_c and len(d["concepts"]) > 0 and isinstance(d["concepts"][0], dict):
            sel_c = d["concepts"][0]

    raw_p = str(d.get("pricing") or "").strip()
    if isinstance(sel_c, dict):
        c_pricing = str(sel_c.get("pricing") or "").strip()
        if c_pricing:
            raw_p = c_pricing
        elif raw_p:
            sel_c["pricing"] = raw_p
        
        # Synchronize concept details into top-level project fields
        if sel_c.get("name") and not d.get("productName"):
            d["productName"] = sel_c["name"]
            d["product_name"] = sel_c["name"]
        if sel_c.get("tagline") and not d.get("productTagline"):
            d["productTagline"] = sel_c["tagline"]
            d["product_tagline"] = sel_c["tagline"]
        if sel_c.get("customer") and not d.get("targetAudience"):
            d["targetAudience"] = sel_c["customer"]
            d["target_audience"] = sel_c["customer"]
            d["customer"] = sel_c["customer"]
        if sel_c.get("problem") and not d.get("problem"):
            d["problem"] = sel_c["problem"]
        if sel_c.get("keyFeatures") and not d.get("keyFeatures"):
            d["keyFeatures"] = sel_c["keyFeatures"]

        d["selectedConcept"] = sel_c
        d["selected_concept"] = sel_c

    if not raw_p:
        raw_p = "$29/mo Starter • $79/mo Pro"
    elif raw_p.startswith("/mo"):
        raw_p = f"$29{raw_p}"
    elif "$" not in raw_p and ("/mo" in raw_p or "Access" in raw_p or "Pro" in raw_p or "Starter" in raw_p):
        raw_p = f"${raw_p}"
    d["pricing"] = raw_p

    # Synchronize validationPlan.pricing
    if isinstance(d.get("validationPlan"), dict):
        d["validationPlan"]["pricing"] = raw_p
        threshold = str(d["validationPlan"].get("threshold") or "").strip()
        if threshold.startswith(",500") or threshold == ",500 in presales within 14 days":
            d["validationPlan"]["threshold"] = "$12,500 in presales within 14 days"

    # 5. Synchronize dynamic pricingConfig and pricingTiers
    parsed_pricing = parse_concept_pricing_details(raw_p)
    p_cfg = parsed_pricing["pricing_config"]
    p_tiers = parsed_pricing["pricing_tiers"]
    founding_p = parsed_pricing["founding_price"]
    deposit_p = parsed_pricing["deposit_price"]

    d["pricingConfig"] = p_cfg
    d["pricing_config"] = p_cfg
    d["pricingTiers"] = p_tiers
    d["pricing_tiers"] = p_tiers

    if "validationCampaign" in d and isinstance(d["validationCampaign"], dict):
        va = d["validationCampaign"].get("productAssets") or d["validationCampaign"].get("product_assets")
        if not isinstance(va, dict):
            va = {}
        existing_cfg = va.get("pricingConfig")
        if not existing_cfg or existing_cfg.get("foundingPrice") in (89, 29, 9919, 177) or existing_cfg.get("foundingPrice") != founding_p:
            va["pricingConfig"] = p_cfg
            va["pricingTiers"] = p_tiers
        d["validationCampaign"]["productAssets"] = va
        d["validationCampaign"]["product_assets"] = va

    if kit and isinstance(kit, dict):
        existing_k_cfg = kit.get("pricingConfig")
        if not existing_k_cfg or existing_k_cfg.get("foundingPrice") in (89, 29, 9919, 177) or existing_k_cfg.get("foundingPrice") != founding_p:
            kit["pricingConfig"] = p_cfg
        if "landingPageCopy" in kit and isinstance(kit["landingPageCopy"], dict):
            kit["landingPageCopy"]["ctaText"] = f"Claim Founding Access (${founding_p})"
            kit["landingPageCopy"]["reservationText"] = f"Reserve with ${deposit_p} Deposit"
            if not kit["landingPageCopy"].get("bulletPoints") or any("$89" in str(b) for b in kit["landingPageCopy"].get("bulletPoints", [])):
                kit["landingPageCopy"]["bulletPoints"] = [
                    "Automate repetitive workflows with tailored software",
                    "Direct private Slack & alpha advisory council access",
                    f"50% lifetime discount locked in forever (${founding_p}/yr)"
                ]
        for copy_key in ("announcementPost", "newsletterDraft", "videoScript", "directMessageScript"):
            copy_val = kit.get(copy_key)
            if isinstance(copy_val, str):
                new_copy = copy_val
                if "($89)" in new_copy:
                    new_copy = new_copy.replace("($89)", f"(${founding_p})")
                if "$89" in new_copy:
                    new_copy = new_copy.replace("$89", f"${founding_p}")
                if "($177)" in new_copy:
                    new_copy = new_copy.replace("($177)", f"(${founding_p})")
                if "$177" in new_copy:
                    new_copy = new_copy.replace("$177", f"${founding_p}")
                if "$18" in new_copy and deposit_p != 18:
                    new_copy = new_copy.replace("$18", f"${deposit_p}")
                kit[copy_key] = new_copy

        schedule = kit.get("postingSchedule")
        if isinstance(schedule, list):
            for t in schedule:
                if isinstance(t, dict):
                    cta_val = str(t.get("cta") or "")
                    if "($89)" in cta_val:
                        t["cta"] = cta_val.replace("($89)", f"(${founding_p})")
                    elif "($177)" in cta_val:
                        t["cta"] = cta_val.replace("($177)", f"(${founding_p})")

    # 6. Synchronize and prioritize real buyer reservations from telemetry
    telem = d.get("telemetry") or {}
    telem_res = telem.get("reservations") if isinstance(telem.get("reservations"), list) else []
    top_res = d.get("reservations") if isinstance(d.get("reservations"), list) else []
    if telem_res:
        d["reservations"] = telem_res
    elif top_res:
        d["reservations"] = top_res
        if "reservations" not in telem:
            telem["reservations"] = top_res
            d["telemetry"] = telem

    return d


_project_cache: Dict[str, Dict[str, Any]] = {}


def _find_mongo_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Helper to locate project in MongoDB Atlas by any identifier with in-memory caching fallback."""
    if not project_id:
        return None
    clean_target = str(project_id).replace("@", "").lower().strip()
    try:
        from app.mongodb import get_collection
        from pymongo import ReadPreference
        coll = get_collection("co_launch_projects")
        if coll is not None:
            doc = coll.with_options(read_preference=ReadPreference.PRIMARY_PREFERRED).find_one({"$or": [
                {"_id": project_id},
                {"id": project_id},
                {"creator_id": project_id},
                {"creatorId": project_id},
                {"creator_handle": clean_target},
                {"creatorHandle": clean_target},
                {"creator_handle": f"@{clean_target}"},
                {"creatorHandle": f"@{clean_target}"},
                {"creator_handle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"creatorHandle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"creator_handle": project_id},
                {"creatorHandle": project_id},
            ]})
            if doc:
                p_id = doc.get("id") or doc.get("_id")
                if p_id:
                    _project_cache[str(p_id)] = doc
                c_handle = (doc.get("creatorHandle") or doc.get("creator_handle") or "").replace("@", "").lower().strip()
                if c_handle:
                    _project_cache[c_handle] = doc
                return doc
    except Exception as e:
        logger.error(f"[MongoDB] _find_mongo_project error: {e}")

    # Fallback to local cache if MongoDB connection had a hiccup
    if project_id in _project_cache:
        return _project_cache[project_id]
    if clean_target in _project_cache:
        return _project_cache[clean_target]
    for p in _project_cache.values():
        if (
            str(p.get("id")) == str(project_id)
            or str(p.get("_id")) == str(project_id)
            or str(p.get("creatorId")) == str(project_id)
            or str(p.get("creator_id")) == str(project_id)
            or str(p.get("creatorHandle", "")).replace("@", "").lower().strip() == clean_target
            or str(p.get("creator_handle", "")).replace("@", "").lower().strip() == clean_target
        ):
            return p
    return None


def _sanitize_heavy_media(obj: Any) -> Any:
    """Recursively strip huge base64 data strings (e.g. postImageDataUrl) when URLs are present or when length > 50KB."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k == "postImageDataUrl" and isinstance(v, str):
                if obj.get("postImageUrl") or obj.get("cloudinaryUrl") or obj.get("cloudinary_url") or len(v) > 50000:
                    continue
            elif isinstance(v, str) and v.startswith("data:image/") and len(v) > 50000:
                continue
            else:
                cleaned[k] = _sanitize_heavy_media(v)
        return cleaned
    elif isinstance(obj, list):
        return [_sanitize_heavy_media(item) for item in obj]
    return obj


def _save_mongo_project(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to upsert project into MongoDB Atlas co_launch_projects."""
    if not doc:
        return doc
    doc = _sanitize_heavy_media(doc)
    p_id = doc.get("id") or doc.get("_id")
    if not p_id:
        p_id = f"proj_{int(datetime.utcnow().timestamp()*1000)}"
        doc["id"] = p_id
    doc["_id"] = p_id
    now_str = datetime.utcnow().isoformat()
    doc["updated_at"] = now_str
    doc["updatedAt"] = now_str

    _project_cache[str(p_id)] = doc
    c_handle = (doc.get("creatorHandle") or doc.get("creator_handle") or "").replace("@", "").lower().strip()
    if c_handle:
        _project_cache[c_handle] = doc

    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None:
            coll.replace_one({"_id": p_id}, doc, upsert=True)
        return doc
    except Exception as e:
        logger.error(f"[MongoDB] _save_mongo_project error: {e}")
        return doc


def _format_any_project(mongo_doc: Optional[Dict[str, Any]] = None, db_proj: Optional[CoLaunchProject] = None) -> Optional[Dict[str, Any]]:
    """Format and normalize project response supporting MongoDB Atlas documents and database models."""
    if mongo_doc:
        try:
            from app.mongodb import get_collection
            ws_coll = get_collection("workflow_states")
            ws_doc = ws_coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) if ws_coll is not None else None
            def_fee = None
            if ws_doc:
                def_fee = ws_doc.get("default_pass_price")
                if def_fee is None:
                    def_fee = (ws_doc.get("extra_state") or {}).get("default_pass_price")
            if def_fee is None:
                def_fee = 50.0
            return _normalize_mongo_project_dict(mongo_doc, float(def_fee))
        except Exception:
            return _normalize_mongo_project_dict(mongo_doc, 50.0)
    if db_proj:
        return _format_project_response(db_proj)
    return None


@router.get("")
def list_projects(db: Session = Depends(get_db)):
    """List all co-launch projects directly and exclusively from MongoDB Atlas."""
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is None:
            if _project_cache:
                unique_cached = {p.get("id") or p.get("_id"): p for p in _project_cache.values() if isinstance(p, dict) and (p.get("id") or p.get("_id"))}
                return [_normalize_mongo_project_dict(d, 50.0) for d in unique_cached.values()]
            return []
        docs = list(coll.find({}))
        for d in docs:
            pid = str(d.get("id") or d.get("_id") or "")
            if pid:
                _project_cache[pid] = d
            handle = (d.get("creatorHandle") or d.get("creator_handle") or "").replace("@", "").lower().strip()
            if handle:
                _project_cache[handle] = d

        ws_coll = get_collection("workflow_states")
        ws_doc = None
        try:
            ws_doc = ws_coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) if ws_coll is not None else None
        except Exception:
            pass
        def_fee = None
        if ws_doc:
            def_fee = ws_doc.get("default_pass_price")
            if def_fee is None:
                def_fee = (ws_doc.get("extra_state") or {}).get("default_pass_price")
        if def_fee is None:
            def_fee = 50.0
        return [_normalize_mongo_project_dict(d, float(def_fee)) for d in docs]
    except Exception as e:
        logger.error(f"[MongoDB] list_projects error: {e}")
        if _project_cache:
            unique_cached = {p.get("id") or p.get("_id"): p for p in _project_cache.values() if isinstance(p, dict) and (p.get("id") or p.get("_id"))}
            return [_normalize_mongo_project_dict(d, 50.0) for d in unique_cached.values()]
        return []


def execute_create_co_launch_project(db: Session, body: CreateProjectRequest) -> dict:
    """
    Initialize a new Co-Launch Project from concept directly and exclusively in MongoDB Atlas.
    Initializes the 5-step validation architecture and sends notifications safely.
    """
    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    creators_coll = get_collection("creators")
    ws_coll = get_collection("workflow_states")

    clean_handle = (body.creatorHandle or "").lstrip("@").strip().lower()

    # 1. Check if project already exists for this creator in MongoDB Atlas
    existing_doc = None
    if coll is not None:
        query_parts = []
        if body.id:
            query_parts.extend([{"_id": body.id}, {"id": body.id}])
        if body.creatorId:
            query_parts.extend([{"creator_id": body.creatorId}, {"creatorId": body.creatorId}])
        if body.creatorEmail:
            query_parts.extend([{"creator_email": body.creatorEmail}, {"creatorEmail": body.creatorEmail}])
        if clean_handle:
            query_parts.extend([
                {"creator_handle": clean_handle}, {"creatorHandle": clean_handle},
                {"creator_handle": f"@{clean_handle}"}, {"creatorHandle": f"@{clean_handle}"},
                {"creator_handle": {"$regex": f"^{clean_handle}$", "$options": "i"}},
                {"creatorHandle": {"$regex": f"^{clean_handle}$", "$options": "i"}}
            ])
        if query_parts:
            existing_doc = coll.find_one({"$or": query_parts})

    # Fetch default pass fee from workflow states (source of truth)
    def_fee = 50.0
    if ws_coll is not None:
        ws_doc = ws_coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]})
        if ws_doc:
            fee_cand = ws_doc.get("default_pass_price") or ws_doc.get("cobuilder_pass_price")
            if fee_cand is None and ws_doc.get("extra_state"):
                fee_cand = ws_doc["extra_state"].get("default_pass_price") or ws_doc["extra_state"].get("cobuilder_pass_price")
            if fee_cand is not None:
                try: def_fee = float(fee_cand)
                except Exception: pass

    now_iso = datetime.utcnow().isoformat()
    concept_data = body.selectedConcept or {}
    product_name = body.productName or concept_data.get("name") or concept_data.get("title") or "New Co-Launch Venture"
    product_tagline = body.productTagline or concept_data.get("tagline") or concept_data.get("productTagline") or concept_data.get("description") or body.product_tagline or f"Exclusive software platform and operating system."
    pricing_str = body.pricing or concept_data.get("pricing") or "$29/mo Starter • $79/mo Pro"
    if not pricing_str or not pricing_str.strip():
        pricing_str = "$29/mo Starter • $79/mo Pro"
    elif pricing_str.strip().startswith("/mo"):
        pricing_str = f"$29{pricing_str.strip()}"
    elif "$" not in pricing_str:
        pricing_str = f"${pricing_str.strip()}"
    presale_target_val = float(body.presaleTarget or concept_data.get("presaleTarget") or 12500.0)

    # Honor custom pass fee if passed explicitly in body
    cand_fee = body.diyFee if body.diyFee is not None else body.diyPassPrice if body.diyPassPrice is not None else body.diy_fee
    if cand_fee is not None:
        try:
            def_fee = float(cand_fee)
            has_custom_fee_init = True
        except Exception:
            has_custom_fee_init = False
    else:
        has_custom_fee_init = bool(body.hasCustomFee or body.has_custom_fee)

    if existing_doc:
        if product_name:
            existing_doc["productName"] = product_name
            existing_doc["product_name"] = product_name
            existing_doc["title"] = product_name
        if product_tagline:
            existing_doc["productTagline"] = product_tagline
            existing_doc["product_tagline"] = product_tagline
        if pricing_str:
            existing_doc["pricing"] = pricing_str
            parsed_pricing = parse_concept_pricing_details(pricing_str)
            if "validationPlan" in existing_doc and isinstance(existing_doc["validationPlan"], dict):
                existing_doc["validationPlan"]["pricing"] = pricing_str
            if "validationCampaign" in existing_doc and isinstance(existing_doc["validationCampaign"], dict):
                va = existing_doc["validationCampaign"].get("productAssets") or existing_doc["validationCampaign"].get("product_assets") or {}
                va["pricingConfig"] = parsed_pricing["pricing_config"]
                va["pricingTiers"] = parsed_pricing["pricing_tiers"]
                existing_doc["validationCampaign"]["productAssets"] = va
                existing_doc["validationCampaign"]["product_assets"] = va
            if "campaignKit" in existing_doc and isinstance(existing_doc["campaignKit"], dict):
                existing_doc["campaignKit"]["pricingConfig"] = parsed_pricing["pricing_config"]
        if concept_data:
            existing_doc["selectedConcept"] = concept_data
            existing_doc["selected_concept"] = concept_data
        # Synchronize problem & target audience from chosen concept
        c_problem = concept_data.get("problem") or body.problem
        if c_problem:
            existing_doc["problem"] = c_problem
        c_customer = concept_data.get("customer") or concept_data.get("demographicAlignment") or body.customer or body.targetAudience
        if c_customer:
            existing_doc["targetAudience"] = c_customer
            existing_doc["target_audience"] = c_customer
            existing_doc["customer"] = c_customer

        # Synchronize validationPlan with chosen concept
        if "validationPlan" in existing_doc and isinstance(existing_doc["validationPlan"], dict):
            if c_problem:
                existing_doc["validationPlan"]["problem"] = c_problem
            if c_customer:
                existing_doc["validationPlan"]["customer"] = c_customer
            if product_name and product_tagline:
                existing_doc["validationPlan"]["offer"] = f"{product_name} Founding Co-Launch Access: {product_tagline}"

        if cand_fee is not None:
            existing_doc["diyFee"] = def_fee
            existing_doc["diyPassPrice"] = def_fee
            existing_doc["hasCustomFee"] = has_custom_fee_init
            if isinstance(existing_doc.get("metadataInfo"), dict):
                existing_doc["metadataInfo"]["diy_fee"] = def_fee
                existing_doc["metadataInfo"]["diyFee"] = def_fee
                existing_doc["metadataInfo"]["diyPassPrice"] = def_fee
                existing_doc["metadataInfo"]["hasCustomFee"] = has_custom_fee_init
        _save_mongo_project(existing_doc)

        c_id = existing_doc.get("creatorId") or existing_doc.get("creator_id")
        if creators_coll is not None and c_id:
            creators_coll.update_one(
                {"$or": [{"_id": c_id}, {"id": c_id}]},
                {"$set": {"status": "partnered", "has_project": True, "hasProject": True, "project_id": existing_doc["id"], "projectId": existing_doc["id"]}}
            )
        if ws_coll is not None:
            ws_coll.update_one(
                {"$or": [{"_id": "default"}, {"id": "default"}]},
                {"$set": {"active_project_id": existing_doc["id"], "selected_creator_id": c_id}}
            )
        return _normalize_mongo_project_dict(existing_doc, def_fee)

    # 2. Lookup creator in MongoDB
    creator_doc = None
    if creators_coll is not None:
        c_query = []
        if body.creatorId:
            c_query.extend([{"_id": body.creatorId}, {"id": body.creatorId}])
        if clean_handle:
            c_query.extend([
                {"handle": clean_handle}, {"handle": f"@{clean_handle}"},
                {"handle": {"$regex": f"^{clean_handle}$", "$options": "i"}}
            ])
        if body.creatorEmail:
            c_query.extend([{"email_public": body.creatorEmail}, {"email": body.creatorEmail}])
        if c_query:
            creator_doc = creators_coll.find_one({"$or": c_query})

    c_id = (creator_doc.get("id") or creator_doc.get("_id")) if creator_doc else (body.creatorId or f"c_{uuid.uuid4().hex[:8]}")
    c_handle = creator_doc.get("handle") if creator_doc else (body.creatorHandle or "creator")
    c_name = (creator_doc.get("display_name") or creator_doc.get("name")) if creator_doc else (body.creatorName or c_handle)
    c_avatar = creator_doc.get("avatar_url") or creator_doc.get("avatar") if creator_doc else body.creatorAvatar
    c_email = creator_doc.get("email_public") or creator_doc.get("email") if creator_doc else body.creatorEmail
    niche_val = creator_doc.get("niche") if creator_doc else body.niche
    niche_str = ", ".join(str(x) for x in niche_val) if isinstance(niche_val, list) else (str(niche_val) if niche_val else "General")
    followers_str = str(creator_doc.get("follower_count") or creator_doc.get("followers") or body.followers or "100,000")

    proj_id = body.id or f"proj_{int(datetime.utcnow().timestamp()*1000)}"
    slug = product_name.lower().replace(" ", "-").replace("'", "")
    customer_desc = body.customer or body.targetAudience or concept_data.get("customer") or concept_data.get("demographicAlignment") or f"{niche_str} audience"
    problem_desc = body.problem or concept_data.get("problem") or concept_data.get("description") or f"Manual workflows and lack of specialized tools in {niche_str}"

    parsed_pricing = parse_concept_pricing_details(pricing_str)
    founding_price = parsed_pricing["founding_price"]
    deposit_price = parsed_pricing["deposit_price"]
    starter_price = parsed_pricing["starter_price"]
    pro_price = parsed_pricing["pro_price"]
    pricing_tiers = parsed_pricing["pricing_tiers"]

    sample_tasks = [
        {"id": f"t_{proj_id}_1", "dayNumber": 1, "day": 1, "channel": "instagram", "task_title": "Post Instagram Story #1: Problem Teaser", "title": "Post Instagram Story #1: Problem Teaser", "content": f"Hey everyone! Notice how annoying {problem_desc.lower()} is? Co-founding a solution.", "cta": "Vote on poll + DM me", "trackingLink": f"/p/{slug}?utm=ig1", "status": "today"},
        {"id": f"t_{proj_id}_2", "dayNumber": 2, "day": 2, "channel": "instagram", "task_title": "Post Instagram Story #2: Behind The Scenes", "title": "Post Instagram Story #2: Behind The Scenes", "content": f"Revealing {product_name} first build preview today!", "cta": "Tap link to view preview", "trackingLink": f"/p/{slug}?utm=ig2", "status": "pending"},
        {"id": f"t_{proj_id}_3", "dayNumber": 3, "day": 3, "channel": "youtube", "task_title": "YouTube Video Integration (60s Mid-Roll)", "title": "YouTube Video Integration (60s Mid-Roll)", "content": f"Opening 50 founding spots for {product_name} at 50% off (${founding_price}).", "cta": "Check pinned comment", "trackingLink": f"/p/{slug}?utm=yt", "status": "pending"},
        {"id": f"t_{proj_id}_4", "dayNumber": 5, "day": 5, "channel": "newsletter", "task_title": "Newsletter Broadcast: Founding Cohort Announcement", "title": "Newsletter Broadcast: Founding Cohort Announcement", "content": f"Subject: Building something new with you.\n\nToday we open founding presales for {product_name} ($50% off - ${founding_price}).", "cta": f"Reserve Founding Access (${founding_price})", "trackingLink": f"/p/{slug}?utm=newsletter", "status": "pending"},
        {"id": f"t_{proj_id}_5", "dayNumber": 7, "day": 7, "channel": "twitter", "task_title": "X/Twitter Breakdown Thread", "title": "X/Twitter Breakdown Thread", "content": f"1/5 Why existing tools fail for {customer_desc}.\n2/5 How we built {product_name}.\n3/5 Pre-orders open now.", "cta": f"Claim founding pass (${founding_price})", "trackingLink": f"/p/{slug}?utm=twitter", "status": "pending"},
    ]

    new_project_doc = {
        "_id": proj_id,
        "id": proj_id,
        "creatorId": c_id,
        "creator_id": c_id,
        "creatorHandle": c_handle,
        "creator_handle": c_handle,
        "creatorName": c_name,
        "creator_name": c_name,
        "creatorAvatar": c_avatar,
        "creator_avatar": c_avatar,
        "creatorEmail": c_email,
        "creator_email": c_email,
        "niche": niche_str,
        "followers": followers_str,
        "productName": product_name,
        "product_name": product_name,
        "title": product_name,
        "productTagline": product_tagline,
        "product_tagline": product_tagline,
        "targetAudience": customer_desc,
        "target_audience": customer_desc,
        "customer": customer_desc,
        "problem": problem_desc,
        "keyFeatures": concept_data.get("keyFeatures") or concept_data.get("features") or body.keyFeatures or [],
        "pricing": pricing_str,
        "revenueModel": body.revenueModel or concept_data.get("revenueModel") or "SaaS Subscription",
        "revenue_model": body.revenueModel or concept_data.get("revenueModel") or "SaaS Subscription",
        "currentPhase": 1,
        "current_phase": 1,
        "currentStep": "plan",
        "current_step": "plan",
        "status": "validating",
        "presaleTarget": presale_target_val,
        "presale_target": presale_target_val,
        "currentPresales": 0.0,
        "current_presales": 0.0,
        "visitors": 0,
        "conversionRate": 0.0,
        "conversion_rate": 0.0,
        "portalToken": "cf_sec_live",
        "portal_token": "cf_sec_live",
        "portalLinkSent": True,
        "portal_link_sent": True,
        "portalLinkSentTo": c_email,
        "portal_link_sent_to": c_email,
        "portalLinkSentAt": now_iso,
        "portal_link_sent_at": now_iso,
        "isDIY": False,
        "is_diy": False,
        "diyOfferStatus": "offer_sent",
        "diy_offer_status": "offer_sent",
        "diyFee": def_fee,
        "diy_fee": def_fee,
        "diyPassPrice": def_fee,
        "hasCustomFee": has_custom_fee_init,
        "selectedConcept": concept_data,
        "selected_concept": concept_data,
        "brandColor": body.brandColor or getattr(body, "brand_color", None) or concept_data.get("brandColor") or "#c81e3c",
        "brand_color": body.brandColor or getattr(body, "brand_color", None) or concept_data.get("brandColor") or "#c81e3c",
        "colorTheme": body.colorTheme or getattr(body, "color_theme", None) or concept_data.get("colorTheme") or "crimson",
        "color_theme": body.colorTheme or getattr(body, "color_theme", None) or concept_data.get("colorTheme") or "crimson",
        "validationPlan": {
            "id": f"plan_{proj_id}",
            "customer": customer_desc,
            "problem": problem_desc,
            "offer": f"{product_name} Founding Co-Launch Access: {product_tagline}",
            "pricing": pricing_str,
            "testMethod": "1) Co-founder video announcement, 2) 10 user interviews, 3) 48-hour Founding Pre-Order sprint",
            "period": "14 days",
            "threshold": f"${int(presale_target_val):,} in presales within 14 days",
            "targetRevenue": presale_target_val,
            "status": "ready"
        },
        "validationCampaign": {
            "id": f"camp_{proj_id}",
            "productAssets": {
                "productName": product_name,
                "productTagline": product_tagline,
                "positioning": f"The #1 automated platform built exclusively for {customer_desc}",
                "headline": f"Finally, an operating system tailored for {customer_desc}",
                "problem": problem_desc,
                "keyFeatures": concept_data.get("keyFeatures") or concept_data.get("features") or body.keyFeatures or [],
                "mockup": body.mockup or concept_data.get("mockup") or {},
                "mockupType": concept_data.get("mockupType") or "saas_os",
                "selectedConcept": concept_data,
                "pricingConfig": {"foundingPrice": founding_price, "depositPrice": deposit_price},
                "pricingTiers": pricing_tiers
            },
            "infrastructure": {
                "landingPageUrl": f"/p/{slug}",
                "checkoutUrl": f"/p/{slug}/checkout",
                "waitlistCount": 240,
                "attributionTracking": True,
                "utmSource": "creator_launch"
            },
            "researchSurvey": {"summary": f"Initial audience survey for {product_name}.", "questions": [], "responses": []},
            "reviewStatus": "draft"
        },
        "campaignKit": {
            "pricingConfig": {"foundingPrice": founding_price, "depositPrice": deposit_price},
            "announcementPost": f"Today I'm officially announcing: I'm co-founding {product_name}. Founding cohort is open now at 50% off (${founding_price}/yr).",
            "storySequence": [f"Story 1: Why existing tools fail for {customer_desc}", f"Story 2: Introducing {product_name}", f"Story 3: Link to founding cohort (${founding_price})"],
            "videoScript": f"Hey everyone, quick heads up: we're launching {product_name}. Check the pinned comment to join for ${founding_price}.",
            "newsletterDraft": f"Subject: Announcing {product_name}.\n\nWe're opening founding presales today (${founding_price}/yr or ${deposit_price} refundable deposit).",
            "landingPageCopy": {
                "headline": f"The {product_name} Operating System",
                "subheadline": product_tagline,
                "ctaText": f"Claim Founding Access (${founding_price})",
                "reservationText": f"Reserve with ${deposit_price} Deposit",
                "bulletPoints": [
                    "Automate repetitive workflows with tailored software",
                    "Direct private Slack & alpha advisory council access",
                    f"50% lifetime discount locked in forever (${founding_price}/yr)"
                ]
            },
            "postingSchedule": sample_tasks
        },
        "creatorTasks": sample_tasks,
        "telemetry": {
            "id": f"telem_{proj_id}",
            "visitors": 0, "views": 0, "ctr": 0.0, "signups": 0, "presalesCount": 0, "presalesRevenue": 0.0, "conversionRate": 0.0,
            "reservations": [], "channelAttribution": {"instagram": 0, "youtube": 0, "twitter": 0, "direct": 0}, "experiments": [], "feedbackClusters": []
        },
        "gateDecisions": [],
        "metadataInfo": {
            "recent_posts": body.recentPosts or body.recent_posts or body.videos or [],
            "channel_url": body.channelUrl or (f"https://www.youtube.com/@{c_handle.lstrip('@')}" if c_handle else ""),
            "channel_description": body.channelDescription or body.creatorBio or "",
            "diy_fee": def_fee, "diyFee": def_fee, "diyPassPrice": def_fee, "hasCustomFee": has_custom_fee_init,
            "activity_logs": [{"timestamp": now_iso, "action": "project_created", "actor": "Co-Founder Operator", "details": f"Created {product_name}"}]
        },
        "createdAt": now_iso, "created_at": now_iso, "updatedAt": now_iso, "updated_at": now_iso
    }

    _save_mongo_project(new_project_doc)

    if creators_coll is not None and c_id:
        creators_coll.update_one(
            {"$or": [{"_id": c_id}, {"id": c_id}]},
            {"$set": {"status": "partnered", "has_project": True, "hasProject": True, "project_id": proj_id, "projectId": proj_id}}
        )
    if ws_coll is not None:
        ws_coll.update_one(
            {"$or": [{"_id": "default"}, {"id": "default"}]},
            {"$set": {"active_project_id": proj_id, "selected_creator_id": c_id, "active_section": "section2", "active_step": 1}}
        )

    try:
        from app.integrations.email_provider import email_provider
        from app.config import settings
        creator_email = (c_email or "").strip()
        admin_email = (settings.ADMIN_EMAIL or "elishadamu97@gmail.com").strip()
        base_frontend = (settings.FRONTEND_URL or "https://creator-forge-frontend.vercel.app").rstrip("/")
        portal_slug = (c_handle or "creator").replace("@", "").replace(" ", "").strip().lower()
        admin_project_link = f"{base_frontend}/launch?section=section2&project={proj_id}"
        portal_magic_link = f"{base_frontend}/portal/{portal_slug}?token=cf_sec_live&project={proj_id}"
        if admin_email and "@" in admin_email:
            email_provider.send(
                to_email=admin_email,
                subject=f"[PROJECT INITIALIZED] {product_name} with {c_name} (Section 2 Live)",
                body_text=f"New project initialized: {product_name} with {c_name}.\nAdmin: {admin_project_link}\nCreator Portal: {portal_magic_link}"
            )
    except Exception as dispatch_err:
        logger.debug(f"[Project Dispatch] Notification notice: {dispatch_err}")

    return _normalize_mongo_project_dict(new_project_doc, def_fee)


@router.post("", status_code=201)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db)):
    """Initialize a new Co-Launch Project from Section 1 concept."""
    return execute_create_co_launch_project(db, body)


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    """Fetch complete co-launch project with all 5 validation steps directly and exclusively from MongoDB."""
    clean_target = project_id.replace("@", "").lower().strip()
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None:
            doc = coll.find_one({"$or": [
                {"_id": project_id},
                {"id": project_id},
                {"creator_id": project_id},
                {"creatorId": project_id},
                {"creator_handle": clean_target},
                {"creatorHandle": clean_target},
                {"creator_handle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"creatorHandle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"creator_handle": project_id},
                {"creatorHandle": project_id},
                {"productSlug": clean_target},
                {"productSlug": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"slug": clean_target},
                {"slug": {"$regex": f"^{clean_target}$", "$options": "i"}},
            ]})
            if not doc:
                # Fallback to latest active project in collection
                doc = coll.find_one(sort=[("updatedAt", -1), ("createdAt", -1), ("_id", -1)])
            if doc:
                ws_coll = get_collection("workflow_states")
                ws_doc = ws_coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) if ws_coll is not None else None
                def_fee = None
                if ws_doc:
                    def_fee = ws_doc.get("default_pass_price")
                    if def_fee is None:
                        def_fee = (ws_doc.get("extra_state") or {}).get("default_pass_price")
                if def_fee is None:
                    def_fee = 50.0
                return _normalize_mongo_project_dict(doc, float(def_fee))
    except Exception as e:
        logger.error(f"[MongoDB] get_project error: {e}")

    raise HTTPException(404, f"Project '{project_id}' not found")


@router.patch("/{project_id}")
@router.put("/{project_id}")
def update_project_general(project_id: str, body: Dict[str, Any], db: Session = Depends(get_db)):
    """Update co-launch project phase, step, status, or metadata attributes directly in MongoDB Atlas."""
    clean_target = project_id.replace("@", "").lower().strip()
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None:
            doc = coll.find_one({"$or": [
                {"_id": project_id},
                {"id": project_id},
                {"creator_id": project_id},
                {"creatorId": project_id},
                {"creator_handle": clean_target},
                {"creatorHandle": clean_target},
                {"creator_handle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"creatorHandle": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"productSlug": clean_target},
                {"productSlug": {"$regex": f"^{clean_target}$", "$options": "i"}},
                {"slug": clean_target},
                {"slug": {"$regex": f"^{clean_target}$", "$options": "i"}},
            ]})
            if not doc:
                # Fallback to latest active project or create one
                doc = coll.find_one(sort=[("updatedAt", -1), ("createdAt", -1), ("_id", -1)])
            if not doc:
                doc = {"_id": project_id, "id": project_id, "createdAt": datetime.utcnow().isoformat(), **body}
                coll.insert_one(doc)
                doc.pop("_id", None)
                return doc

            doc_meta = doc.get("metadataInfo") or doc.get("metadata_info") or {}
            if isinstance(doc_meta, str):
                try: doc_meta = json.loads(doc_meta)
                except Exception: doc_meta = {}
            meta_in = body.get("metadataInfo") or body.get("metadata_info")
            if isinstance(meta_in, dict):
                doc_meta.update(meta_in)
            for k, v in body.items():
                doc[k] = v
            if "diyFee" in body or "diy_fee" in body or "diyPassPrice" in body or body.get("resetToDefault") or "hasCustomFee" in body or "has_custom_fee" in body:
                is_reset = bool(body.get("resetToDefault"))
                f_raw = body.get("diyFee") if "diyFee" in body else body.get("diy_fee") if "diy_fee" in body else body.get("diyPassPrice")
                if is_reset or (f_raw is None and body.get("resetToDefault")):
                    doc_meta.pop("diy_fee", None)
                    doc_meta.pop("diyFee", None)
                    doc_meta.pop("diyPassPrice", None)
                    doc_meta["hasCustomFee"] = False
                    doc.pop("diyFee", None)
                    doc.pop("diyPassPrice", None)
                    doc["hasCustomFee"] = False
                elif f_raw is not None and f_raw != "":
                    try:
                        f_val = float(f_raw)
                        doc["diyFee"] = f_val
                        doc["diyPassPrice"] = f_val
                        doc_meta["diy_fee"] = f_val
                        doc_meta["diyFee"] = f_val
                        doc_meta["diyPassPrice"] = f_val
                        is_custom = bool(body.get("hasCustomFee") is not False and body.get("has_custom_fee") is not False)
                        doc_meta["hasCustomFee"] = is_custom
                        doc["hasCustomFee"] = is_custom
                    except (ValueError, TypeError):
                        pass
            doc["metadataInfo"] = doc_meta
            doc["metadata_info"] = doc_meta
            doc["updatedAt"] = datetime.utcnow().isoformat()
            doc["updated_at"] = datetime.utcnow().isoformat()
            doc_id = doc.get("_id", project_id)
            coll.replace_one({"_id": doc_id}, doc, upsert=True)
            doc.pop("_id", None)
            return doc
    except Exception as e:
        logger.error(f"[MongoDB] update_project_general error: {e}")
        return {"status": "saved", "id": project_id, **body}

    meta = body.get("metadataInfo") or body.get("metadata_info")
    raw_meta = proj.metadata_info
    if isinstance(raw_meta, str):
        try:
            cur_meta = json.loads(raw_meta)
        except Exception:
            cur_meta = {}
    elif isinstance(raw_meta, dict):
        cur_meta = dict(raw_meta)
    else:
        cur_meta = {}

    if meta is not None and isinstance(meta, dict):
        cur_meta.update(meta)

    phase = body.get("currentPhase") if body.get("currentPhase") is not None else body.get("current_phase")
    if phase is not None:
        proj.current_phase = int(phase)
        if int(phase) == 2 and proj.status == "validating":
            proj.status = "building"
        elif int(phase) == 3:
            proj.status = "launched"

    step = body.get("currentStep") or body.get("current_step")
    if step is not None:
        proj.current_step = str(step)
        if str(step) in ["beta", "gate"]:
            cur_meta["buildCompleted"] = True
            cur_meta["mvpBuildDone"] = True
            cur_meta["step2Done"] = True
        if str(step) == "gate":
            cur_meta["betaTestingCompleted"] = True
            cur_meta["betaApproved"] = True
            cur_meta["step3Done"] = True

    status = body.get("status")
    if status is not None:
        if str(status) in ["scouting", "pitching", "qualified", "validating", "building", "launched", "killed"]:
            proj.status = str(status)
        elif str(status) == "approved":
            cur_meta["buildPlanApproved"] = True
            cur_meta["build_plan_approved"] = True

    if "productName" in body or "product_name" in body:
        proj.product_name = str(body.get("productName") or body.get("product_name"))

    if "productTagline" in body or "product_tagline" in body:
        proj.product_tagline = str(body.get("productTagline") or body.get("product_tagline"))

    if "pricing" in body:
        proj.pricing = str(body.get("pricing"))

    target = body.get("presaleTarget") if body.get("presaleTarget") is not None else body.get("presale_target")
    if target is not None:
        proj.presale_target = float(target)

    if "mockupImage" in body or "mockup_image" in body:
        mockup_val = body.get("mockupImage") or body.get("mockup_image")
        if mockup_val and isinstance(mockup_val, str):
            if "data:image" in mockup_val or (mockup_val.startswith("http") and "cloudinary.com" not in mockup_val):
                try:
                    import re, time
                    clean_handle = (proj.creator_handle or "").replace("@", "").strip()
                    creator_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', clean_handle or proj.creator_name or proj.creator_id or "creator").strip('_').lower() or "creator"
                    cld_folder = f"creator_forge/creators/{creator_slug}/mockups"
                    cld_id = f"{creator_slug}_mockup_{int(time.time())}"
                    from app.integrations.cloudinary_service import upload_media_to_cloudinary
                    cld_res = upload_media_to_cloudinary(
                        file_data=mockup_val,
                        public_id=cld_id,
                        folder=cld_folder,
                        resource_type="image",
                        tags=[f"creator:{creator_slug}", "product_mockup"]
                    )
                    if cld_res.get("success") and cld_res.get("secure_url"):
                        mockup_val = cld_res.get("secure_url")
                except Exception as m_err:
                    logger.warning(f"Mockup Cloudinary upload fallback: {m_err}")
            cur_meta["mockup_image"] = mockup_val

    if "projectFiles" in body or "project_files" in body:
        cur_meta["project_files"] = body.get("projectFiles") or body.get("project_files")

    if "messages" in body:
        cur_meta["messages"] = body["messages"]

    if "mvpBuildPlan" in body or "mvp_build_plan" in body:
        plan_val = body.get("mvpBuildPlan") or body.get("mvp_build_plan")
        cur_meta["mvp_build_plan"] = plan_val
        cur_meta["mvpBuildPlan"] = plan_val

    if "engineeringTasks" in body or "engineering_tasks" in body:
        tasks_val = body.get("engineeringTasks") or body.get("engineering_tasks")
        cur_meta["engineering_tasks"] = tasks_val
        cur_meta["engineeringTasks"] = tasks_val

    if "qaResults" in body or "qa_results" in body:
        cur_meta["qa_results"] = body.get("qaResults") or body.get("qa_results")

    if "betaFeedback" in body or "beta_feedback" in body:
        cur_meta["beta_feedback"] = body.get("betaFeedback") or body.get("beta_feedback")

    if "feedbackClusters" in body or "feedback_clusters" in body:
        clusters = body.get("feedbackClusters") or body.get("feedback_clusters")
        cur_meta["feedback_clusters"] = clusters
        if proj.telemetry:
            proj.telemetry.feedback_clusters = clusters

    if "readinessReport" in body or "readiness_report" in body:
        cur_meta["readiness_report"] = body.get("readinessReport") or body.get("readiness_report")

    if "appliedPatches" in body or "applied_patches" in body:
        cur_meta["applied_patches"] = body.get("appliedPatches") or body.get("applied_patches")

    if "mvpVersion" in body or "mvp_version" in body:
        cur_meta["mvp_version"] = body.get("mvpVersion") or body.get("mvp_version")

    if "launchStrategy" in body or "launch_strategy" in body:
        cur_meta["launch_strategy"] = body.get("launchStrategy") or body.get("launch_strategy")

    if "creatorAssets" in body or "creator_assets" in body:
        cur_meta["creator_assets"] = body.get("creatorAssets") or body.get("creator_assets")

    if "launchTelemetry" in body or "launch_telemetry" in body:
        cur_meta["launch_telemetry"] = body.get("launchTelemetry") or body.get("launch_telemetry")

    if "channelStats" in body or "channel_stats" in body:
        cur_meta["channel_stats"] = body.get("channelStats") or body.get("channel_stats")

    if "launchManagerData" in body or "launch_manager_data" in body:
        cur_meta["launch_manager_data"] = body.get("launchManagerData") or body.get("launch_manager_data")

    if "dispatchedActions" in body or "dispatched_actions" in body:
        cur_meta["dispatched_actions"] = body.get("dispatchedActions") or body.get("dispatched_actions")

    if "launchReport" in body or "launch_report" in body:
        cur_meta["launch_report"] = body.get("launchReport") or body.get("launch_report")

    if "launchStatus" in body or "launch_status" in body:
        cur_meta["launch_status"] = body.get("launchStatus") or body.get("launch_status")

    if "productInfrastructure" in body or "product_infrastructure" in body:
        cur_meta["product_infrastructure"] = body.get("productInfrastructure") or body.get("product_infrastructure")

    if "campaignKit" in body or "campaign_kit" in body:
        ck = body.get("campaignKit") or body.get("campaign_kit")
        if ck and isinstance(ck, dict):
            cur_meta["campaign_kit"] = ck
            cur_meta["campaign_launched"] = True
            if proj.validation_campaign:
                proj.validation_campaign.campaign_kit = ck
                flag_modified(proj.validation_campaign, "campaign_kit")

    if "campaignLaunched" in body or "campaign_launched" in body:
        cl = body.get("campaignLaunched") if body.get("campaignLaunched") is not None else body.get("campaign_launched")
        cur_meta["campaign_launched"] = bool(cl)

    if "experimentsData" in body or "experiments_data" in body:
        exp_data = body.get("experimentsData") or body.get("experiments_data")
        cur_meta["experimentsData"] = exp_data
        cur_meta["experiments_data"] = exp_data
        if proj.telemetry and isinstance(exp_data, dict) and "experiments" in exp_data:
            proj.telemetry.experiments = exp_data.get("experiments") or []
            flag_modified(proj.telemetry, "experiments")

    if "experiments" in body:
        exps = body.get("experiments")
        cur_meta["experiments"] = exps
        if proj.telemetry and isinstance(exps, list):
            proj.telemetry.experiments = exps
            flag_modified(proj.telemetry, "experiments")

    if "diySubscription" in body or "diy_subscription" in body:
        diy_data = body.get("diySubscription") or body.get("diy_subscription")
        cur_meta["diy_subscription"] = diy_data
        cur_meta["diySubscription"] = diy_data
        if isinstance(diy_data, dict) and diy_data.get("active"):
            cur_meta["is_diy"] = True
            cur_meta["isDIY"] = True

    if "isDIY" in body or "is_diy" in body:
        val = bool(body.get("isDIY") if "isDIY" in body else body.get("is_diy"))
        cur_meta["is_diy"] = val
        cur_meta["isDIY"] = val

    if "diyOfferStatus" in body or "diy_offer_status" in body:
        dos = body.get("diyOfferStatus") or body.get("diy_offer_status")
        cur_meta["diy_offer_status"] = dos
        cur_meta["diyOfferStatus"] = dos

    if "diyOfferSentAt" in body or "diy_offer_sent_at" in body:
        dosa = body.get("diyOfferSentAt") or body.get("diy_offer_sent_at")
        cur_meta["diy_offer_sent_at"] = dosa
        cur_meta["diyOfferSentAt"] = dosa

    if "diyFee" in body or "diy_fee" in body or "diyPassPrice" in body or body.get("resetToDefault") or "hasCustomFee" in body or "has_custom_fee" in body:
        is_reset = bool(body.get("resetToDefault") or body.get("hasCustomFee") is False or body.get("has_custom_fee") is False)
        fee_raw = body.get("diyFee") if "diyFee" in body else body.get("diy_fee") if "diy_fee" in body else body.get("diyPassPrice")
        if is_reset or fee_raw is None or fee_raw == "":
            cur_meta.pop("diy_fee", None)
            cur_meta.pop("diyFee", None)
            cur_meta.pop("diyPassPrice", None)
            cur_meta["hasCustomFee"] = False
        else:
            try:
                fee_val = float(fee_raw)
                cur_meta["diy_fee"] = fee_val
                cur_meta["diyFee"] = fee_val
                cur_meta["diyPassPrice"] = fee_val
                is_custom = bool(body.get("hasCustomFee") is True or body.get("has_custom_fee") is True or (body.get("hasCustomFee") is None and body.get("has_custom_fee") is None))
                cur_meta["hasCustomFee"] = is_custom
            except (ValueError, TypeError):
                pass

    if "buildPlanApproved" in body or "build_plan_approved" in body:
        bpa = bool(body.get("buildPlanApproved") or body.get("build_plan_approved"))
        cur_meta["buildPlanApproved"] = bpa
        cur_meta["build_plan_approved"] = bpa

    if "buildCompleted" in body or "build_completed" in body:
        bc = bool(body.get("buildCompleted") or body.get("build_completed"))
        cur_meta["buildCompleted"] = bc
        cur_meta["build_completed"] = bc

    if "mvpBuildDone" in body or "mvp_build_done" in body:
        mbd = bool(body.get("mvpBuildDone") or body.get("mvp_build_done"))
        cur_meta["mvpBuildDone"] = mbd
        cur_meta["mvp_build_done"] = mbd

    if "betaTestingCompleted" in body or "beta_testing_completed" in body:
        btc = bool(body.get("betaTestingCompleted") or body.get("beta_testing_completed"))
        cur_meta["betaTestingCompleted"] = btc
        cur_meta["beta_testing_completed"] = btc

    if "betaApproved" in body or "beta_approved" in body:
        ba = bool(body.get("betaApproved") or body.get("beta_approved"))
        cur_meta["betaApproved"] = ba
        cur_meta["beta_approved"] = ba

    if "step2Done" in body or "step2_done" in body:
        s2d = bool(body.get("step2Done") or body.get("step2_done"))
        cur_meta["step2Done"] = s2d
        cur_meta["buildCompleted"] = s2d
        cur_meta["mvpBuildDone"] = s2d

    if "step3Done" in body or "step3_done" in body:
        s3d = bool(body.get("step3Done") or body.get("step3_done"))
        cur_meta["step3Done"] = s3d
        cur_meta["betaTestingCompleted"] = s3d
        cur_meta["betaApproved"] = s3d

    if "p1Complete" in body or "p1_complete" in body:
        cur_meta["p1Complete"] = bool(body.get("p1Complete") or body.get("p1_complete"))

    if "phase1Passed" in body:
        cur_meta["phase1Passed"] = bool(body.get("phase1Passed"))

    if "p2Complete" in body or "p2_complete" in body:
        cur_meta["p2Complete"] = bool(body.get("p2Complete") or body.get("p2_complete"))

    if "phase2Passed" in body:
        cur_meta["phase2Passed"] = bool(body.get("phase2Passed"))

    proj.metadata_info = cur_meta
    flag_modified(proj, "metadata_info")

    proj.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(proj)
    return _format_project_response(proj)


@router.put("/{project_id}/plan")
def update_validation_plan(project_id: str, body: UpdatePlanRequest, db: Session = Depends(get_db)):
    """Save or update Step 1 Validation Plan directly in MongoDB Atlas."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")

    plan = proj.get("validationPlan") or proj.get("validation_plan") or {}
    if body.customer is not None: plan["customer"] = body.customer
    if body.problem is not None: plan["problem"] = body.problem
    if body.offer is not None: plan["offer"] = body.offer
    if body.pricing is not None:
        plan["pricing"] = body.pricing
        proj["pricing"] = body.pricing
    if body.test_method is not None: plan["testMethod"] = body.test_method
    if body.period is not None: plan["period"] = body.period
    if body.threshold is not None: plan["threshold"] = body.threshold
    if body.target_revenue is not None:
        plan["targetRevenue"] = body.target_revenue
        proj["presaleTarget"] = body.target_revenue
        proj["presale_target"] = body.target_revenue
    if body.status is not None: plan["status"] = body.status

    proj["validationPlan"] = plan
    proj["validation_plan"] = plan
    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


@router.put("/{project_id}/campaign")
def update_validation_campaign(project_id: str, body: UpdateCampaignRequest, db: Session = Depends(get_db)):
    """Save or update Step 2 Campaign Assets, Infrastructure, Step 3 Campaign Kit, and Creator Tasks in MongoDB Atlas."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")

    camp = proj.get("validationCampaign") or proj.get("validation_campaign") or {}
    prod_assets = body.product_assets if body.product_assets is not None else body.productAssets
    if prod_assets is not None:
        camp["productAssets"] = prod_assets
        camp["product_assets"] = prod_assets
    if body.infrastructure is not None:
        camp["infrastructure"] = body.infrastructure
    res_survey = body.research_survey if body.research_survey is not None else body.researchSurvey
    if res_survey is not None:
        camp["researchSurvey"] = res_survey
        camp["research_survey"] = res_survey
    rev_status = body.review_status if body.review_status is not None else body.reviewStatus
    if rev_status is not None:
        camp["reviewStatus"] = rev_status
        camp["review_status"] = rev_status
        if rev_status in ("approved", "launched"):
            camp["approvedAt"] = datetime.utcnow().isoformat()

    kit = body.campaign_kit if body.campaign_kit is not None else body.campaignKit
    if kit is None and prod_assets and isinstance(prod_assets, dict):
        if "campaign_kit" in prod_assets:
            kit = prod_assets["campaign_kit"]
        elif "campaignKit" in prod_assets:
            kit = prod_assets["campaignKit"]

    if kit is not None:
        camp["campaignKit"] = kit
        camp["campaign_kit"] = kit
        proj["campaignKit"] = kit
        proj["campaign_kit"] = kit

    camp_launched = body.campaign_launched if body.campaign_launched is not None else body.campaignLaunched
    if camp_launched is not None:
        proj["campaignLaunched"] = camp_launched
        if camp_launched:
            camp["reviewStatus"] = "approved"

    creator_tasks = body.creator_tasks if body.creator_tasks is not None else body.creatorTasks
    if creator_tasks is not None and isinstance(creator_tasks, list) and len(creator_tasks) > 0:
        proj["creatorTasks"] = creator_tasks
        proj["creator_tasks"] = creator_tasks

    proj["validationCampaign"] = camp
    proj["validation_campaign"] = camp

    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


class GenerateCampaignMediaRequest(BaseModel):
    prompt: Optional[str] = None
    apiKey: Optional[str] = None
    openaiApiKey: Optional[str] = None
    postImageUrl: Optional[str] = None
    caller: Optional[str] = "admin"  # 'creator' or 'admin'


@router.post("/{project_id}/campaign/generate-image")
def generate_project_campaign_image(
    project_id: str,
    body: Optional[GenerateCampaignMediaRequest] = None,
    x_gemini_key: Optional[str] = Header(None),
    x_openai_key: Optional[str] = Header(None),
    x_user_role: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Generate social media post image and store in Cloudinary under creator's profile folder."""
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None
    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    creator_name = (mongo_doc.get("creatorName") or mongo_doc.get("creator_name")) if mongo_doc else None
    if not creator_name and proj:
        creator_name = proj.creator_name
    creator_name = creator_name or "Creator"

    creator_handle = (mongo_doc.get("creatorHandle") or mongo_doc.get("creator_handle")) if mongo_doc else None
    if not creator_handle and proj:
        creator_handle = proj.creator_handle
    creator_handle = creator_handle or ""

    creator_id = (mongo_doc.get("creatorId") or mongo_doc.get("creator_id")) if mongo_doc else None
    if not creator_id and proj:
        creator_id = proj.creator_id
    creator_id = creator_id or ""

    niche = mongo_doc.get("niche") if mongo_doc else (proj.niche if proj else None)
    niche = niche or "Tech"

    product_name = (mongo_doc.get("productName") or mongo_doc.get("product_name")) if mongo_doc else None
    if not product_name and proj:
        product_name = proj.product_name
    product_name = product_name or "New Software"

    product_tagline = (mongo_doc.get("productTagline") or mongo_doc.get("product_tagline")) if mongo_doc else None
    if not product_tagline and proj:
        product_tagline = proj.product_tagline
    product_tagline = product_tagline or ""

    target_audience = (mongo_doc.get("targetAudience") or mongo_doc.get("target_audience")) if mongo_doc else None
    if not target_audience and proj:
        target_audience = proj.target_audience
    target_audience = target_audience or ""

    effective_proj_id = (mongo_doc.get("id") or mongo_doc.get("_id")) if mongo_doc else (proj.id if proj else project_id)

    kit = None
    if mongo_doc:
        kit = (
            mongo_doc.get("campaignKit")
            or mongo_doc.get("campaign_kit")
            or (mongo_doc.get("validationCampaign") or {}).get("campaignKit")
            or (mongo_doc.get("validationCampaign") or {}).get("campaign_kit")
            or (mongo_doc.get("metadataInfo") or {}).get("campaign_kit")
            or (mongo_doc.get("metadata_info") or {}).get("campaign_kit")
        )
    if not kit and proj and proj.validation_campaign:
        kit = proj.validation_campaign.campaign_kit
    if not kit and proj and proj.metadata_info:
        kit = proj.metadata_info.get("campaign_kit")
    kit = dict(kit or {})

    announcement_post = kit.get("announcementPost") if isinstance(kit, dict) else ""

    from app.services.campaign_media import build_creator_channel_image_prompt, generate_campaign_social_image
    prompt = build_creator_channel_image_prompt(
        creator_name=creator_name,
        creator_handle=creator_handle,
        niche=niche,
        product_name=product_name,
        product_tagline=product_tagline,
        target_audience=target_audience,
        announcement_post=announcement_post or "",
        user_prompt=body.prompt if body else None
    )

    api_key = (body.apiKey if body and body.apiKey else None) or (x_gemini_key if isinstance(x_gemini_key, str) and x_gemini_key.strip() else None)
    openai_key = (body.openaiApiKey if body and body.openaiApiKey else None) or (x_openai_key if isinstance(x_openai_key, str) and x_openai_key.strip() else None)
    caller = (body.caller if body and body.caller else None) or (x_user_role if isinstance(x_user_role, str) and x_user_role.strip() else None) or "admin"

    try:
        media_result = generate_campaign_social_image(
            prompt,
            api_key=api_key,
            openai_api_key=openai_key,
            creator_name=creator_name,
            creator_handle=creator_handle,
            creator_id=creator_id,
            niche=niche,
            product_name=product_name,
            project_id=effective_proj_id,
            generated_by=caller
        )
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        raise HTTPException(500, detail=f"Image generation failed: {str(e)}")

    kit["postImageUrl"] = media_result["url"]
    kit["postImageDataUrl"] = media_result.get("data_url")
    kit["postImagePrompt"] = prompt
    kit["postImageModel"] = media_result.get("model")
    kit["postImageProvider"] = media_result.get("provider")
    kit["cloudinaryPublicId"] = media_result.get("cloudinary_public_id")
    kit["cloudinaryUrl"] = media_result.get("cloudinary_url")
    kit["optimizeUrl"] = media_result.get("optimize_url")
    kit["thumbnailUrl"] = media_result.get("thumbnail_url")
    kit["creatorFolder"] = media_result.get("creator_folder")
    kit["isCloudinary"] = media_result.get("is_cloudinary", False)
    kit["generatedBy"] = caller

    # Synchronize regenerated image into posting schedule tasks
    schedule = list(kit.get("postingSchedule") or [])
    for t in schedule:
        ch = (t.get("channel") or "").lower()
        ti = (t.get("title") or "").lower()
        dk = t.get("draftKey") or ""
        is_post_task = dk == "announcementPost" or (
            dk != "videoScript"
            and dk != "newsletterDraft"
            and dk != "storySequence"
            and (
                "announcement" in ti
                or "launch" in ti
                or ("video" not in ch and "story" not in ch and "email" not in ch and "newsletter" not in ch)
            )
        )
        if is_post_task:
            t["imageUrl"] = media_result["url"]
            t["postImageUrl"] = media_result["url"]
            t["imageGeneratedAt"] = datetime.utcnow().isoformat()
    kit["postingSchedule"] = schedule

    file_id = f"cld-{media_result.get('cloudinary_public_id') or media_result.get('filename')}"
    new_file_item = {
        "id": file_id,
        "public_id": media_result.get("cloudinary_public_id"),
        "name": "Campaign Announcement Graphic",
        "url": media_result["url"],
        "optimizeUrl": media_result.get("optimize_url") or media_result["url"],
        "thumbnailUrl": media_result.get("thumbnail_url") or media_result["url"],
        "size": "1024x1024",
        "type": "png",
        "category": "campaign_media",
        "creatorSlug": media_result.get("creator_slug"),
        "folder": media_result.get("creator_folder"),
        "generatedBy": caller,
        "updatedAt": "Cloudinary CDN" if media_result.get("cloudinary_url") else "Local Storage"
    }

    if mongo_doc:
        mongo_doc["campaignKit"] = kit
        mongo_doc["campaign_kit"] = kit
        if "validationCampaign" in mongo_doc and isinstance(mongo_doc["validationCampaign"], dict):
            mongo_doc["validationCampaign"]["campaignKit"] = kit
            mongo_doc["validationCampaign"]["campaign_kit"] = kit
        meta = dict(mongo_doc.get("metadataInfo") or mongo_doc.get("metadata_info") or {})
        meta["campaign_kit"] = kit
        cur_files = list(meta.get("project_files") or [])
        cur_files = [f for f in cur_files if f.get("name") != "Campaign Announcement Graphic" and f.get("id") != file_id]
        cur_files.append(new_file_item)
        meta["project_files"] = cur_files
        mongo_doc["metadataInfo"] = meta
        mongo_doc["metadata_info"] = meta
        _save_mongo_project(mongo_doc)

        try:
            from app.mongodb import get_collection
            vc_coll = get_collection("validation_campaigns")
            if vc_coll is not None:
                clean_kit = _sanitize_heavy_media(kit)
                vc_coll.update_one(
                    {"$or": [{"project_id": effective_proj_id}, {"id": f"vc_{effective_proj_id}"}, {"id": effective_proj_id}, {"_id": effective_proj_id}]},
                    {"$set": {"campaign_kit": clean_kit, "campaignKit": clean_kit, "updated_at": datetime.utcnow().isoformat()}},
                    upsert=True
                )
        except Exception as err:
            logger.debug(f"[MongoDB] validation_campaigns sync note: {err}")

    if proj:
        campaign = proj.validation_campaign
        if not campaign:
            campaign = ValidationCampaign(project_id=proj.id)
            db.add(campaign)
        campaign.campaign_kit = kit
        flag_modified(campaign, "campaign_kit")
        meta_sql = dict(proj.metadata_info or {})
        meta_sql["campaign_kit"] = kit
        cur_files_sql = list(meta_sql.get("project_files") or [])
        cur_files_sql = [f for f in cur_files_sql if f.get("name") != "Campaign Announcement Graphic" and f.get("id") != file_id]
        cur_files_sql.append(new_file_item)
        meta_sql["project_files"] = cur_files_sql
        proj.metadata_info = meta_sql
        flag_modified(proj, "metadata_info")
        db.commit()
        db.refresh(proj)

    return {
        "success": True,
        "media": media_result,
        "project": _format_any_project(mongo_doc=mongo_doc, db_proj=proj)
    }


@router.post("/{project_id}/campaign/generate-video")
def generate_project_campaign_video(
    project_id: str,
    body: Optional[GenerateCampaignMediaRequest] = None,
    x_gemini_key: Optional[str] = Header(None),
    x_openai_key: Optional[str] = Header(None),
    x_user_role: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Generate 60s campaign video teaser and store in Cloudinary under creator's profile folder."""
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None
    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    creator_name = (mongo_doc.get("creatorName") or mongo_doc.get("creator_name")) if mongo_doc else None
    if not creator_name and proj:
        creator_name = proj.creator_name
    creator_name = creator_name or "Creator"

    creator_handle = (mongo_doc.get("creatorHandle") or mongo_doc.get("creator_handle")) if mongo_doc else None
    if not creator_handle and proj:
        creator_handle = proj.creator_handle
    creator_handle = creator_handle or ""

    creator_id = (mongo_doc.get("creatorId") or mongo_doc.get("creator_id")) if mongo_doc else None
    if not creator_id and proj:
        creator_id = proj.creator_id
    creator_id = creator_id or ""

    niche = mongo_doc.get("niche") if mongo_doc else (proj.niche if proj else None)
    niche = niche or "Tech"

    product_name = (mongo_doc.get("productName") or mongo_doc.get("product_name")) if mongo_doc else None
    if not product_name and proj:
        product_name = proj.product_name
    product_name = product_name or "Software Venture"

    product_tagline = (mongo_doc.get("productTagline") or mongo_doc.get("product_tagline")) if mongo_doc else None
    if not product_tagline and proj:
        product_tagline = proj.product_tagline
    product_tagline = product_tagline or ""

    effective_proj_id = (mongo_doc.get("id") or mongo_doc.get("_id")) if mongo_doc else (proj.id if proj else project_id)

    kit = None
    if mongo_doc:
        kit = (
            mongo_doc.get("campaignKit")
            or mongo_doc.get("campaign_kit")
            or (mongo_doc.get("validationCampaign") or {}).get("campaignKit")
            or (mongo_doc.get("validationCampaign") or {}).get("campaign_kit")
            or (mongo_doc.get("metadataInfo") or {}).get("campaign_kit")
            or (mongo_doc.get("metadata_info") or {}).get("campaign_kit")
        )
    if not kit and proj and proj.validation_campaign:
        kit = proj.validation_campaign.campaign_kit
    if not kit and proj and proj.metadata_info:
        kit = proj.metadata_info.get("campaign_kit")
    kit = dict(kit or {})

    video_script = kit.get("videoScript") if isinstance(kit, dict) else ""
    post_image_url = (body.postImageUrl if body and body.postImageUrl else None) or (kit.get("postImageUrl") if isinstance(kit, dict) else None)

    from app.services.campaign_media import build_creator_channel_video_prompt, generate_campaign_video
    prompt = build_creator_channel_video_prompt(
        creator_name=creator_name,
        creator_handle=creator_handle,
        niche=niche,
        product_name=product_name,
        product_tagline=product_tagline,
        video_script=video_script or "",
        user_prompt=body.prompt if body else None
    )

    api_key = (body.apiKey if body and body.apiKey else None) or (x_gemini_key if isinstance(x_gemini_key, str) and x_gemini_key.strip() else None)
    openai_key = (body.openaiApiKey if body and body.openaiApiKey else None) or (x_openai_key if isinstance(x_openai_key, str) and x_openai_key.strip() else None)
    caller = (body.caller if body and body.caller else None) or (x_user_role if isinstance(x_user_role, str) and x_user_role.strip() else None) or "admin"

    try:
        media_result = generate_campaign_video(
            prompt,
            api_key=api_key,
            openai_api_key=openai_key,
            creator_name=creator_name,
            creator_handle=creator_handle,
            creator_id=creator_id,
            product_name=product_name,
            niche=niche,
            post_image_url=post_image_url,
            video_script=video_script,
            project_id=effective_proj_id,
            generated_by=caller
        )
    except Exception as e:
        logger.error(f"Video generation error: {e}")
        raise HTTPException(500, detail=f"Video generation failed: {str(e)}")

    kit["videoUrl"] = media_result["url"]
    kit["videoPrompt"] = prompt
    kit["videoModel"] = media_result.get("model")
    kit["videoProvider"] = media_result.get("provider")
    kit["cloudinaryVideoPublicId"] = media_result.get("cloudinary_video_public_id") or media_result.get("cloudinary_public_id")
    kit["cloudinaryVideoUrl"] = media_result.get("cloudinary_video_url") or media_result.get("cloudinary_url")
    kit["videoThumbnailUrl"] = media_result.get("thumbnail_url")
    kit["videoOptimizeUrl"] = media_result.get("optimize_url")
    kit["creatorFolder"] = media_result.get("creator_folder")
    kit["isVideoCloudinary"] = media_result.get("is_cloudinary", False)
    kit["generatedBy"] = caller

    # Synchronize regenerated video into posting schedule tasks
    schedule = list(kit.get("postingSchedule") or [])
    for t in schedule:
        ch = (t.get("channel") or "").lower()
        ti = (t.get("title") or "").lower()
        dk = t.get("draftKey") or ""
        is_video_task = dk == "videoScript" or (
            dk != "announcementPost"
            and dk != "newsletterDraft"
            and dk != "storySequence"
            and (
                "video" in ch
                or "video" in ti
                or "reel" in ch
                or "short" in ch
                or ("youtube" in ch and "community" not in ch)
            )
        )
        if is_video_task:
            t["videoUrl"] = media_result["url"]
            t["thumbnailUrl"] = media_result.get("thumbnail_url") or media_result["url"]
            t["videoGeneratedAt"] = datetime.utcnow().isoformat()
        elif dk == "announcementPost" and "videoUrl" in t:
            t.pop("videoUrl", None)
            t.pop("thumbnailUrl", None)
            t.pop("videoGeneratedAt", None)
    kit["postingSchedule"] = schedule

    file_id = f"cld-{media_result.get('cloudinary_public_id') or media_result.get('filename')}"
    new_video_file = {
        "id": file_id,
        "public_id": media_result.get("cloudinary_public_id"),
        "name": "Campaign Launch Teaser Video",
        "url": media_result["url"],
        "thumbnailUrl": media_result.get("thumbnail_url"),
        "optimizeUrl": media_result.get("optimize_url") or media_result["url"],
        "size": "MP4 1080p",
        "type": "mp4",
        "category": "campaign_media",
        "creatorSlug": media_result.get("creator_slug"),
        "folder": media_result.get("creator_folder"),
        "generatedBy": caller,
        "updatedAt": "Cloudinary CDN" if media_result.get("cloudinary_url") else "Local Storage"
    }

    if mongo_doc:
        mongo_doc["campaignKit"] = kit
        mongo_doc["campaign_kit"] = kit
        if "validationCampaign" in mongo_doc and isinstance(mongo_doc["validationCampaign"], dict):
            mongo_doc["validationCampaign"]["campaignKit"] = kit
            mongo_doc["validationCampaign"]["campaign_kit"] = kit
        meta = dict(mongo_doc.get("metadataInfo") or mongo_doc.get("metadata_info") or {})
        meta["campaign_kit"] = kit
        cur_files = list(meta.get("project_files") or [])
        cur_files = [f for f in cur_files if f.get("name") != "Campaign Launch Teaser Video" and f.get("id") != file_id]
        cur_files.append(new_video_file)
        meta["project_files"] = cur_files
        mongo_doc["metadataInfo"] = meta
        mongo_doc["metadata_info"] = meta
        _save_mongo_project(mongo_doc)

        try:
            from app.mongodb import get_collection
            vc_coll = get_collection("validation_campaigns")
            if vc_coll is not None:
                clean_kit = _sanitize_heavy_media(kit)
                vc_coll.update_one(
                    {"$or": [{"project_id": effective_proj_id}, {"id": f"vc_{effective_proj_id}"}, {"id": effective_proj_id}, {"_id": effective_proj_id}]},
                    {"$set": {"campaign_kit": clean_kit, "campaignKit": clean_kit, "updated_at": datetime.utcnow().isoformat()}},
                    upsert=True
                )
        except Exception as err:
            logger.debug(f"[MongoDB] validation_campaigns sync note: {err}")

    if proj:
        campaign = proj.validation_campaign
        if not campaign:
            campaign = ValidationCampaign(project_id=proj.id)
            db.add(campaign)
        campaign.campaign_kit = kit
        flag_modified(campaign, "campaign_kit")
        meta_sql = dict(proj.metadata_info or {})
        meta_sql["campaign_kit"] = kit
        cur_files_sql = list(meta_sql.get("project_files") or [])
        cur_files_sql = [f for f in cur_files_sql if f.get("name") != "Campaign Launch Teaser Video" and f.get("id") != file_id]
        cur_files_sql.append(new_video_file)
        meta_sql["project_files"] = cur_files_sql
        proj.metadata_info = meta_sql
        flag_modified(proj, "metadata_info")
        db.commit()
        db.refresh(proj)

    return {
        "success": True,
        "media": media_result,
        "project": _format_any_project(mongo_doc=mongo_doc, db_proj=proj)
    }


@router.post("/{project_id}/campaign/send-post-email")
def send_campaign_post_email_endpoint(
    project_id: str,
    body: Optional[SendCampaignPostEmailRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Dispatches a ready-to-publish campaign post kit (caption + visual preview + asset download links)
    directly to the creator or founder's email inbox. Zero app logins required.
    """
    from app.services.autonomous_campaign_dispatcher import dispatch_campaign_post_email
    try:
        req = body or SendCampaignPostEmailRequest()
        result = dispatch_campaign_post_email(
            db=db,
            project_id=project_id,
            task_id=req.taskId,
            day=req.day,
            recipient_email=req.recipientEmail,
            caller=req.caller or "admin"
        )
        m_doc = _find_mongo_project(project_id)
        proj = db.get(CoLaunchProject, project_id) if db else None
        return {
            **result,
            "project": _format_any_project(mongo_doc=m_doc, db_proj=proj)
        }
    except Exception as e:
        logger.error(f"Error dispatching campaign post email: {e}")
        raise HTTPException(500, detail=str(e))


@router.post("/{project_id}/campaign/toggle-autonomous-delivery")
def toggle_autonomous_delivery_endpoint(
    project_id: str,
    body: ToggleAutonomousDeliveryRequest,
    db: Session = Depends(get_db)
):
    """
    Configures autonomous 24-hour delivery of scheduled post kits directly to creator/founder inbox.
    """
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None

    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    kit = dict(
        (mongo_doc.get("campaign_kit") or mongo_doc.get("campaignKit") or (mongo_doc.get("metadataInfo") or {}).get("campaign_kit") if mongo_doc else None)
        or (proj.validation_campaign.campaign_kit if proj and proj.validation_campaign and getattr(proj.validation_campaign, "campaign_kit", None) else None)
        or (proj.metadata_info or {}).get("campaign_kit") if proj else {}
        or {}
    )

    tz = (body.timezone or "").strip() or kit.get("creatorTimezone") or "UTC"
    country = (body.country or "").strip() or kit.get("creatorCountry") or "United States"
    target_email = (
        (body.recipientEmail or "").strip()
        or (mongo_doc.get("creatorEmail") or mongo_doc.get("creator_email") or mongo_doc.get("email") if mongo_doc else None)
        or (getattr(proj, "creator_email", "") if proj else "")
        or (getattr(proj, "email", "") if proj else "")
    )

    delivery_config = {
        "enabled": bool(body.enabled),
        "recipientEmail": target_email,
        "preferredHour": 0 if body.preferredHour is None else body.preferredHour,
        "intervalHours": 24,
        "intervalSeconds": 86400,
        "cadence": "24_hours",
        "timezone": tz,
        "country": country,
        "dispatchTime": "Every 24 Hours",
        "dispatchSchedule": f"Every 24 Hours ({tz})",
        "updatedAt": datetime.utcnow().isoformat()
    }
    kit["autonomousEmailDelivery"] = delivery_config
    kit["creatorTimezone"] = tz
    kit["creatorCountry"] = country
    if target_email:
        kit["creatorEmail"] = target_email

    # 1. Update MongoDB Atlas collections directly
    if mongo_doc:
        p_doc = mongo_doc
        meta_m = p_doc.get("metadataInfo") if isinstance(p_doc.get("metadataInfo"), dict) else {}
        meta_m["campaign_kit"] = kit
        p_doc["metadataInfo"] = meta_m
        p_doc["campaign_kit"] = kit
        p_doc["campaignKit"] = kit
        if target_email:
            p_doc["creatorEmail"] = target_email
            p_doc["creator_email"] = target_email
        _save_mongo_project(p_doc)

        try:
            from app.mongodb import get_collection
            vc_coll = get_collection("validation_campaigns")
            if vc_coll is not None:
                doc_id = str(p_doc.get("_id") or p_doc.get("id") or project_id)
                vc_doc = {
                    "_id": doc_id,
                    "project_id": doc_id,
                    "campaign_kit": _sanitize_heavy_media(kit),
                    "updated_at": datetime.utcnow().isoformat(),
                }
                vc_coll.replace_one({"_id": doc_id}, vc_doc, upsert=True)
        except Exception as m_err:
            logger.debug(f"[MongoDB] Toggle autonomous delivery validation_campaigns note: {m_err}")

    # 2. Update SQLite in-memory models if present
    if proj:
        campaign = proj.validation_campaign
        if not campaign:
            campaign = ValidationCampaign(project_id=proj.id)
            db.add(campaign)
        campaign.campaign_kit = kit
        flag_modified(campaign, "campaign_kit")

        meta = dict(proj.metadata_info or {})
        meta["campaign_kit"] = kit
        proj.metadata_info = meta
        flag_modified(proj, "metadata_info")

        db.commit()
        db.refresh(proj)

    # 3. Return updated response prioritizing Mongo
    updated_m = _find_mongo_project(project_id)
    return {
        "success": True,
        "config": delivery_config,
        "project": _format_any_project(mongo_doc=updated_m, db_proj=proj)
    }


@router.post("/{project_id}/campaign/start-simulation")
async def start_campaign_simulation_endpoint(
    project_id: str,
    body: Optional[StartCampaignSimulationRequest] = None,
    db: Session = Depends(get_db)
):
    """
    Starts an autonomous 5-minute campaign email delivery simulation.
    Sends Post 1, Post 2, Post 3, etc. spaced across the requested interval.
    """
    from app.services.autonomous_campaign_dispatcher import run_autonomous_campaign_simulation, get_simulation_status
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None
    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    raw_kit = (
        (mongo_doc.get("campaign_kit") or mongo_doc.get("campaignKit") or (mongo_doc.get("metadataInfo") or {}).get("campaign_kit") if mongo_doc else None)
        or (proj.validation_campaign.campaign_kit if proj and proj.validation_campaign else None)
        or (proj.metadata_info or {}).get("campaign_kit") if proj else {}
        or {}
    )
    auto_config = raw_kit.get("autonomousEmailDelivery") or {}
    recipient = (
        (body.recipientEmail if body and body.recipientEmail else None)
        or auto_config.get("recipientEmail")
        or (mongo_doc.get("creatorEmail") or mongo_doc.get("creator_email") or mongo_doc.get("email") if mongo_doc else None)
        or getattr(proj, "creator_email", None)
        or getattr(proj, "email", None)
    )
    interval = (body.intervalSeconds if body and body.intervalSeconds else 42)
    total_posts = (body.totalPosts if body and body.totalPosts else None)
    prod_name = (mongo_doc.get("productName") or mongo_doc.get("product_name") if mongo_doc else None) or getattr(proj, "product_name", "Venture")

    # Launch in background asyncio task
    asyncio.create_task(run_autonomous_campaign_simulation(
        project_id=project_id,
        recipient_email=recipient,
        interval_seconds=interval,
        total_posts=total_posts
    ))

    return {
        "success": True,
        "message": f"Autonomous campaign simulation started for {prod_name}. Delivering posts every {interval}s to {recipient}.",
        "status": get_simulation_status(project_id)
    }


@router.get("/{project_id}/campaign/simulation-status")
def get_campaign_simulation_status_endpoint(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Fetches real-time status and delivery logs for active autonomous simulation."""
    from app.services.autonomous_campaign_dispatcher import get_simulation_status
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None
    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")
    return {
        "success": True,
        "projectId": project_id,
        "simulation": get_simulation_status(project_id)
    }


@router.post("/{project_id}/campaign/stop-simulation")
def stop_campaign_simulation_endpoint(
    project_id: str,
    db: Session = Depends(get_db)
):
    """Cancels any running autonomous campaign simulation."""
    from app.services.autonomous_campaign_dispatcher import stop_simulation, get_simulation_status
    stopped = stop_simulation(project_id)
    return {
        "success": True,
        "stopped": stopped,
        "status": get_simulation_status(project_id)
    }



@router.get("/{project_id}/creator-tasks")
def get_creator_tasks(project_id: str, db: Session = Depends(get_db)):
    """Fetch Step 3 Creator Campaign Tasks directly from MongoDB Atlas."""
    proj = _find_mongo_project(project_id)
    if not proj:
        return []
    tasks = proj.get("creatorTasks") or proj.get("creator_tasks") or []
    if not tasks:
        kit = proj.get("campaignKit") or proj.get("campaign_kit") or {}
        tasks = kit.get("postingSchedule") or []
    return tasks


@router.patch("/{project_id}/creator-tasks/{task_id}")
def update_creator_task(project_id: str, task_id: str, body: UpdateTaskRequest, db: Session = Depends(get_db)):
    """Update a Step 3 creator campaign checklist task directly in MongoDB Atlas."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")
    tasks = proj.get("creatorTasks") or proj.get("creator_tasks") or []
    matched = None
    for t in tasks:
        if str(t.get("id")) == str(task_id):
            matched = t
            break
    if not matched:
        raise HTTPException(404, f"Task '{task_id}' not found")
    if body.status is not None:
        matched["status"] = body.status
        if body.status == "completed":
            matched["completedAt"] = datetime.utcnow().isoformat()
    if body.content_draft is not None: matched["content"] = body.content_draft
    if body.cta_text is not None: matched["cta"] = body.cta_text
    if body.tracking_link is not None: matched["trackingLink"] = body.tracking_link
    _save_mongo_project(proj)
    return {"status": "success", "taskId": task_id, "newStatus": matched.get("status")}


@router.post("/{project_id}/remind-task/{task_id}")
def send_task_reminder(project_id: str, task_id: str, db: Session = Depends(get_db)):
    """Send an email reminder to creator directly using MongoDB Atlas project."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")
    tasks = proj.get("creatorTasks") or proj.get("creator_tasks") or []
    task = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if not task:
        raise HTTPException(404, f"Task '{task_id}' not found")

    from app.integrations.email_provider import email_provider
    from app.config import settings

    creator_email = (proj.get("creatorEmail") or proj.get("creator_email") or settings.RECIPIENT_EMAIL or "").strip()
    base_frontend = (settings.FRONTEND_URL or "https://creator-forge-frontend.vercel.app").rstrip("/")
    c_handle = proj.get("creatorHandle") or proj.get("creator_handle") or "creator"
    portal_slug = c_handle.replace("@", "").replace(" ", "").strip().lower()
    portal_magic_link = f"{base_frontend}/portal/{portal_slug}?token={proj.get('portalToken', 'cf_sec_live')}&project={proj.get('id')}"

    task_title = task.get("task_title") or task.get("title") or "Campaign Post"
    day_num = task.get("dayNumber") or task.get("day") or 1
    content_draft = task.get("content") or task.get("content_draft") or ""
    channel = task.get("channel") or "social"

    subject = f"[LAUNCH MISSION] Day {day_num} Posting Reminder: {task_title}"
    body_text = f"Hi {proj.get('creatorName') or 'there'},\n\nReminder for Day {day_num} co-launch milestone for {proj.get('productName')}:\n\nMission: {task_title}\nChannel: {channel}\n\nDraft:\n{content_draft}\n\nPortal: {portal_magic_link}"

    if creator_email and "@" in creator_email:
        email_provider.send(to_email=creator_email, subject=subject, body_text=body_text)

    return {"status": "success", "sent": True, "recipient": creator_email, "taskId": task_id, "channel": channel}


@router.post("/{project_id}/reservations")
def add_reservation(project_id: str, body: AddReservationRequest, db: Session = Depends(get_db)):
    """Record a verified buyer pre-order / reservation in Step 4 Telemetry directly in MongoDB."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")

    telem = proj.get("telemetry") or {}
    cur_res = list(telem.get("reservations") or [])
    res_item = {
        "id": f"res_{int(datetime.utcnow().timestamp()*1000)}",
        "name": body.name,
        "email": body.email,
        "amount": float(body.amount),
        "tier": body.tier,
        "channel": body.channel,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "status": "paid"
    }
    cur_res.insert(0, res_item)
    telem["reservations"] = cur_res
    telem["presalesCount"] = len(cur_res)
    new_revenue = sum(float(r.get("amount", 0)) for r in cur_res)
    telem["presalesRevenue"] = new_revenue
    proj["currentPresales"] = new_revenue
    proj["current_presales"] = new_revenue
    proj["reservations"] = cur_res

    cur_visitors = max(int(telem.get("visitors", 0)), len(cur_res) * 6)
    telem["visitors"] = cur_visitors
    proj["visitors"] = cur_visitors
    conv = round((len(cur_res) / cur_visitors) * 100, 1) if cur_visitors > 0 else 0.0
    telem["conversionRate"] = conv
    proj["conversionRate"] = conv

    cur_attr = dict(telem.get("channelAttribution") or {})
    chan = body.channel or "direct"
    cur_attr[chan] = cur_attr.get(chan, 0) + 1
    telem["channelAttribution"] = cur_attr
    proj["telemetry"] = telem

    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


@router.post("/{project_id}/gate-decision")
def record_gate_decision(project_id: str, body: GateDecisionRequest, db: Session = Depends(get_db)):
    """Record Step 5 Executive Validation Gate decision directly in MongoDB."""
    proj = _find_mongo_project(project_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")

    target = float(proj.get("presaleTarget") or proj.get("presale_target") or 5000.0)
    achieved = float(proj.get("currentPresales") or proj.get("current_presales") or 0.0)

    if body.decision == "pass_to_phase2":
        proj["currentPhase"] = 2
        proj["current_phase"] = 2
        proj["currentStep"] = "plan"
        proj["current_step"] = "plan"
        proj["status"] = "building"
        gate_status = "passed"
    elif body.decision == "iterate_validation":
        proj["currentPhase"] = 1
        proj["current_phase"] = 1
        proj["currentStep"] = "optimize"
        proj["current_step"] = "optimize"
        proj["status"] = "validating"
        gate_status = "iterating"
    else:
        proj["status"] = "killed"
        gate_status = "failed"

    gates = list(proj.get("gateDecisions") or proj.get("gate_decisions") or [])
    gates.insert(0, {
        "id": f"gate_{int(datetime.utcnow().timestamp()*1000)}",
        "decision": body.decision,
        "targetRevenue": target,
        "achievedRevenue": achieved,
        "gateStatus": gate_status,
        "notes": body.notes or f"Executive gate decision: {body.decision}",
        "decidedAt": datetime.utcnow().isoformat()
    })
    proj["gateDecisions"] = gates
    proj["gate_decisions"] = gates

    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


@router.post("/record-visit")
def record_visit_universal(body: TrackVisitRequest, db: Session = Depends(get_db)):
    """Universal public page visit recorder operating directly on MongoDB Atlas."""
    if body.path and ("/dashboard" in body.path or "/admin" in body.path):
        return {"status": "ignored", "message": "Admin dashboard views are not tracked as customer visits"}

    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    if coll is None:
        return {"status": "ok", "message": "No database"}

    proj = None
    if body.projectId:
        proj = _find_mongo_project(body.projectId)

    if not proj and body.slug:
        clean_slug = body.slug.lower().strip()
        proj = coll.find_one({"$or": [
            {"product_name": {"$regex": clean_slug, "$options": "i"}},
            {"productName": {"$regex": clean_slug, "$options": "i"}},
            {"creator_handle": clean_slug},
            {"creatorHandle": clean_slug},
            {"id": clean_slug}
        ]})

    if not proj:
        proj = coll.find_one()

    if not proj:
        return {"status": "ok", "message": "No active project"}

    telem = proj.get("telemetry") or {}
    telem["views"] = int(telem.get("views") or 0) + 1

    meta = dict(proj.get("metadataInfo") or proj.get("metadata_info") or {})
    raw_tracked = meta.get("tracked_client_ids") or []
    tracked_clients = set(raw_tracked)
    client_key = (body.clientId or body.fingerprint or "").strip()

    is_truly_new = False
    if client_key:
        if client_key not in tracked_clients:
            tracked_clients.add(client_key)
            meta["tracked_client_ids"] = list(tracked_clients)[-5000:]
            proj["metadataInfo"] = meta
            proj["metadata_info"] = meta
            is_truly_new = True
    elif body.isNewVisitor is True:
        is_truly_new = True

    if is_truly_new or not telem.get("visitors"):
        telem["visitors"] = max(1, len(tracked_clients) if tracked_clients else (int(telem.get("visitors") or 0) + 1))
        proj["visitors"] = telem["visitors"]

    chan = body.channel or "Direct / Other"
    cur_attr = dict(telem.get("channelAttribution") or {})
    if is_truly_new or chan not in cur_attr:
        cur_attr[chan] = cur_attr.get(chan, 0) + 1
        telem["channelAttribution"] = cur_attr

    res_count = len(telem.get("reservations") or [])
    if telem.get("visitors") and telem["visitors"] > 0:
        telem["conversionRate"] = round((res_count / telem["visitors"]) * 100, 1)
        proj["conversionRate"] = telem["conversionRate"]

    proj["telemetry"] = telem
    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


@router.post("/{project_id}/track-visit")
def track_project_visit(project_id: str, body: TrackVisitRequest, db: Session = Depends(get_db)):
    """Track a visit for a specific project ID."""
    body.projectId = project_id
    return record_visit_universal(body, db)


@router.post("/record-preorder")
def record_preorder_universal(body: RecordPreorderRequest, db: Session = Depends(get_db)):
    """Universal public pre-order recorder directly in MongoDB Atlas."""
    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    if coll is None:
        raise HTTPException(500, "Database unavailable")

    proj = None
    if body.projectId:
        proj = _find_mongo_project(body.projectId)

    if not proj and body.slug:
        clean_slug = body.slug.lower().strip()
        proj = coll.find_one({"$or": [
            {"product_name": {"$regex": clean_slug, "$options": "i"}},
            {"productName": {"$regex": clean_slug, "$options": "i"}},
            {"creator_handle": clean_slug},
            {"creatorHandle": clean_slug},
            {"id": clean_slug}
        ]})

    if not proj:
        proj = coll.find_one()

    if not proj:
        raise HTTPException(404, "No active co-launch project found to record reservation")

    telem = proj.get("telemetry") or {}
    res_id = f"res_{int(datetime.utcnow().timestamp()*1000)}"
    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    reservation_item = {
        "id": res_id,
        "name": body.name,
        "email": body.email,
        "amount": float(body.amount),
        "tier": body.tier or "Founding Pass",
        "paymentMethod": body.paymentMethod or "Stripe",
        "channel": body.channel or "Direct / Other",
        "txId": body.txId or f"tx_{res_id}",
        "timestamp": timestamp_str,
        "status": "Paid"
    }

    cur_res = list(telem.get("reservations") or [])
    cur_res.insert(0, reservation_item)
    telem["reservations"] = cur_res
    telem["presalesCount"] = len(cur_res)
    new_revenue = sum(float(r.get("amount", 0)) for r in cur_res)
    telem["presalesRevenue"] = new_revenue
    proj["currentPresales"] = new_revenue
    proj["current_presales"] = new_revenue
    proj["reservations"] = cur_res

    cur_visitors = max(int(telem.get("visitors", 0)), len(cur_res) * 5, 1)
    telem["visitors"] = cur_visitors
    proj["visitors"] = cur_visitors
    conv = round((len(cur_res) / cur_visitors) * 100, 1) if cur_visitors > 0 else 0.0
    telem["conversionRate"] = conv
    proj["conversionRate"] = conv

    cur_attr = dict(telem.get("channelAttribution") or {})
    chan = body.channel or "Direct / Other"
    cur_attr[chan] = cur_attr.get(chan, 0) + 1
    telem["channelAttribution"] = cur_attr

    meta = dict(proj.get("metadataInfo") or proj.get("metadata_info") or {})
    act_logs = list(meta.get("activity_logs") or [])
    act_item = {
        "id": f"act_{int(datetime.utcnow().timestamp()*1000)}",
        "action": "Customer Pre-Order Received",
        "details": f"${body.amount:.0f} {body.tier} reserved by {body.name} ({body.email}) via {body.paymentMethod or 'Stripe'}",
        "category": "revenue",
        "timestamp": timestamp_str
    }
    act_logs.insert(0, act_item)
    meta["activity_logs"] = act_logs[:50]
    proj["metadataInfo"] = meta
    proj["metadata_info"] = meta
    proj["telemetry"] = telem

    _save_mongo_project(proj)
    return _normalize_mongo_project_dict(proj)


@router.post("/record-survey-response")
@router.post("/{project_id}/survey-response")
def record_survey_response_universal(
    body: RecordSurveyResponseRequest,
    project_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Universal public survey response recorder called by /survey/:slug form or simulation.
    Persists respondent answers, increments question response counts, updates telemetry, and logs activity exclusively in MongoDB Atlas.
    """
    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    vc_coll = get_collection("validation_campaigns")

    target_id = project_id or body.projectId
    doc = None
    if coll is not None:
        if target_id:
            clean_target = target_id.replace("@", "").lower().strip()
            doc = coll.find_one({"$or": [
                {"_id": target_id}, {"id": target_id},
                {"creator_id": target_id}, {"creatorId": target_id},
                {"creator_handle": clean_target}, {"creatorHandle": clean_target},
            ]})
        if not doc and body.slug:
            clean_slug = body.slug.lower().strip()
            slug_regex = clean_slug.replace("-", "[- ]")
            doc = coll.find_one({"$or": [
                {"id": body.slug}, {"slug": clean_slug},
                {"creator_handle": clean_slug}, {"creatorHandle": clean_slug},
                {"product_name": {"$regex": slug_regex, "$options": "i"}},
                {"productName": {"$regex": slug_regex, "$options": "i"}},
            ]})
        if not doc:
            doc = coll.find_one()

    if not doc:
        raise HTTPException(404, "No active co-launch project found to record survey response")

    pid = str(doc.get("_id") or doc.get("id"))
    vc_doc = vc_coll.find_one({"$or": [{"_id": pid}, {"project_id": pid}]}) if vc_coll is not None else None

    campaign_kit = dict(doc.get("campaignKit") or doc.get("campaign_kit") or (vc_doc.get("campaign_kit") if vc_doc else None) or {})
    res_survey = dict((vc_doc.get("research_survey") if vc_doc else None) or campaign_kit.get("research_survey") or (doc.get("metadataInfo") or {}).get("research_survey") or {})

    res_id = f"sr_{int(datetime.utcnow().timestamp()*1000)}"
    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    res_name = (body.name or body.respondentName or "").strip() or "Community Member"
    res_email = (body.email or body.respondentEmail or "").strip() or f"respondent_{res_id[-4:]}@example.com"
    res_rating = int(body.rating if body.rating is not None else (body.intentScore if body.intentScore is not None else 8))

    response_item = {
        "id": res_id,
        "name": res_name,
        "email": res_email,
        "rating": res_rating,
        "answers": body.answers or {},
        "submittedAt": body.submittedAt or timestamp_str,
        "date": datetime.utcnow().strftime("%Y-%m-%d")
    }

    cur_responses = list(res_survey.get("responses") or [])
    cur_responses.insert(0, response_item)
    res_survey["responses"] = cur_responses

    # Update question response counts
    cur_questions = list(res_survey.get("questions") or [])
    if cur_questions:
        for q in cur_questions:
            qid = q.get("id")
            if body.answers and qid in body.answers and str(body.answers[qid]).strip():
                q["responseCount"] = int(q.get("responseCount") or 0) + 1
            elif not body.answers:
                q["responseCount"] = int(q.get("responseCount") or 0) + 1
        res_survey["questions"] = cur_questions

    campaign_kit["research_survey"] = res_survey
    doc["campaignKit"] = campaign_kit
    doc["campaign_kit"] = campaign_kit

    meta = dict(doc.get("metadataInfo") or doc.get("metadata_info") or {})
    meta["survey_responses"] = cur_responses

    telemetry = dict(doc.get("telemetry") or {})
    telemetry["signups"] = max(int(telemetry.get("signups") or 0), len(cur_responses))
    doc["telemetry"] = telemetry

    # Activity Log
    act_logs = list(meta.get("activity_logs") or [])
    act_item = {
        "id": f"act_{int(datetime.utcnow().timestamp()*1000)}",
        "action": "Audience Survey Response Recorded",
        "details": f"Survey feedback submitted by {res_name} ({res_email}) with intent score {res_rating}/10",
        "category": "research",
        "timestamp": timestamp_str
    }
    act_logs.insert(0, act_item)
    meta["activity_logs"] = act_logs[:50]
    doc["metadataInfo"] = meta
    doc["metadata_info"] = meta

    if coll is not None:
        coll.replace_one({"_id": doc.get("_id", pid)}, doc, upsert=True)

    if vc_coll is not None:
        vc_payload = {
            "_id": pid,
            "project_id": pid,
            "research_survey": res_survey,
            "campaign_kit": campaign_kit,
            "updated_at": datetime.utcnow().isoformat()
        }
        vc_coll.replace_one({"_id": pid}, vc_payload, upsert=True)

    return _normalize_mongo_project_dict(doc)


@router.delete("/{project_id}/survey-response/{response_id}")
def delete_survey_response(project_id: str, response_id: str, db: Session = Depends(get_db)):
    """Delete a single survey response and recalculate question response counts in MongoDB Atlas."""
    doc = _find_mongo_project(project_id)
    if not doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    vc_coll = get_collection("validation_campaigns")
    pid = str(doc.get("_id") or doc.get("id"))

    vc_doc = vc_coll.find_one({"$or": [{"_id": pid}, {"project_id": pid}]}) if vc_coll is not None else None
    campaign_kit = dict(doc.get("campaignKit") or doc.get("campaign_kit") or (vc_doc.get("campaign_kit") if vc_doc else None) or {})
    res_survey = dict((vc_doc.get("research_survey") if vc_doc else None) or campaign_kit.get("research_survey") or (doc.get("metadataInfo") or {}).get("research_survey") or {})

    cur_responses = list(res_survey.get("responses") or [])
    new_responses = [r for r in cur_responses if r.get("id") != response_id]
    res_survey["responses"] = new_responses

    cur_questions = list(res_survey.get("questions") or [])
    if cur_questions:
        for q in cur_questions:
            qid = q.get("id")
            count = 0
            for r in new_responses:
                ans = r.get("answers") or {}
                if qid in ans and str(ans[qid]).strip():
                    count += 1
                elif not ans:
                    count += 1
            q["responseCount"] = count
        res_survey["questions"] = cur_questions

    campaign_kit["research_survey"] = res_survey
    doc["campaignKit"] = campaign_kit
    doc["campaign_kit"] = campaign_kit

    meta = dict(doc.get("metadataInfo") or doc.get("metadata_info") or {})
    meta["survey_responses"] = new_responses

    telemetry = dict(doc.get("telemetry") or {})
    telemetry["signups"] = len(new_responses)
    doc["telemetry"] = telemetry
    doc["metadataInfo"] = meta
    doc["metadata_info"] = meta

    if coll is not None:
        coll.replace_one({"_id": doc.get("_id", pid)}, doc, upsert=True)

    if vc_coll is not None:
        vc_coll.replace_one({"_id": pid}, {
            "_id": pid,
            "project_id": pid,
            "research_survey": res_survey,
            "campaign_kit": campaign_kit,
            "updated_at": datetime.utcnow().isoformat()
        }, upsert=True)

    return _normalize_mongo_project_dict(doc)


@router.delete("/{project_id}/survey-responses")
def clear_all_survey_responses(project_id: str, db: Session = Depends(get_db)):
    """Clear all survey responses and reset question response counts to 0 in MongoDB Atlas."""
    doc = _find_mongo_project(project_id)
    if not doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    vc_coll = get_collection("validation_campaigns")
    pid = str(doc.get("_id") or doc.get("id"))

    vc_doc = vc_coll.find_one({"$or": [{"_id": pid}, {"project_id": pid}]}) if vc_coll is not None else None
    campaign_kit = dict(doc.get("campaignKit") or doc.get("campaign_kit") or (vc_doc.get("campaign_kit") if vc_doc else None) or {})
    res_survey = dict((vc_doc.get("research_survey") if vc_doc else None) or campaign_kit.get("research_survey") or (doc.get("metadataInfo") or {}).get("research_survey") or {})

    res_survey["responses"] = []
    res_survey["analysis"] = None

    cur_questions = list(res_survey.get("questions") or [])
    if cur_questions:
        for q in cur_questions:
            q["responseCount"] = 0
        res_survey["questions"] = cur_questions

    campaign_kit["research_survey"] = res_survey
    doc["campaignKit"] = campaign_kit
    doc["campaign_kit"] = campaign_kit

    meta = dict(doc.get("metadataInfo") or doc.get("metadata_info") or {})
    meta["survey_responses"] = []
    meta["survey_analysis"] = None

    telemetry = dict(doc.get("telemetry") or {})
    telemetry["signups"] = 0
    doc["telemetry"] = telemetry
    doc["metadataInfo"] = meta
    doc["metadata_info"] = meta

    if coll is not None:
        coll.replace_one({"_id": doc.get("_id", pid)}, doc, upsert=True)

    if vc_coll is not None:
        vc_coll.replace_one({"_id": pid}, {
            "_id": pid,
            "project_id": pid,
            "research_survey": res_survey,
            "campaign_kit": campaign_kit,
            "updated_at": datetime.utcnow().isoformat()
        }, upsert=True)

    return _normalize_mongo_project_dict(doc)


@router.post("/{project_id}/log-activity")
def log_admin_activity(project_id: str, body: LogActivityRequest, db: Session = Depends(get_db)):
    """Record an audit trail action conducted by the admin or AI agent."""
    mongo_doc = _find_mongo_project(project_id)
    proj = db.get(CoLaunchProject, project_id) if db else None
    if not proj and not mongo_doc:
        raise HTTPException(404, f"Project '{project_id}' not found")

    timestamp_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    act_item = {
        "id": f"act_{int(datetime.utcnow().timestamp()*1000)}",
        "action": body.action,
        "details": body.details or body.action,
        "category": body.category or "admin_action",
        "step": body.step or "plan",
        "phase": body.phase or 1,
        "timestamp": timestamp_str
    }

    act_logs = []
    if mongo_doc:
        meta_m = dict(mongo_doc.get("metadataInfo") or mongo_doc.get("metadata_info") or {})
        act_logs = list(meta_m.get("activity_logs") or [])
        act_logs.insert(0, act_item)
        meta_m["activity_logs"] = act_logs[:50]
        mongo_doc["metadataInfo"] = meta_m
        mongo_doc["metadata_info"] = meta_m
        _save_mongo_project(mongo_doc)

    if proj:
        meta_sql = dict(proj.metadata_info or {})
        act_logs_sql = list(meta_sql.get("activity_logs") or [])
        act_logs_sql.insert(0, act_item)
        meta_sql["activity_logs"] = act_logs_sql[:50]
        proj.metadata_info = meta_sql
        flag_modified(proj, "metadata_info")
        db.commit()
        db.refresh(proj)
        if not act_logs:
            act_logs = act_logs_sql

    return {"status": "success", "activity": act_item, "activityLogs": act_logs}


@router.get("/by-slug/{slug}")
def get_project_by_slug(slug: str, db: Session = Depends(get_db)):
    """Lookup active co-launch project by slug, product name, or creator handle from MongoDB Atlas."""
    import re
    clean_slug = slug.lower().strip()
    alpha_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_slug)

    # 1. Prioritize MongoDB co_launch_projects
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None:
            slug_regex = clean_slug.replace("-", "[- ]")
            query_conditions = [
                {"id": slug},
                {"id": clean_slug},
                {"slug": clean_slug},
                {"creator_handle": clean_slug},
                {"creator_handle": f"@{clean_slug}"},
                {"creatorHandle": clean_slug},
                {"creatorHandle": f"@{clean_slug}"},
                {"creator_handle": {"$regex": f"^{re.escape(clean_slug)}$", "$options": "i"}},
                {"creatorHandle": {"$regex": f"^{re.escape(clean_slug)}$", "$options": "i"}},
                {"product_name": {"$regex": slug_regex, "$options": "i"}},
                {"productName": {"$regex": slug_regex, "$options": "i"}},
                {"title": {"$regex": slug_regex, "$options": "i"}},
            ]
            if alpha_slug:
                query_conditions.extend([
                    {"product_name": {"$regex": alpha_slug, "$options": "i"}},
                    {"productName": {"$regex": alpha_slug, "$options": "i"}},
                    {"title": {"$regex": alpha_slug, "$options": "i"}},
                    {"slug": {"$regex": alpha_slug, "$options": "i"}},
                ])

            doc = coll.find_one({"$or": query_conditions})

            # If still not found by direct query, perform in-memory normalized comparison across all documents
            if not doc and alpha_slug:
                all_docs = list(coll.find({}))
                for cand in all_docs:
                    c_slug = re.sub(r"[^a-zA-Z0-9]", "", str(cand.get("slug") or "")).lower()
                    c_name = re.sub(r"[^a-zA-Z0-9]", "", str(cand.get("productName") or cand.get("product_name") or cand.get("title") or "")).lower()
                    c_handle = re.sub(r"[^a-zA-Z0-9]", "", str(cand.get("creatorHandle") or cand.get("creator_handle") or "")).lower()
                    c_id = str(cand.get("id") or "").lower()
                    if alpha_slug in (c_slug, c_name, c_handle, c_id) or (c_name and alpha_slug in c_name) or (c_slug and alpha_slug in c_slug):
                        doc = cand
                        break

            # Fallback to first project if available
            if not doc:
                doc = coll.find_one()

            if doc:
                ws_coll = get_collection("workflow_states")
                ws_doc = ws_coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) if ws_coll is not None else None
                def_fee = 199.0
                if ws_doc:
                    def_fee = float(ws_doc.get("default_pass_price") or (ws_doc.get("extra_state") or {}).get("default_pass_price") or 199.0)
                return _normalize_mongo_project_dict(doc, def_fee)
    except Exception as e:
        logger.debug(f"[MongoDB] get_project_by_slug notice: {e}")

    # 2. Secondary fallback: SQLite session
    try:
        sql_proj = db.query(CoLaunchProject).filter(
            (CoLaunchProject.id == slug) |
            (CoLaunchProject.creator_handle.ilike(f"%{clean_slug}%")) |
            (CoLaunchProject.product_name.ilike(f"%{clean_slug}%"))
        ).first()
        if not sql_proj:
            sql_proj = db.query(CoLaunchProject).first()
        if sql_proj:
            return _format_project_response(sql_proj)
    except Exception as e:
        logger.debug(f"[SQLite] get_project_by_slug notice: {e}")

    raise HTTPException(404, f"No project found matching slug '{slug}' in MongoDB Atlas")


@router.delete("")
@router.delete("/")
def delete_all_projects(db: Session = Depends(get_db)):
    """Delete all co-launch projects directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    count = 0
    if coll is not None:
        count = coll.count_documents({})
        coll.delete_many({})
    return {"status": "success", "deleted_count": count, "message": f"Deleted {count} co-launch projects."}


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Delete a co-launch project directly from MongoDB Atlas."""
    from app.mongodb import get_collection
    coll = get_collection("co_launch_projects")
    if coll is not None:
        clean_target = project_id.replace("@", "").lower().strip()
        coll.delete_one({"$or": [
            {"_id": project_id},
            {"id": project_id},
            {"creator_id": project_id},
            {"creatorId": project_id},
            {"creator_handle": clean_target},
            {"creatorHandle": clean_target},
        ]})
    return {"status": "success", "message": f"Project '{project_id}' deleted."}
