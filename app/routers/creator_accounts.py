# -*- coding: utf-8 -*-
"""
DIY creators: pay for the pass (real Stripe Checkout), create an account, sign in.

  GET  /api/creator-auth/diy/price?project=      the pass price the server will charge
  POST /api/creator-auth/diy/checkout            Stripe Checkout session for the DIY pass
  POST /api/creator-auth/diy/confirm             creator returned from Stripe: verify the payment with Stripe
  POST /api/creator-auth/signup                  name + email + password, only with a paid, unclaimed payment
  POST /api/creator-auth/login                   email + password -> creator session token
  GET  /api/creator-auth/me                      the signed-in creator and their projects

Collections: diy_payments (one per Stripe session), creator_accounts (PBKDF2-hashed passwords).
A creator session token only opens that creator's own projects (see app.auth.require_project_owner).
"""

import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import AuthRole, AuthenticatedUser, get_current_auth, _sign

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/creator-auth", tags=["creator-auth"])

CREATOR_TOKEN_PREFIX = "forge_cr_"
CREATOR_TOKEN_TTL = 7 * 24 * 3600


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coll(name: str):
    from app.mongodb import get_collection
    return get_collection(name)


_indexed = False


def _accounts():
    global _indexed
    c = _coll("creator_accounts")
    if not _indexed and c is not None:
        try:
            c.create_index("email", unique=True)
            c.create_index("project_ids")
            _coll("diy_payments").create_index("project_id")
        except Exception:
            pass
        _indexed = True
    return c


def _public_base() -> str:
    from app.config import settings
    return (getattr(settings, "PUBLIC_APP_URL", "") or "https://colaunch-2.vercel.app").rstrip("/")


def _project(pid: str) -> Dict[str, Any]:
    from app.routers.projects import _find_mongo_project
    doc = _find_mongo_project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    return doc


def _pid(doc) -> str:
    return str(doc.get("id") or doc.get("_id"))


def _portal_path(doc) -> str:
    handle = re.sub(r"[^a-z0-9_.-]", "", str(doc.get("creatorHandle") or doc.get("creator_handle") or "").lstrip("@").lower())
    return f"/portal/{handle or _pid(doc)}?view=projectos&project={_pid(doc)}"


# ── Session tokens ─────────────────────────────────────────────────────────────────────
def issue_creator_token(account_id: str) -> str:
    payload = f"{int(time.time()) + CREATOR_TOKEN_TTL}.{account_id}.{secrets.token_hex(8)}"
    return f"{CREATOR_TOKEN_PREFIX}{payload}.{_sign('creator:' + payload)}"


def verify_creator_token(token: str) -> Optional[Dict[str, Any]]:
    """Return the account document for a valid, unexpired creator session token."""
    if not token or not token.startswith(CREATOR_TOKEN_PREFIX):
        return None
    try:
        exp, account_id, nonce, sig = token[len(CREATOR_TOKEN_PREFIX):].split(".", 3)
        if int(exp) < int(time.time()):
            return None
        if not hmac.compare_digest(sig, _sign(f"creator:{exp}.{account_id}.{nonce}")):
            return None
        acc = _accounts().find_one({"_id": account_id, "active": {"$ne": False}})
        return acc
    except Exception:
        return None


# ── Price ──────────────────────────────────────────────────────────────────────────────
def diy_price(doc: Dict[str, Any]) -> float:
    """Same rule the portal shows: a custom project fee wins, then the studio default, then the project fee."""
    meta = doc.get("metadataInfo") or doc.get("metadata_info") or {}
    raw = doc.get("diyFee") if doc.get("diyFee") is not None else doc.get("diyPassPrice")
    if raw is None and isinstance(meta, dict):
        raw = meta.get("diy_fee") if meta.get("diy_fee") is not None else meta.get("diyPassPrice")
    try:
        project_fee = float(raw or 0)
    except (TypeError, ValueError):
        project_fee = 0.0
    default_fee = 0.0
    try:
        from app.routers.workflow import _get_mongo_workflow_state
        default_fee = float(_get_mongo_workflow_state().get("default_pass_price") or 0)
    except Exception:
        try:
            wf = _coll("workflow_states").find_one({"_id": "default"}) or {}
            extra = wf.get("extra_state") or {}
            default_fee = float(wf.get("default_pass_price") or wf.get("cobuilder_pass_price") or extra.get("default_pass_price") or 0)
        except Exception:
            default_fee = 0.0
    custom = bool(doc.get("hasCustomFee") or (isinstance(meta, dict) and meta.get("hasCustomFee")))
    if project_fee > 0 and (custom or not default_fee):
        return round(project_fee, 2)
    if default_fee > 0:
        return round(default_fee, 2)
    return round(project_fee, 2) if project_fee > 0 else 50.0


@router.get("/diy/price")
def get_diy_price(project: str):
    doc = _project(project)
    from app.routers.launch import stripe_mode
    return {"amount": diy_price(doc), "currency": "usd", "stripe": stripe_mode()}


# ── Checkout ───────────────────────────────────────────────────────────────────────────
class DiyCheckoutRequest(BaseModel):
    projectId: str
    email: Optional[str] = None


@router.post("/diy/checkout")
def diy_checkout(body: DiyCheckoutRequest):
    from app.services.stripe_service import get_stripe_keys
    key = get_stripe_keys().get("secret_key")
    if not key:
        raise HTTPException(503, "Payments are not set up yet (STRIPE_SECRET_KEY is missing on the server).")
    doc = _project(body.projectId)
    pid = _pid(doc)
    amount = diy_price(doc)
    product = doc.get("productName") or doc.get("product_name") or "your product"
    base = _public_base()
    try:
        import stripe
        stripe.api_key = key
        params = {
            "mode": "payment",
            "line_items": [{"quantity": 1, "price_data": {
                "currency": "usd", "unit_amount": int(round(amount * 100)),
                "product_data": {"name": f"CoLaunch DIY pass: {product}",
                                 "description": "Run Phase 1, 2 and 3 of your launch yourself, with the CoLaunch AI workspace."}}}],
            "success_url": f"{base}/creator/signup?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}{_portal_path(doc)}&diy=cancelled",
            "client_reference_id": pid,
            "metadata": {"kind": "diy_pass", "projectId": pid, "amount": str(amount)},
        }
        email = (body.email or doc.get("creatorEmail") or doc.get("email_public") or "").strip()
        if email and "@" in email:
            params["customer_email"] = email
        sess = stripe.checkout.Session.create(**params)
    except Exception as e:
        logger.error(f"[DIY] checkout failed: {e}")
        if "payment method" in str(e).lower():
            raise HTTPException(502, _stripe_setup_problem(key))
        raise HTTPException(502, f"Stripe could not start checkout: {str(e)[:200]}")
    c = _coll("diy_payments")
    if c is not None:
        c.update_one({"_id": sess.id}, {"$setOnInsert": {"project_id": pid, "amount": amount, "status": "pending",
                                                          "livemode": key.startswith(("sk_live", "rk_live")), "createdAt": _iso()}}, upsert=True)
    return {"checkoutUrl": sess.url, "sessionId": sess.id}


def _stripe_setup_problem(key: str) -> str:
    """Stripe refused to show any payment method: find out why, in words the operator can act on."""
    mode = "live" if key.startswith(("sk_live", "rk_live")) else "test"
    where = "Settings → Payments → Payment methods" + (" (with Test mode on)" if mode == "test" else " (with Test mode off)")
    found = []
    try:
        import stripe
        stripe.api_key = key
        try:
            acct = stripe.Account.retrieve()
            if not acct.get("charges_enabled"):
                found.append(f"the Stripe account ({acct.get('country') or 'unknown country'}) can't take {mode} payments yet: finish activation in Settings → Account (business details and a bank account)")
        except Exception as e:
            logger.info(f"[DIY] account check skipped: {e}")
        try:
            cfgs = stripe.PaymentMethodConfiguration.list(limit=20).get("data") or []
            default = next((c for c in cfgs if c.get("is_default")), None)
            if default is not None:
                card = default.get("card") or {}
                if not card.get("available"):
                    found.append(f"Cards is off in the default payment method configuration: turn it on in {where}")
        except Exception as e:
            logger.info(f"[DIY] payment method check skipped: {e}")
    except Exception:
        pass
    if found:
        return "Stripe can't show a payment method because " + "; and ".join(found) + "."
    return (f"Stripe has no payment method switched on for {mode} mode. Turn on Cards in {where}. "
            + ("Live payments also need the account fully activated (Settings → Account)." if mode == "live" else ""))


def _activate_project(pid: str, pay: Dict[str, Any]):
    """Write the DIY pass onto the project (overlaid on reads too, so a stale save can't drop it)."""
    sub = {
        "active": True, "status": "active", "plan": "diy_pass", "planName": "DIY pass",
        "amount": pay.get("amount"), "billingCycle": "one_time", "paymentMethod": "Stripe",
        "transactionId": pay.get("payment_intent") or pay.get("_id"), "unlockedAt": pay.get("paidAt") or _iso(),
        "unlockedPhases": [1, 2, 3], "livemode": pay.get("livemode", False),
    }
    pc = _coll("co_launch_projects")
    if pc is not None:
        pc.update_one({"$or": [{"_id": pid}, {"id": pid}]}, {"$set": {"isDIY": True, "diyOfferStatus": "paid", "diySubscription": sub}})
    try:
        from app.routers.projects import _project_cache
        for k, v in list(_project_cache.items()):
            if str(v.get("id") or v.get("_id")) == pid:
                v.update({"isDIY": True, "diyOfferStatus": "paid", "diySubscription": sub})
    except Exception:
        pass
    return sub


def mark_diy_paid(session_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Idempotent: called by the webhook and by /diy/confirm once Stripe says the session is paid."""
    sid = session_obj.get("id")
    meta = session_obj.get("metadata") or {}
    pid = meta.get("projectId") or session_obj.get("client_reference_id")
    if not sid or not pid or session_obj.get("payment_status") != "paid":
        return None
    c = _coll("diy_payments")
    cust = session_obj.get("customer_details") or {}
    upd = {"project_id": pid, "status": "paid", "email": (cust.get("email") or session_obj.get("customer_email") or "").lower(),
           "name": cust.get("name") or "", "amount": (session_obj.get("amount_total") or 0) / 100.0,
           "payment_intent": session_obj.get("payment_intent"), "livemode": bool(session_obj.get("livemode"))}
    prev = c.find_one({"_id": sid}) or {}
    if prev.get("status") != "paid":
        upd["paidAt"] = _iso()
    c.update_one({"_id": sid}, {"$set": upd}, upsert=True)
    pay = c.find_one({"_id": sid})
    _activate_project(pid, pay)
    return pay


def diy_overlay(pid: str) -> Dict[str, Any]:
    """Merged into every project read: the pass and whether the creator has an account."""
    out: Dict[str, Any] = {}
    c = _coll("diy_payments")
    if c is None or not pid:
        return out
    pay = c.find_one({"project_id": pid, "status": "paid"}, sort=[("paidAt", -1)])
    if not pay:
        return out
    out.update({"isDIY": True, "diyOfferStatus": "paid"})
    out["diySubscription"] = {"active": True, "status": "active", "plan": "diy_pass", "planName": "DIY pass", "amount": pay.get("amount"),
                              "paymentMethod": "Stripe", "transactionId": pay.get("payment_intent") or pay.get("_id"),
                              "unlockedAt": pay.get("paidAt"), "unlockedPhases": [1, 2, 3], "livemode": pay.get("livemode", False)}
    acc = _accounts().find_one({"project_ids": pid}, {"email": 1, "name": 1, "createdAt": 1})
    out["creatorAccount"] = {"email": acc["email"], "name": acc.get("name"), "createdAt": acc.get("createdAt")} if acc else None
    return out


class ConfirmRequest(BaseModel):
    sessionId: str


@router.post("/diy/confirm")
def diy_confirm(body: ConfirmRequest):
    from app.services.stripe_service import get_stripe_keys
    key = get_stripe_keys().get("secret_key")
    c = _coll("diy_payments")
    pay = c.find_one({"_id": body.sessionId}) if c is not None else None
    if not pay:
        raise HTTPException(404, "We couldn't find this payment. Start again from your portal.")
    if pay.get("status") != "paid":
        if not key:
            raise HTTPException(503, "Payments are not set up on the server.")
        try:
            import stripe
            stripe.api_key = key
            sess = stripe.checkout.Session.retrieve(body.sessionId)
            sess = sess.to_dict() if hasattr(sess, "to_dict") else dict(sess)
        except Exception as e:
            raise HTTPException(502, f"Couldn't reach Stripe to confirm the payment: {str(e)[:160]}")
        pay = mark_diy_paid(sess) or pay
    doc = _project(pay["project_id"])
    return {
        "paid": pay.get("status") == "paid",
        "amount": pay.get("amount"),
        "email": pay.get("email") or (doc.get("creatorEmail") or ""),
        "name": pay.get("name") or doc.get("creatorName") or "",
        "claimed": bool(pay.get("account_id")),
        "project": {"id": _pid(doc), "productName": doc.get("productName"), "creatorName": doc.get("creatorName")},
    }


# ── Accounts ───────────────────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    sessionId: str
    name: str
    email: str
    password: str


def _send_welcome(email: str, name: str, doc: Dict[str, Any]):
    product = doc.get("productName") or "your product"
    first = escape((name or "").split(" ")[0] or "there")
    login = f"{_public_base()}/creator/login?email={email}"
    text = (f"Hi {first},\n\nYour CoLaunch account is ready. You signed up with {email} for the DIY pass on {product}.\n\n"
            f"Sign in with your email and password: {login}\n\n"
            "Inside you'll run Phase 1 (validate), Phase 2 (build) and Phase 3 (launch) yourself, with the AI advising at every step.\n\n"
            "If you didn't create this account, reply to this email and we'll lock it.\n\nThe CoLaunch team")
    html = (
        '<!DOCTYPE html><div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;padding:28px 24px;color:#18181b;background:#ffffff;">'
        '<p style="font:700 12px sans-serif;color:#c81e3c;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 14px;">CoLaunch · Account created</p>'
        f'<h1 style="font:400 28px Georgia,serif;margin:0 0 16px;color:#09090b;">Welcome, {first}.</h1>'
        f'<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">Your account is ready. You signed up with <b>{escape(email)}</b> for the DIY pass on <b>{escape(product)}</b>.</p>'
        '<p style="font-size:15px;line-height:1.6;margin:0 0 20px;">Inside you run Phase 1 (validate), Phase 2 (build) and Phase 3 (launch) yourself, with the AI advising at every step.</p>'
        f'<a href="{escape(login)}" style="display:inline-block;background:#c81e3c;color:#ffffff;padding:13px 22px;font-weight:700;text-decoration:none;">Sign in to your workspace</a>'
        '<p style="font-size:13px;line-height:1.6;margin:24px 0 0;color:#71717a;">If you didn\'t create this account, reply to this email and we\'ll lock it.</p></div>'
    )
    try:
        from app.integrations.email_provider import EmailProvider
        EmailProvider().send(to_email=email, subject=f"Your CoLaunch account is ready ({product})", body_html=html, body_text=text, from_name="CoLaunch")
        return True
    except Exception as e:
        logger.warning(f"[CreatorAuth] welcome email failed: {e}")
        return False


@router.post("/signup", status_code=201)
def signup(body: SignupRequest):
    from app.services.operator_accounts import validate_new_account, SignupError, _hash_password, PBKDF2_ITERATIONS
    c = _coll("diy_payments")
    pay = c.find_one({"_id": body.sessionId}) if c is not None else None
    if not pay or pay.get("status") != "paid":
        raise HTTPException(402, "Sign-up opens after the DIY pass is paid.")
    if pay.get("account_id"):
        raise HTTPException(409, "An account was already created for this payment. Sign in instead.")
    try:
        name, email = validate_new_account(body.name, body.email, body.password)
    except SignupError as e:
        raise HTTPException(e.status_code, e.detail)
    accs = _accounts()
    pid = pay["project_id"]
    existing = accs.find_one({"email": email})
    if existing:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    salt = secrets.token_bytes(16)
    acc_id = f"cr_{secrets.token_hex(10)}"
    accs.insert_one({"_id": acc_id, "email": email, "name": name, "role": "creator", "project_ids": [pid],
                     "password_hash": _hash_password(body.password, salt), "password_salt": salt.hex(),
                     "password_iterations": PBKDF2_ITERATIONS, "createdAt": _iso(), "active": True,
                     "diy_session": body.sessionId})
    c.update_one({"_id": body.sessionId}, {"$set": {"account_id": acc_id, "claimedAt": _iso()}})
    doc = _project(pid)
    emailed = _send_welcome(email, name, doc)
    logger.info(f"[CreatorAuth] account created for project {pid}")
    return {"ok": True, "email": email, "emailed": emailed}


class LoginRequest(BaseModel):
    email: str
    password: str


def _projects_for(acc: Dict[str, Any]):
    from app.routers.projects import _find_mongo_project
    out = []
    for pid in acc.get("project_ids") or []:
        d = _find_mongo_project(pid)
        if d:
            out.append({"id": _pid(d), "productName": d.get("productName"), "creatorName": d.get("creatorName"), "portalPath": _portal_path(d)})
    return out


@router.post("/login")
def login(body: LoginRequest):
    from app.services.operator_accounts import _hash_password, PBKDF2_ITERATIONS
    email = (body.email or "").strip().lower()
    acc = _accounts().find_one({"email": email, "active": {"$ne": False}}) if email else None
    if not acc:
        _hash_password(body.password or "x", b"\x00" * 16)
        raise HTTPException(401, "Wrong email or password.")
    cand = _hash_password(body.password or "", bytes.fromhex(acc["password_salt"]), int(acc.get("password_iterations") or PBKDF2_ITERATIONS))
    if not hmac.compare_digest(cand, acc.get("password_hash", "")):
        raise HTTPException(401, "Wrong email or password.")
    _accounts().update_one({"_id": acc["_id"]}, {"$set": {"lastLoginAt": _iso()}})
    return {"token": issue_creator_token(acc["_id"]), "expiresIn": CREATOR_TOKEN_TTL,
            "user": {"email": acc["email"], "name": acc.get("name"), "role": "creator"}, "projects": _projects_for(acc)}


@router.get("/me")
def me(user: AuthenticatedUser = Depends(get_current_auth)):
    if user.role != AuthRole.CREATOR or not user.account_id:
        raise HTTPException(401, "Not signed in.")
    acc = _accounts().find_one({"_id": user.account_id})
    if not acc:
        raise HTTPException(401, "Not signed in.")
    return {"user": {"email": acc["email"], "name": acc.get("name"), "role": "creator"}, "projects": _projects_for(acc)}
