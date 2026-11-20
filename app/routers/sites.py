# -*- coding: utf-8 -*-
"""
Product websites built in the CoLaunch site builder.

  GET    /api/sites/public/{slug}            the published page (public)
  POST   /api/sites/public/{slug}/signup     email signup from a published page (public)
  GET    /api/sites/{pid}                    draft + publish state (operator or the project's creator)
  PUT    /api/sites/{pid}                    autosave the draft (optimistic: baseRev must match)
  POST   /api/sites/{pid}/publish            publish the draft at /site/<slug>
  POST   /api/sites/{pid}/unpublish          take the page offline

Collection `sites` (one per project, _id = project id). The page content is a JSON document of
sections the frontend renders; the server stores, versions and publishes it. Launching in Phase 3
publishes the site and emails the creator its link (see publish_on_launch).
"""

import json
import logging
import re
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import AuthenticatedUser, require_project_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sites", tags=["sites"])

MAX_DRAFT_BYTES = 600_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coll(name: str):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def _project(pid: str) -> Optional[Dict[str, Any]]:
    from app.routers.projects import _find_mongo_project
    return _find_mongo_project(pid)


def _pid(doc) -> str:
    return str(doc.get("id") or doc.get("_id"))


def _slugify(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(raw or "").lower()).strip("-")[:60] or "product"


def _unique_slug(doc: Dict[str, Any], pid: str) -> str:
    base = _slugify(doc.get("slug") or doc.get("productName") or doc.get("product_name") or doc.get("title"))
    c = _coll("sites")
    slug, n = base, 2
    while c is not None and c.find_one({"slug": slug, "_id": {"$ne": pid}}, {"_id": 1}):
        slug, n = f"{base}-{n}", n + 1
    return slug


def _base_url() -> str:
    try:
        from app.config import settings
        return (getattr(settings, "PUBLIC_APP_URL", "") or "https://colaunch-2.vercel.app").rstrip("/")
    except Exception:
        return "https://colaunch-2.vercel.app"


def _project_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _pid(doc),
        "productName": doc.get("productName") or doc.get("product_name") or doc.get("title") or "",
        "creatorName": doc.get("creatorName") or doc.get("creator_name") or "",
        "creatorHandle": str(doc.get("creatorHandle") or doc.get("creator_handle") or "").lstrip("@"),
        "slug": doc.get("slug") or "",
        "status": doc.get("status") or "",
        "currentPhase": doc.get("currentPhase") or doc.get("current_phase"),
    }


def _public_view(site: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slug": site.get("slug"),
        "url": f"{_base_url()}/site/{site.get('slug')}" if site.get("slug") else None,
        "rev": site.get("rev", 0),
        "draft": site.get("draft"),
        "updatedAt": site.get("updatedAt"),
        "published": bool(site.get("published")),
        "publishedAt": site.get("publishedAt"),
        "publishedRev": site.get("publishedRev"),
        "notifiedAt": site.get("notifiedAt"),
    }


def _get_site(pid: str) -> Optional[Dict[str, Any]]:
    c = _coll("sites")
    return c.find_one({"_id": pid}) if c is not None else None


class SaveBody(BaseModel):
    draft: Dict[str, Any]
    baseRev: int = 0


class PublishBody(BaseModel):
    notify: bool = False


class SignupBody(BaseModel):
    email: str
    name: Optional[str] = None


# ── Public ────────────────────────────────────────────────────────────────────────────────

@router.get("/public/{slug}")
def public_site(slug: str):
    c = _coll("sites")
    site = c.find_one({"slug": slug.lower().strip()}) if c is not None else None
    if not site or not site.get("published"):
        raise HTTPException(404, "This page isn't published.")
    doc = _project(site["_id"]) or {}
    if (doc.get("status") == "killed") or doc.get("publicMode") == "closed":
        raise HTTPException(410, "This product is no longer available.")
    return {"slug": site["slug"], "site": site["published"], "publishedAt": site.get("publishedAt"), "project": _project_summary(doc)}


@router.post("/public/{slug}/signup", status_code=201)
def public_signup(slug: str, body: SignupBody, request: Request):
    email = (body.email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        raise HTTPException(400, "Enter a valid email address.")
    c = _coll("sites")
    site = c.find_one({"slug": slug.lower().strip()}, {"_id": 1, "published": 1}) if c is not None else None
    if not site or not site.get("published"):
        raise HTTPException(404, "This page isn't published.")
    sc = _coll("site_signups")
    if sc is None:
        raise HTTPException(503, "Signups are unavailable right now. Try again shortly.")
    sc.update_one(
        {"project_id": site["_id"], "email": email},
        {"$setOnInsert": {"project_id": site["_id"], "slug": slug, "email": email, "name": (body.name or "").strip()[:120], "at": _iso(),
                          "ip": request.client.host if request.client else None}},
        upsert=True,
    )
    return {"ok": True}


# ── Owner (operator or the project's creator) ─────────────────────────────────────────────────

def _resolve(pid: str) -> Dict[str, Any]:
    doc = _project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    return doc


@router.get("/{pid}")
def get_site(pid: str, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    real = _pid(doc)
    site = _get_site(real)
    sc = _coll("site_signups")
    signups = sc.count_documents({"project_id": real}) if sc is not None else 0
    return {"project": _project_summary(doc), "site": _public_view(site) if site else None, "signups": signups,
            "suggestedSlug": (site or {}).get("slug") or _unique_slug(doc, real)}


@router.put("/{pid}")
def save_site(pid: str, body: SaveBody, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    real = _pid(doc)
    raw = json.dumps(body.draft, default=str)
    if len(raw) > MAX_DRAFT_BYTES:
        raise HTTPException(413, "The page is too large to save. Use image links instead of pasting images.")
    c = _coll("sites")
    if c is None:
        raise HTTPException(503, "Storage is unavailable right now. Your changes stay in this tab; we'll retry.")
    site = c.find_one({"_id": real}, {"rev": 1, "slug": 1})
    rev = int((site or {}).get("rev") or 0)
    if site and body.baseRev != rev:
        current = c.find_one({"_id": real})
        raise HTTPException(409, detail={"message": "This page was changed in another tab.", "site": _public_view(current)})
    now = _iso()
    update = {"draft": body.draft, "rev": rev + 1, "updatedAt": now, "updatedBy": user.account_id or user.identifier, "project_id": real}
    if not site:
        update["slug"] = _unique_slug(doc, real)
        update["createdAt"] = now
    res = c.find_one_and_update({"_id": real, "rev": rev} if site else {"_id": real}, {"$set": update}, upsert=not site, return_document=True)
    if res is None:
        raise HTTPException(409, detail={"message": "This page was changed in another tab.", "site": _public_view(c.find_one({"_id": real}))})
    return {"ok": True, "rev": res["rev"], "updatedAt": now, "slug": res.get("slug")}


def _recipient(doc: Dict[str, Any], pid: str):
    acc = _coll("creator_accounts")
    a = acc.find_one({"project_ids": pid}, {"email": 1, "name": 1}) if acc is not None else None
    if a and a.get("email"):
        return a["email"], a.get("name") or ""
    email = doc.get("creatorEmail") or doc.get("creator_email")
    return (email, doc.get("creatorName") or doc.get("creator_name") or "") if email else (None, "")


def _send_site_email(doc: Dict[str, Any], pid: str, url: str) -> bool:
    to, name = _recipient(doc, pid)
    if not to:
        logger.info(f"[Sites] no creator email for {pid}; skipping launch email")
        return False
    product = doc.get("productName") or doc.get("product_name") or "your product"
    first = escape((name or "").split(" ")[0] or "there")
    text = (f"Hi {first},\n\n{product} is live. Your product page is published at:\n{url}\n\n"
            "Share it anywhere. You can keep editing it in the site builder; changes go live when you publish again.\n\nThe CoLaunch team")
    html = (
        '<!DOCTYPE html><div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;padding:28px 24px;color:#18181b;background:#ffffff;">'
        '<p style="font:700 12px sans-serif;color:#c81e3c;letter-spacing:1.5px;text-transform:uppercase;margin:0 0 14px;">CoLaunch · You\'re live</p>'
        f'<h1 style="font:400 28px Georgia,serif;margin:0 0 16px;color:#09090b;">{escape(product)} is live, {first}.</h1>'
        '<p style="font-size:15px;line-height:1.6;margin:0 0 18px;">Your product page is published. Share this link anywhere:</p>'
        f'<p style="font:600 15px monospace;margin:0 0 20px;word-break:break-all;"><a href="{escape(url)}" style="color:#c81e3c;">{escape(url)}</a></p>'
        f'<a href="{escape(url)}" style="display:inline-block;background:#c81e3c;color:#ffffff;padding:13px 22px;font-weight:700;text-decoration:none;">Open your page</a>'
        '<p style="font-size:13px;line-height:1.6;margin:24px 0 0;color:#71717a;">You can keep editing it in the site builder. Changes go live when you publish again.</p></div>'
    )
    try:
        from app.integrations.email_provider import EmailProvider
        EmailProvider().send(to_email=to, subject=f"{product} is live: your page is published", body_html=html, body_text=text, from_name="CoLaunch")
        return True
    except Exception as e:
        logger.warning(f"[Sites] launch email failed for {pid}: {e}")
        return False


def _publish(doc: Dict[str, Any], notify: bool) -> Dict[str, Any]:
    real = _pid(doc)
    c = _coll("sites")
    if c is None:
        raise HTTPException(503, "Storage is unavailable right now. Try again shortly.")
    site = c.find_one({"_id": real})
    now = _iso()
    if not site:
        # Nothing built yet: publish an automatic page made from the project (the renderer fills it in).
        site = {"_id": real, "project_id": real, "slug": _unique_slug(doc, real), "rev": 0, "draft": {"auto": True}, "createdAt": now}
        c.insert_one(site)
    c.update_one({"_id": real}, {"$set": {"published": site.get("draft") or {"auto": True}, "publishedAt": now, "publishedRev": site.get("rev", 0)}})
    url = f"{_base_url()}/site/{site['slug']}"
    emailed = False
    if notify:
        emailed = _send_site_email(doc, real, url)
        if emailed:
            c.update_one({"_id": real}, {"$set": {"notifiedAt": now}})
    return {"ok": True, "url": url, "slug": site["slug"], "publishedAt": now, "emailed": emailed}


@router.post("/{pid}/publish")
def publish_site(pid: str, body: PublishBody = PublishBody(), user: AuthenticatedUser = Depends(require_project_owner)):
    return _publish(_resolve(pid), body.notify)


@router.post("/{pid}/unpublish")
def unpublish_site(pid: str, user: AuthenticatedUser = Depends(require_project_owner)):
    real = _pid(_resolve(pid))
    c = _coll("sites")
    if c is not None:
        c.update_one({"_id": real}, {"$set": {"published": None, "publishedAt": None}})
    return {"ok": True}


def publish_on_launch(doc: Dict[str, Any], decision: str) -> None:
    """Called from the Phase 3 decision. Launching publishes the page and emails the link once; kill takes it down."""
    try:
        real = _pid(doc)
        if decision == "kill":
            c = _coll("sites")
            if c is not None:
                c.update_one({"_id": real}, {"$set": {"published": None, "publishedAt": None}})
            return
        site = _get_site(real) or {}
        _publish(doc, notify=not site.get("notifiedAt"))
    except Exception as e:
        logger.warning(f"[Sites] publish on launch failed: {e}")
