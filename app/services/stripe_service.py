# -*- coding: utf-8 -*-
"""
Stripe Payment & Checkout Session Service for CoLaunch.
Handles:
  1. Creation of real Stripe Checkout sessions for buyer pre-orders
  2. Cryptographic verification of incoming Stripe webhook signatures
  3. Recording verified presale events in validation_events collection
  4. Updating project telemetry, real buyer reservations, and threshold status
"""
import os
import json
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def get_stripe_keys() -> Dict[str, str]:
    """Retrieve Stripe API keys from environment."""
    secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY", os.getenv("VITE_STRIPE_PUBLISHABLE_KEY", "")).strip()
    return {
        "secret_key": secret_key,
        "webhook_secret": webhook_secret,
        "publishable_key": publishable_key,
    }


def create_checkout_session(
    project_id: str,
    product_name: str,
    amount: float,
    tier_name: str,
    customer_name: Optional[str] = None,
    customer_email: Optional[str] = None,
    channel: Optional[str] = None,
    ref: Optional[str] = None,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Creates a Stripe Checkout Session for a pre-order.
    If STRIPE_SECRET_KEY is configured, creates a real Stripe session.
    If missing, returns an explicit test-mode checkout redirect URL.
    """
    keys = get_stripe_keys()
    secret_key = keys["secret_key"]

    meta = {
        "projectId": project_id,
        "productName": product_name,
        "tier": tier_name,
        "amount": str(amount),
        "channel": channel or "Direct / Other",
        "ref": ref or "",
        "customerName": customer_name or "",
        "customerEmail": customer_email or "",
    }
    if metadata:
        meta.update(metadata)

    # Base URLs
    base_success = success_url or f"https://colaunch.app/p/{project_id}?checkout_success=true"
    base_cancel = cancel_url or f"https://colaunch.app/p/{project_id}?checkout_cancelled=true"

    if secret_key:
        try:
            import stripe
            stripe.api_key = secret_key

            unit_cents = int(round(amount * 100))
            if unit_cents <= 0:
                unit_cents = 100  # Minimum 1 dollar

            session_params = {
                "payment_method_types": ["card"],
                "line_items": [{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"{product_name} — {tier_name}",
                            "description": f"Founding pre-order reservation for {product_name}",
                        },
                        "unit_amount": unit_cents,
                    },
                    "quantity": 1,
                }],
                "mode": "payment",
                "success_url": base_success + ("&" if "?" in base_success else "?") + "session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": base_cancel,
                "metadata": meta,
            }
            if customer_email and "@" in customer_email:
                session_params["customer_email"] = customer_email.strip()

            session = stripe.checkout.Session.create(**session_params)
            return {
                "checkoutUrl": session.url,
                "sessionId": session.id,
                "mode": "stripe_live",
                "isTestMode": False,
            }
        except Exception as e:
            logger.error(f"[Stripe] Error creating Checkout Session: {e}")
            raise RuntimeError(f"Stripe Checkout error: {str(e)}")

    # Test mode / Developer fallback when Stripe key is not configured
    test_session_id = f"cs_test_{uuid.uuid4().hex[:16]}"
    sep = "&" if "?" in base_success else "?"
    test_url = f"{base_success}{sep}test_checkout=true&session_id={test_session_id}&amount={amount:.2f}&tier={tier_name}&simulated=true"
    return {
        "checkoutUrl": test_url,
        "sessionId": test_session_id,
        "mode": "stripe_simulated_test",
        "isTestMode": True,
        "message": "STRIPE_SECRET_KEY not set. Operating in developer test mode.",
    }


def verify_and_construct_webhook_event(
    payload_bytes: bytes,
    sig_header: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verifies Stripe webhook signature and constructs event payload.
    Fails with ValueError if signature verification fails.
    """
    keys = get_stripe_keys()
    webhook_secret = keys["webhook_secret"]

    if webhook_secret:
        if not sig_header:
            raise ValueError("Missing stripe-signature header")
        try:
            import stripe
            event = stripe.Webhook.construct_event(
                payload_bytes, sig_header, webhook_secret
            )
            return event
        except Exception as e:
            logger.error(f"[Stripe] Webhook signature verification failed: {e}")
            raise ValueError(f"Invalid webhook signature: {e}")

    # No webhook secret configured: parse payload directly
    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid JSON payload: {e}")


def process_successful_checkout(session_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handles completed checkout session:
      1. Writes verified event to validation_events collection
      2. Appends real buyer reservation to project telemetry
      3. Recalculates presales revenue & evaluates validation threshold
      4. Logs audit activity in project metadata
    """
    from app.mongodb import get_collection
    from app.routers.projects import _find_mongo_project, _save_mongo_project

    meta = session_obj.get("metadata") or {}
    project_id = meta.get("projectId") or session_obj.get("client_reference_id")

    if not project_id:
        logger.warning("[Stripe] Received completed checkout session without projectId in metadata.")
        return {"status": "ignored", "reason": "no_project_id"}

    proj = _find_mongo_project(project_id)
    if not proj:
        logger.warning(f"[Stripe] Project '{project_id}' not found for completed checkout.")
        return {"status": "ignored", "reason": "project_not_found"}

    # Extract payment details
    raw_amount_total = session_obj.get("amount_total")
    if raw_amount_total is not None:
        amount = float(raw_amount_total) / 100.0
    else:
        amount = float(meta.get("amount") or 0.0)

    cust_details = session_obj.get("customer_details") or {}
    email = (
        cust_details.get("email")
        or session_obj.get("customer_email")
        or meta.get("customerEmail")
        or ""
    ).strip()
    name = (
        cust_details.get("name")
        or meta.get("customerName")
        or "Founding Supporter"
    ).strip()

    tx_id = (
        session_obj.get("payment_intent")
        or session_obj.get("id")
        or f"tx_{int(datetime.utcnow().timestamp()*1000)}"
    )
    tier = meta.get("tier") or "Founding Member Pass"
    channel = meta.get("channel") or "Direct / Other"
    is_simulated = bool(meta.get("simulated", False) or session_obj.get("is_test", False))
    now_iso = datetime.utcnow().isoformat()
    now_display = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    # 1. Log event in validation_events collection
    events_coll = get_collection("validation_events")
    if events_coll is not None:
        event_doc = {
            "_id": f"evt_{tx_id}",
            "id": f"evt_{tx_id}",
            "type": "presale",
            "projectId": str(proj.get("id") or proj.get("_id")),
            "amount": amount,
            "currency": session_obj.get("currency", "usd"),
            "email": email,
            "name": name,
            "tier": tier,
            "channel": channel,
            "txId": tx_id,
            "verified": not is_simulated,
            "simulated": is_simulated,
            "timestamp": now_iso,
        }
        events_coll.replace_one({"_id": event_doc["_id"]}, event_doc, upsert=True)

    # 2. Update Project Telemetry & Reservations
    telem = proj.get("telemetry") or {}
    cur_res = list(telem.get("reservations") or [])

    # Idempotency check: don't double count if webhook is redelivered
    already_recorded = any(str(r.get("txId")) == str(tx_id) for r in cur_res)
    if not already_recorded:
        new_res_item = {
            "id": f"res_{tx_id}",
            "name": name,
            "email": email,
            "amount": amount,
            "tier": tier,
            "channel": channel,
            "txId": tx_id,
            "paymentMethod": "Stripe",
            "timestamp": now_display,
            "status": "Paid",
            "verified": not is_simulated,
            "simulated": is_simulated,
        }
        cur_res.insert(0, new_res_item)

    telem["reservations"] = cur_res
    telem["presalesCount"] = len(cur_res)
    new_rev = sum(float(r.get("amount", 0)) for r in cur_res)
    telem["presalesRevenue"] = new_rev

    proj["currentPresales"] = new_rev
    proj["current_presales"] = new_rev
    proj["reservations"] = cur_res

    # Attribution
    cur_attr = dict(telem.get("channelAttribution") or {})
    cur_attr[channel] = cur_attr.get(channel, 0) + 1
    telem["channelAttribution"] = cur_attr

    # Conversion rate based on real visitors
    cur_visitors = int(telem.get("visitors", 0))
    if cur_visitors > 0:
        telem["conversionRate"] = round((len(cur_res) / cur_visitors) * 100, 1)
        proj["conversionRate"] = telem["conversionRate"]

    # 3. Check Validation Threshold
    presale_target = float(proj.get("presaleTarget") or proj.get("presale_target") or 5000.0)
    if new_rev >= presale_target and not proj.get("thresholdReached"):
        proj["thresholdReached"] = True
        proj["thresholdReachedAt"] = now_iso
        logger.info(f"[Stripe] Project '{project_id}' MET VALIDATION THRESHOLD! ${new_rev:.0f} / ${presale_target:.0f}")

    # 4. Activity Log
    meta_info = dict(proj.get("metadataInfo") or proj.get("metadata_info") or {})
    act_logs = list(meta_info.get("activity_logs") or [])
    act_logs.insert(0, {
        "id": f"act_{int(datetime.utcnow().timestamp()*1000)}",
        "action": "Verified Pre-Order Received",
        "details": f"${amount:.0f} {tier} reserved by {name} ({email}) via Stripe Checkout",
        "category": "revenue",
        "timestamp": now_iso,
    })
    meta_info["activity_logs"] = act_logs[:50]
    proj["metadataInfo"] = meta_info
    proj["metadata_info"] = meta_info
    proj["telemetry"] = telem

    _save_mongo_project(proj)
    return {
        "status": "success",
        "projectId": project_id,
        "amount": amount,
        "txId": tx_id,
        "totalRevenue": new_rev,
        "presaleTarget": presale_target,
        "thresholdReached": bool(proj.get("thresholdReached")),
    }
