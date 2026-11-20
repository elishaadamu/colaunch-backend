# -*- coding: utf-8 -*-
"""
Phase 3 · Launch. Makes "launch" real:

  - Orders: every checkout is recorded; paid status is confirmed with Stripe (test orders kept apart)
  - Product: a hosted production release (promoted from staging) or the team's own production URL
  - Go-live: flips the public page to buy-now and emails customers their access link
  - Telemetry: events (page views, sign-ups, activation, checkout, JS errors) + server uptime checks
  - Metrics: computed from those records only

Collections: launch_config, launch_events, launch_orders, uptime_checks, production_releases, launch_decisions
"""

import asyncio
import logging
import re
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth import require_operator, require_project_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/launch", tags=["launch"])

EVENT_TYPES = {"page_view", "signup", "activated", "session", "checkout_open", "checkout_error", "js_error", "custom"}


def _coll(name):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def _now():
    return datetime.utcnow()


def _iso(dt=None):
    return (dt or _now()).isoformat()


def _project(pid_or_slug: str) -> Optional[Dict[str, Any]]:
    from app.routers.projects import _find_mongo_project
    doc = _find_mongo_project(pid_or_slug)
    if doc:
        return doc
    from app.routers.beta_program import find_project_by_slug
    return find_project_by_slug(pid_or_slug)


def _pid(doc) -> str:
    return str(doc.get("_id") or doc.get("id"))


def _slug(doc) -> str:
    raw = doc.get("slug") or doc.get("productName") or doc.get("product_name") or "product"
    return re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-")


def get_config(pid: str) -> Dict[str, Any]:
    c = _coll("launch_config")
    doc = (c.find_one({"_id": pid}) if c is not None else None) or {}
    doc.pop("_id", None)
    return doc


def stripe_mode() -> str:
    from app.services.stripe_service import get_stripe_keys
    key = get_stripe_keys().get("secret_key") or ""
    return "live" if key.startswith(("sk_live", "rk_live")) else "test" if key.startswith(("sk_test", "rk_test")) else "none"


# ── Orders ───────────────────────────────────────────────────────────────────────────────
def record_checkout_started(pid: str, session_res: Dict[str, Any], body: Dict[str, Any]):
    """Called from the checkout-session endpoint: every checkout leaves a record."""
    oc = _coll("launch_orders")
    sid = session_res.get("sessionId") or session_res.get("id")
    if oc is None or not sid:
        return
    test = bool(session_res.get("isTestMode"))
    oc.update_one({"_id": sid}, {"$setOnInsert": {
        "project_id": pid, "sessionId": sid, "email": (body.get("email") or "").strip().lower(),
        "name": body.get("name") or "", "amount": float(body.get("amount") or 0), "tier": body.get("tier") or "",
        "ref": body.get("ref") or body.get("channel") or "direct", "channel": body.get("channel") or "",
        "status": "test" if test else "pending", "test": test, "createdAt": _iso(),
    }}, upsert=True)


class ConfirmOrderRequest(BaseModel):
    sessionId: str


@router.post("/order/confirm")
def confirm_order(body: ConfirmOrderRequest):
    """Customer returned from checkout. Ask Stripe whether it was paid and record it once."""
    oc = _coll("launch_orders")
    order = oc.find_one({"_id": body.sessionId}) if oc is not None else None
    if not order:
        raise HTTPException(404, "Order not found")
    if order.get("status") in ("paid", "test"):
        return {"status": order["status"], "test": order.get("test", False)}
    from app.services.stripe_service import get_stripe_keys, process_successful_checkout
    key = get_stripe_keys().get("secret_key")
    if not key:
        return {"status": order.get("status")}
    try:
        import stripe
        stripe.api_key = key
        sess = stripe.checkout.Session.retrieve(body.sessionId)
        sess = sess.to_dict() if hasattr(sess, "to_dict") else dict(sess)
    except Exception as e:
        logger.warning(f"[Launch] Stripe retrieve failed: {e}")
        return {"status": order.get("status")}
    if sess.get("payment_status") == "paid":
        process_successful_checkout(sess)  # idempotent by payment intent
        oc.update_one({"_id": body.sessionId}, {"$set": {"status": "paid", "paidAt": _iso(), "amount": (sess.get("amount_total") or 0) / 100.0 or order.get("amount")}})
        on_new_customer(order["project_id"], order.get("email"), order.get("name"))
        return {"status": "paid"}
    return {"status": order.get("status")}


def mark_paid_from_webhook(session_obj: Dict[str, Any]):
    oc = _coll("launch_orders")
    sid = session_obj.get("id")
    if oc is None or not sid:
        return
    order = oc.find_one({"_id": sid})
    if order and order.get("status") != "paid":
        oc.update_one({"_id": sid}, {"$set": {"status": "paid", "paidAt": _iso()}})
        on_new_customer(order["project_id"], order.get("email"), order.get("name"))


# ── Access + onboarding ──────────────────────────────────────────────────────────────────
def access_link(doc: Dict[str, Any], email: str) -> str:
    from app.config import settings
    from app.routers.beta_program import email_token
    cfg = get_config(_pid(doc))
    if cfg.get("mode") == "external" and cfg.get("productionUrl"):
        return cfg["productionUrl"]
    base = (getattr(settings, "PUBLIC_APP_URL", "") or "https://colaunch-2.vercel.app").rstrip("/")
    return f"{base}/app/{_slug(doc)}?token={email_token(email)}"


def send_onboarding(doc: Dict[str, Any], email: str, name: str = "") -> bool:
    if not email or "@" not in email or email.endswith("@example.com"):
        return False
    cfg = get_config(_pid(doc))
    ob = cfg.get("onboarding") or {}
    product = doc.get("productName") or "your product"
    creator = doc.get("creatorName") or "the team"
    link = access_link(doc, email)
    first = (name or "").split(" ")[0] or "there"
    subject = (ob.get("subject") or f"{product} is live. Here's your access").replace("{{product}}", product)
    body = (ob.get("body") or (
        "Hi {{first_name}},\n\n{{product}} is live, and you're one of the first in.\n\n"
        "Start here: {{link}}\n\nFirst thing to try: {{first_step}}\n\n"
        "Reply to this email if anything gets in your way.\n\n{{creator}}"
    ))
    body = (body.replace("{{first_name}}", first).replace("{{product}}", product).replace("{{link}}", link)
            .replace("{{creator}}", creator).replace("{{first_step}}", ob.get("firstStep") or "open it and complete the first setup step"))
    html = (f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#18181b;">'
            f'<p style="font:700 13px sans-serif;color:#c81e3c;letter-spacing:1px;text-transform:uppercase;margin:0 0 12px;">{product} is live</p>'
            + "".join(f'<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">{line}</p>' for line in body.replace(link, "").split("\n") if line.strip())
            + f'<a href="{link}" style="display:inline-block;background:#c81e3c;color:#fff;padding:12px 20px;font-weight:700;text-decoration:none;">Open {product}</a></div>')
    try:
        from app.integrations.email_provider import EmailProvider
        EmailProvider().send(to_email=email, subject=subject, body_html="<!DOCTYPE html>" + html, body_text=body,
                             from_name=f"{product}", reply_to=cfg.get("supportEmail") or None)
        sent = _coll("launch_onboarding")
        if sent is not None:
            sent.update_one({"_id": f"{_pid(doc)}:{email.lower()}"}, {"$set": {"project_id": _pid(doc), "email": email.lower(), "sentAt": _iso()}}, upsert=True)
        return True
    except Exception as e:
        logger.warning(f"[Launch] onboarding email failed for {email}: {e}")
        return False


def on_new_customer(pid: str, email: str, name: str = ""):
    doc = _project(pid)
    if doc and (doc.get("launchStatus") == "LIVE"):
        send_onboarding(doc, email, name)


@router.get("/access")
def product_access(slug: str, token: str = ""):
    """Paying customers open the hosted production release."""
    doc = _project(slug)
    if not doc:
        raise HTTPException(404, "Not found")
    pid = _pid(doc)
    if doc.get("launchStatus") != "LIVE":
        raise HTTPException(403, "Not live yet")
    from app.routers.beta_program import email_token, reservation_token
    customers = {}
    for r in doc.get("reservations") or []:
        if isinstance(r, dict) and r.get("email"):
            customers[email_token(r["email"])] = r
            customers[reservation_token(r.get("id"))] = r
    oc = _coll("launch_orders")
    if oc is not None:
        for o in oc.find({"project_id": pid, "status": "paid"}):
            customers[email_token(o.get("email") or "")] = o
    if token not in customers:
        raise HTTPException(403, "This link isn't valid. Use the link from your access email.")
    rel = _coll("production_releases")
    release = rel.find_one({"_id": pid}) if rel is not None else None
    if not release:
        raise HTTPException(404, "No production release")
    release.pop("_id", None)
    from app.routers.projects import _filter_public_project_dict
    return {"project": _filter_public_project_dict(dict(doc)), "projectId": pid, "release": release,
            "customer": {"name": (customers[token].get("name") or "").split(" ")[0]}}


# ── Telemetry ────────────────────────────────────────────────────────────────────────────
class EventRequest(BaseModel):
    projectId: Optional[str] = None
    slug: Optional[str] = None
    type: str
    anon: Optional[str] = None
    ref: Optional[str] = None
    path: Optional[str] = None
    source: Optional[str] = "page"  # page | product | app
    meta: Optional[Dict[str, Any]] = None


_rate: Dict[str, List[float]] = {}


@router.post("/event")
def track_event(body: EventRequest, request: Request):
    if body.type not in EVENT_TYPES:
        raise HTTPException(400, "Unknown event")
    ip = request.client.host if request.client else "?"
    now = time.time()
    hits = [t for t in _rate.get(ip, []) if now - t < 60]
    if len(hits) > 120:
        raise HTTPException(429, "Too many events")
    _rate[ip] = hits + [now]
    doc = _project(body.projectId or body.slug or "")
    if not doc:
        raise HTTPException(404, "Unknown project")
    ec = _coll("launch_events")
    if ec is not None:
        meta = {k: (str(v)[:300] if v is not None else None) for k, v in (body.meta or {}).items()} if body.meta else {}
        ec.insert_one({"_id": uuid.uuid4().hex, "project_id": _pid(doc), "type": body.type, "anon": (body.anon or "")[:64],
                       "ref": (body.ref or "")[:60].lower(), "path": (body.path or "")[:200], "source": (body.source or "page")[:20],
                       "meta": meta, "at": _iso()})
    return {"ok": True}


SNIPPET = r"""(function(){var P="%PID%",A="%API%";var k="cl_anon",a=localStorage.getItem(k);if(!a){a=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem(k,a)}
var q=new URLSearchParams(location.search),r=q.get("ref")||q.get("utm_source")||localStorage.getItem("cl_ref")||"";if(q.get("ref")||q.get("utm_source"))localStorage.setItem("cl_ref",r);
function s(t,m){try{fetch(A+"/api/launch/event",{method:"POST",headers:{"Content-Type":"application/json"},keepalive:true,body:JSON.stringify({projectId:P,type:t,anon:a,ref:r,path:location.pathname,source:"product",meta:m||null})})}catch(e){}}
window.colaunch={track:function(t,m){s(t==="signup"||t==="activated"?t:"custom",Object.assign({name:t},m||{}))}};
window.addEventListener("error",function(e){s("js_error",{message:String(e.message||"").slice(0,200),file:String(e.filename||"").slice(-80),line:e.lineno})});
window.addEventListener("unhandledrejection",function(e){s("js_error",{message:String((e.reason&&e.reason.message)||e.reason||"").slice(0,200)})});
s("session");})();"""


@router.get("/t.js")
def tracking_snippet(p: str, request: Request):
    from app.config import settings
    base = (getattr(settings, "BACKEND_URL", "") or str(request.base_url)).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    js = SNIPPET.replace("%PID%", re.sub(r"[^A-Za-z0-9_\-]", "", p)).replace("%API%", base)
    return Response(content=js, media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


# ── Uptime checks ────────────────────────────────────────────────────────────────────────
def check_url(url: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoLaunch-Uptime/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
        ok = 200 <= code < 400
    except Exception as e:
        code = getattr(e, "code", None) or 0
        ok = False
    return {"ok": ok, "status": code, "ms": int((time.time() - t0) * 1000)}


def run_uptime_tick():
    cfgs = _coll("launch_config")
    uc = _coll("uptime_checks")
    if cfgs is None or uc is None:
        return
    for cfg in cfgs.find({"mode": "external", "productionUrl": {"$regex": "^https?://"}}):
        res = check_url(cfg["productionUrl"])
        uc.insert_one({"_id": uuid.uuid4().hex, "project_id": cfg["_id"], "url": cfg["productionUrl"], "at": _iso(), **res})


async def start_uptime_loop(interval_seconds: int = 300):
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(run_uptime_tick)
        except Exception as e:
            logger.debug(f"[Launch] uptime tick: {e}")
        await asyncio.sleep(interval_seconds)


# ── Operator: config, readiness, go-live ─────────────────────────────────────────────────
class LaunchConfig(BaseModel):
    mode: Optional[str] = None            # hosted | external
    productionUrl: Optional[str] = None
    launchDate: Optional[str] = None
    price: Optional[float] = None
    priceLabel: Optional[str] = None
    supportEmail: Optional[str] = None
    faqs: Optional[List[Dict[str, str]]] = None
    onboarding: Optional[Dict[str, str]] = None


@router.put("/{pid}/config")
def save_config(pid: str, body: LaunchConfig, user=Depends(require_project_owner)):
    c = _coll("launch_config")
    patch = {k: v for k, v in body.dict().items() if v is not None}
    if "productionUrl" in patch and patch["productionUrl"] and not re.match(r"^https?://", patch["productionUrl"]):
        patch["productionUrl"] = "https://" + patch["productionUrl"]
    patch["updatedAt"] = _iso()
    c.update_one({"_id": pid}, {"$set": patch}, upsert=True)
    return {"ok": True, "config": get_config(pid)}


def readiness(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    pid = _pid(doc)
    cfg = get_config(pid)
    mode = cfg.get("mode") or "hosted"
    ec = _coll("launch_events")
    since = _iso(_now() - timedelta(days=7))
    product_events = ec.count_documents({"project_id": pid, "source": "product", "at": {"$gte": since}}) if ec is not None else 0
    from app.routers.beta_program import staging_for
    staging = staging_for(pid)
    rel = _coll("production_releases")
    release = rel.find_one({"_id": pid}, {"files": 0}) if rel is not None else None
    uc = _coll("uptime_checks")
    last_check = uc.find_one({"project_id": pid}, sort=[("at", -1)]) if uc is not None else None
    sm = stripe_mode()
    kit = doc.get("campaignKit") or {}
    price = cfg.get("price") or (kit.get("pricingConfig") or {}).get("foundingPrice")
    faqs = cfg.get("faqs") or []
    ob = cfg.get("onboarding") or {}

    def item(key, label, ok, detail, critical=True, warn=False):
        return {"key": key, "label": label, "status": "pass" if ok else ("warn" if warn else "fail"), "detail": detail, "critical": critical}

    if mode == "external":
        prod_ok = bool(cfg.get("productionUrl")) and bool(last_check and last_check.get("ok"))
        prod = item("production", "Production deployment", prod_ok,
                    f"{cfg.get('productionUrl')} · last check {'up' if last_check and last_check.get('ok') else 'down or not checked'}" if cfg.get("productionUrl") else "Add your production URL")
    else:
        prod = item("production", "Production deployment", bool(release or staging),
                    f"Release {release.get('version')} live" if release else (f"Staging {staging.get('version')} will be promoted at launch" if staging else "Deploy a staging build in Phase 2 first"))
    return [
        prod,
        item("billing", "Billing", sm == "live" and bool(price), f"Stripe {'live' if sm == 'live' else 'test mode' if sm == 'test' else 'not connected'} · price {('$' + str(price)) if price else 'not set'}", warn=(sm == "test")),
        item("onboarding", "Onboarding email", bool(ob.get("subject") and ob.get("body")), "Sent to every customer with their access link" if ob.get("subject") else "Write the welcome email"),
        item("analytics", "Analytics & activation", mode == "hosted" or product_events > 0, "Tracked automatically in the hosted app" if mode == "hosted" else (f"{product_events} events from your product this week" if product_events else "Install the tracking snippet in your product")),
        item("attribution", "Attribution", bool(kit.get("postingSchedule")), "Creator posts use ?ref= tracking links" if kit.get("postingSchedule") else "Generate the creator campaign", critical=False),
        item("errors", "Error tracking", mode == "hosted" or product_events > 0, "Browser errors are reported automatically" if mode == "hosted" or product_events else "Install the tracking snippet", critical=False),
        item("support", "Support & FAQs", bool(cfg.get("supportEmail")) and len(faqs) >= 3, f"{cfg.get('supportEmail') or 'No support email'} · {len(faqs)} FAQs", critical=False),
    ]


@router.get("/{pid}/state")
def launch_state(pid: str, user=Depends(require_project_owner)):
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    rel = _coll("production_releases")
    release = rel.find_one({"_id": _pid(doc)}, {"files": 0}) if rel is not None else None
    if release:
        release.pop("_id", None)
    return {"config": get_config(_pid(doc)), "readiness": readiness(doc), "release": release, "stripeMode": stripe_mode(),
            "launchStatus": doc.get("launchStatus") or "PREP", "liveAt": doc.get("liveAt"), "slug": _slug(doc),
            "snippetPath": f"/api/launch/t.js?p={_pid(doc)}"}


class GoLiveRequest(BaseModel):
    overrideReason: Optional[str] = None
    notifyCustomers: bool = True


@router.post("/{pid}/go-live")
def go_live(pid: str, body: GoLiveRequest, user=Depends(require_project_owner)):
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    pid = _pid(doc)
    checks = readiness(doc)
    failing = [c for c in checks if c["critical"] and c["status"] == "fail"]
    if failing and len((body.overrideReason or "").strip()) < 15:
        raise HTTPException(409, "Not ready: " + "; ".join(c["label"] for c in failing))
    cfg = get_config(pid)
    if (cfg.get("mode") or "hosted") == "hosted":
        st = _coll("staging_deployments")
        staging = st.find_one({"_id": pid}) if st is not None else None
        if staging:
            staging.pop("_id", None)
            _coll("production_releases").update_one({"_id": pid}, {"$set": {**staging, "promotedAt": _iso()}}, upsert=True)
    now = _iso()
    from app.routers.projects import _save_mongo_project
    doc["launchStatus"] = "LIVE"
    doc["isLive"] = True
    doc["liveAt"] = doc.get("liveAt") or now
    doc["publicMode"] = "live"
    doc["launchOverrideReason"] = body.overrideReason if failing else None
    _save_mongo_project(doc)
    sent = 0
    if body.notifyCustomers:
        seen = set()
        oc = _coll("launch_orders")
        people = [(r.get("email"), r.get("name")) for r in (doc.get("reservations") or []) if isinstance(r, dict)]
        if oc is not None:
            people += [(o.get("email"), o.get("name")) for o in oc.find({"project_id": pid, "status": "paid"})]
        for email, name in people:
            e = (email or "").lower()
            if e and e not in seen:
                seen.add(e)
                sent += 1 if send_onboarding(doc, e, name) else 0
    return {"ok": True, "liveAt": doc["liveAt"], "onboardingSent": sent, "overridden": bool(failing)}


@router.post("/{pid}/pause")
def pause_launch(pid: str, user=Depends(require_project_owner)):
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    from app.routers.projects import _save_mongo_project
    doc["launchStatus"] = "PAUSED"
    doc["publicMode"] = "preorder"
    _save_mongo_project(doc)
    return {"ok": True}


# ── Metrics (all computed from records) ──────────────────────────────────────────────────
CHANNEL_RULES = [("instagram", r"insta|\big\b|story"), ("video", r"tiktok|short|reel|youtube|\byt\b"), ("x", r"twitter|x_post|tweet|^x$"),
                 ("newsletter", r"newsletter|email|mail"), ("dm", r"\bdm\b|outreach")]


def channel_of(ref: str) -> str:
    s = (ref or "").lower()
    for key, pat in CHANNEL_RULES:
        if re.search(pat, s):
            return key
    return "direct"


def _window_stats(pid, start, end, orders, events):
    ev = [e for e in events if start <= e["at"] < end]
    od = [o for o in orders if start <= (o.get("paidAt") or o.get("createdAt") or "") < end]
    visitors = len({e["anon"] for e in ev if e["type"] == "page_view" and e["source"] == "page" and e["anon"]})
    opens = len([e for e in ev if e["type"] == "checkout_open"])
    paid = [o for o in od if o.get("status") == "paid"]
    return {"visitors": visitors, "checkoutOpens": opens, "customers": len(paid), "revenue": round(sum(float(o.get("amount") or 0) for o in paid), 2),
            "signups": len({e["anon"] for e in ev if e["type"] == "signup"}), "errors": len([e for e in ev if e["type"] in ("js_error", "checkout_error")])}


@router.get("/{pid}/metrics")
def launch_metrics(pid: str, user=Depends(require_project_owner)):
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    pid = _pid(doc)
    live_at = doc.get("liveAt")
    since = live_at or _iso(_now() - timedelta(days=30))
    ec, oc, uc = _coll("launch_events"), _coll("launch_orders"), _coll("uptime_checks")
    events = list(ec.find({"project_id": pid, "at": {"$gte": since}}, {"_id": 0})) if ec is not None else []
    orders = list(oc.find({"project_id": pid}, {"_id": 0})) if oc is not None else []
    paid = [o for o in orders if o.get("status") == "paid" and (o.get("paidAt") or "") >= since]
    test_orders = [o for o in orders if o.get("status") == "test"]
    visitors = {e["anon"] for e in events if e["type"] == "page_view" and e["source"] == "page" and e["anon"]}
    signups = {e["anon"] for e in events if e["type"] == "signup"}
    customer_emails = {(o.get("email") or "").lower() for o in paid if o.get("email")}
    # Activation: a customer who opened the product (hosted app sessions carry the email token)
    from app.routers.beta_program import email_token
    # Everyone with access: pre-order backers + customers since launch
    access_emails = set(customer_emails) | {(r.get("email") or "").lower() for r in (doc.get("reservations") or []) if isinstance(r, dict) and r.get("email")}
    tokens = {email_token(e): e for e in access_emails}
    activated_tokens = {(e.get("meta") or {}).get("token") for e in events if e["type"] == "activated" and e["source"] in ("app", "product")}
    opened_tokens = {(e.get("meta") or {}).get("token") for e in events if e["type"] == "session" and e["source"] == "app"}
    activated = len([t for t in tokens if t in activated_tokens]) + len({e["anon"] for e in events if e["type"] == "activated" and e["source"] == "product"})
    opened = len([t for t in tokens if t in opened_tokens])
    revenue = round(sum(float(o.get("amount") or 0) for o in paid), 2)

    # Attribution by channel
    first_ref = {}
    for e in sorted(events, key=lambda x: x["at"]):
        if e["anon"] and e["anon"] not in first_ref and e.get("ref"):
            first_ref[e["anon"]] = e["ref"]
    ch: Dict[str, Dict[str, Any]] = {}
    for anon in visitors:
        c = channel_of(first_ref.get(anon, ""))
        ch.setdefault(c, {"channel": c, "visitors": 0, "customers": 0, "revenue": 0.0})["visitors"] += 1
    for o in paid:
        c = channel_of(o.get("ref") or o.get("channel"))
        row = ch.setdefault(c, {"channel": c, "visitors": 0, "customers": 0, "revenue": 0.0})
        row["customers"] += 1
        row["revenue"] = round(row["revenue"] + float(o.get("amount") or 0), 2)
    for row in ch.values():
        row["conversion"] = round(row["customers"] / row["visitors"] * 100, 2) if row["visitors"] else None
    # Content: which ref tag (post) drove what
    content: Dict[str, Dict[str, Any]] = {}
    for anon, ref in first_ref.items():
        if anon in visitors:
            content.setdefault(ref, {"ref": ref, "visitors": 0, "customers": 0})["visitors"] += 1
    for o in paid:
        r = (o.get("ref") or "").lower()
        if r:
            content.setdefault(r, {"ref": r, "visitors": 0, "customers": 0})["customers"] += 1

    # Technical health
    checks = list(uc.find({"project_id": pid, "at": {"$gte": _iso(_now() - timedelta(days=7))}}, {"_id": 0})) if uc is not None else []
    js_errors = [e for e in events if e["type"] == "js_error"]
    checkout_errors = [e for e in events if e["type"] == "checkout_error"]
    opens = len([e for e in events if e["type"] == "checkout_open"])
    health = {
        "uptime": round(sum(1 for c in checks if c["ok"]) / len(checks) * 100, 2) if checks else None,
        "avgLatencyMs": int(sum(c["ms"] for c in checks if c["ok"]) / max(1, sum(1 for c in checks if c["ok"]))) if checks else None,
        "checks": len(checks), "lastCheck": checks[-1] if checks else None,
        "jsErrors": len(js_errors), "checkoutErrors": len(checkout_errors),
        "checkoutSuccess": round(len(paid) / opens * 100, 1) if opens else None,
        "topErrors": sorted({(e.get("meta") or {}).get("message", ""): 0 for e in js_errors}.keys())[:5],
    }

    # Period comparison (for the AI Launch Manager)
    now = _now()
    def iso(d): return d.isoformat()
    last24 = _window_stats(pid, iso(now - timedelta(hours=24)), iso(now), orders, events)
    prev72 = _window_stats(pid, iso(now - timedelta(hours=96)), iso(now - timedelta(hours=24)), orders, events)
    last7 = _window_stats(pid, iso(now - timedelta(days=7)), iso(now), orders, events)
    prev7 = _window_stats(pid, iso(now - timedelta(days=14)), iso(now - timedelta(days=7)), orders, events)
    daily = []
    for i in range(13, -1, -1):
        d0 = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        w = _window_stats(pid, iso(d0), iso(d0 + timedelta(days=1)), orders, events)
        daily.append({"date": d0.strftime("%Y-%m-%d"), **w})

    return {
        "liveAt": live_at, "since": since,
        "totals": {"visitors": len(visitors), "signups": len(signups), "customers": len(customer_emails) or len(paid),
                   "activated": activated, "revenue": revenue, "checkoutOpens": opens,
                   "conversion": round(len(paid) / len(visitors) * 100, 2) if visitors else None,
                   "withAccess": len(access_emails), "opened": opened,
                   "activationRate": round(activated / len(access_emails) * 100, 1) if access_emails else None,
                   "testOrders": len(test_orders)},
        "channels": sorted(ch.values(), key=lambda r: (-r["revenue"], -r["visitors"])),
        "content": sorted(content.values(), key=lambda r: (-r["customers"], -r["visitors"]))[:12],
        "health": health,
        "periods": {"last24h": last24, "prev72h": prev72, "last7d": last7, "prev7d": prev7},
        "daily": daily,
    }


class DecisionRequest(BaseModel):
    decision: str            # scale | iterate | maintain | kill
    recommended: Optional[str] = None
    overrideReason: Optional[str] = None
    reasons: Optional[List[str]] = None
    snapshot: Optional[Dict[str, Any]] = None


@router.post("/{pid}/decision")
def launch_decision(pid: str, body: DecisionRequest, user=Depends(require_project_owner)):
    if body.decision not in ("scale", "iterate", "maintain", "kill"):
        raise HTTPException(400, "Unknown decision")
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    pid = _pid(doc)
    if body.decision != body.recommended and body.decision != "iterate" and len((body.overrideReason or "").strip()) < 15:
        raise HTTPException(400, "Explain why you're going against the recommendation (15+ characters).")
    record = {"_id": uuid.uuid4().hex, "project_id": pid, "decision": body.decision, "recommended": body.recommended,
              "overrideReason": body.overrideReason, "reasons": body.reasons or [], "snapshot": body.snapshot or {}, "at": _iso()}
    dc = _coll("launch_decisions")
    if dc is not None:
        dc.insert_one(record)
    from app.routers.projects import _save_mongo_project
    status = {"scale": "scaling", "iterate": "iterating", "maintain": "maintaining", "kill": "killed"}[body.decision]
    doc["status"] = status
    doc["phase3Decision"] = {k: v for k, v in record.items() if k != "_id"}
    if body.decision == "kill":
        doc["launchStatus"] = "CLOSED"
        doc["publicMode"] = "closed"
        dl = _coll("campaign_delivery")
        if dl is not None:
            dl.update_one({"_id": pid}, {"$set": {"enabled": False}})
    _save_mongo_project(doc)
    from app.routers.sites import publish_on_launch
    publish_on_launch(doc, body.decision)
    return {"ok": True, "status": status}


@router.get("/{pid}/decisions")
def list_decisions(pid: str, user=Depends(require_project_owner)):
    dc = _coll("launch_decisions")
    return {"decisions": list(dc.find({"project_id": pid}, {"_id": 0}).sort("at", -1).limit(20)) if dc is not None else []}
