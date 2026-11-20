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


def _handle(doc: Optional[Dict[str, Any]]) -> str:
    h = str((doc or {}).get("creatorHandle") or (doc or {}).get("creator_handle") or "").lstrip("@").strip().lower()
    return re.sub(r"[^a-z0-9_.-]", "", h)


def _site_path(doc: Optional[Dict[str, Any]], slug: str) -> str:
    """Public address: /<creator handle>/<site slug>, or /site/<slug> when the project has no handle."""
    h = _handle(doc)
    return f"/{h}/{slug}" if h else f"/site/{slug}"


def _public_view(site: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slug": site.get("slug"),
        "url": f"{_base_url()}{_site_path(_project(site['_id']), site['slug'])}" if site.get("slug") else None,
        "path": _site_path(_project(site['_id']), site['slug']) if site.get("slug") else None,
        "rev": site.get("rev", 0),
        "draft": site.get("draft"),
        "updatedAt": site.get("updatedAt"),
        "published": bool(site.get("published")),
        "publishedAt": site.get("publishedAt"),
        "publishedRev": site.get("publishedRev"),
        "notifiedAt": site.get("notifiedAt"),
    }


REVISION_KEEP = 60
AUTOSAVE_EVERY_SECONDS = 10 * 60


def _snapshot(pid: str, draft: Dict[str, Any], rev: int, kind: str, user: Optional[AuthenticatedUser], force: bool = False) -> None:
    """Keep restorable versions: every publish, and an autosave at most every 10 minutes."""
    rc = _coll("site_revisions")
    if rc is None or not isinstance(draft, dict) or not draft.get("sections"):
        return
    try:
        if not force:
            last = rc.find_one({"project_id": pid, "kind": "autosave"}, {"at": 1}, sort=[("at", -1)])
            if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])).total_seconds() < AUTOSAVE_EVERY_SECONDS:
                return
        import uuid
        rc.insert_one({"_id": uuid.uuid4().hex, "project_id": pid, "rev": rev, "kind": kind, "at": _iso(),
                       "by": (user.account_id and "creator") or ("operator" if user else "system") if user else "system",
                       "sections": len(draft.get("sections") or []), "pages": len(draft.get("pages") or []), "draft": draft})
        old = list(rc.find({"project_id": pid}, {"_id": 1}).sort("at", -1).skip(REVISION_KEEP))
        if old:
            rc.delete_many({"_id": {"$in": [o["_id"] for o in old]}})
    except Exception as e:
        logger.warning(f"[Sites] snapshot failed: {e}")


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
def public_site(slug: str, handle: Optional[str] = None):
    c = _coll("sites")
    site = c.find_one({"slug": slug.lower().strip()}) if c is not None else None
    if not site or not site.get("published"):
        raise HTTPException(404, "This page isn't published.")
    doc = _project(site["_id"]) or {}
    if handle is not None and handle.lstrip("@").strip().lower() != _handle(doc):
        raise HTTPException(404, "This page isn't published.")
    if (doc.get("status") == "killed") or doc.get("publicMode") == "closed":
        raise HTTPException(410, "This product is no longer available.")
    return {"slug": site["slug"], "path": _site_path(doc, site["slug"]), "site": site["published"], "publishedAt": site.get("publishedAt"), "project": _project_summary(doc)}


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
            "suggestedSlug": (site or {}).get("slug") or _unique_slug(doc, real),
            "basePath": _site_path(doc, (site or {}).get("slug") or _unique_slug(doc, real))}


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
    _snapshot(real, body.draft, res["rev"], "autosave", user)
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
    if (site.get("draft") or {}).get("sections"):
        _snapshot(real, site["draft"], site.get("rev", 0), "publish", None, force=True)
    url = f"{_base_url()}{_site_path(doc, site['slug'])}"
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


# ── AI: write sections / a whole page, custom code sections, logos ───────────────────────────────

class AISectionBody(BaseModel):
    type: str
    data: Dict[str, Any] = {}
    instruction: Optional[str] = None


class AIPageBody(BaseModel):
    sections: list = []
    instruction: Optional[str] = None


class AICodeBody(BaseModel):
    prompt: str
    html: Optional[str] = None
    css: Optional[str] = None
    theme: Dict[str, Any] = {}


class AILogoBody(BaseModel):
    style: str = "svg"   # svg | image
    brief: Optional[str] = None
    accent: Optional[str] = None


def _keys(request: Request) -> Dict[str, str]:
    h = request.headers
    return {"geminiKey": h.get("X-Gemini-Key") or "", "openaiKey": h.get("X-OpenAI-Key") or "", "anthropicKey": h.get("X-Anthropic-Key") or ""}


def _context(doc: Dict[str, Any]) -> str:
    concepts = doc.get("productConcepts") or doc.get("product_concepts") or []
    c = doc.get("selectedConcept") or doc.get("selected_concept") or next((x for x in concepts if isinstance(x, dict) and x.get("selected")), None) or (concepts[0] if concepts and isinstance(concepts[0], dict) else {}) or {}
    feats = c.get("keyFeatures") or doc.get("keyFeatures") or []
    feats = [f if isinstance(f, str) else (f.get("title") or f.get("name") or "") for f in feats][:8]
    parts = {
        "Product": c.get("name") or doc.get("productName") or doc.get("product_name"),
        "Tagline": c.get("tagline") or doc.get("productTagline"),
        "Problem it solves": c.get("problem") or doc.get("problem"),
        "Customer": c.get("customer") or doc.get("targetAudience"),
        "Pricing": c.get("pricing") or doc.get("pricing"),
        "Key features": ", ".join([f for f in feats if f]),
        "Creator": doc.get("creatorName") or doc.get("creator_name"),
        "Creator niche": doc.get("niche"),
    }
    return "\n".join(f"{k}: {v}" for k, v in parts.items() if v)


ARCHETYPE_LABELS = {"workflow": "Workflow Operating System", "copilot": "AI Intelligence Copilot", "vault": "Member Vault & Private Cohort"}


def _concept(doc: Dict[str, Any]) -> Dict[str, Any]:
    concepts = doc.get("productConcepts") or doc.get("product_concepts") or []
    return (doc.get("selectedConcept") or doc.get("selected_concept")
            or next((x for x in concepts if isinstance(x, dict) and x.get("selected")), None)
            or (concepts[0] if concepts and isinstance(concepts[0], dict) else {}) or {})


def _phase1(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The approved Phase 1 concept, as the website must show it (same as the concept landing page and mockup)."""
    c = _concept(doc)
    feats = []
    for f in (c.get("keyFeatures") or doc.get("keyFeatures") or [])[:6]:
        if isinstance(f, dict):
            t, d = f.get("title") or f.get("name") or "", f.get("description") or f.get("text") or ""
        else:
            t, _, d = str(f).partition(":")
        if t.strip():
            feats.append({"title": t.strip()[:80], "text": d.strip()[:300]})
    color = c.get("landingBrandColor") or c.get("brandColor") or doc.get("brandColor") or "#10B981"
    if not re.match(r"^#[0-9a-fA-F]{6}$", str(color)):
        color = "#10B981"
    idx = c.get("landingIndex")
    arche = c.get("archetype") if c.get("archetype") in ARCHETYPE_LABELS else (["workflow", "copilot", "vault"][idx % 3] if isinstance(idx, int) else "workflow")
    return {
        "name": c.get("name") or doc.get("productName") or doc.get("product_name") or "Product",
        "tagline": (c.get("tagline") or doc.get("productTagline") or "").strip(),
        "problem": (c.get("problem") or doc.get("problem") or "").strip(),
        "customer": (c.get("customer") or doc.get("targetAudience") or "").strip(),
        "pricing": (c.get("pricing") or doc.get("pricing") or "").strip(),
        "features": feats, "color": color, "archetype": arche, "label": ARCHETYPE_LABELS[arche],
        "landingIndex": idx if isinstance(idx, int) else None,
        "creator": doc.get("creatorName") or doc.get("creator_name") or "",
        "avatar": doc.get("creatorAvatar") or (doc.get("creatorProfile") or {}).get("avatar_url") or "",
    }


def _json_from(raw: str):
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        a, b = s.find(opener), s.rfind(closer)
        if a != -1 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except Exception:
                continue
    raise HTTPException(502, "The AI reply couldn't be read. Try again.")


def _llm(prompt: str, system: str, keys: Dict[str, str], max_tokens: int = 2000) -> str:
    from app.services.llm import call_llm
    try:
        out = call_llm(prompt, max_tokens=max_tokens, system_prompt=system, api_keys=keys)
    except Exception as e:
        logger.warning(f"[Sites AI] llm failed: {e}")
        raise HTTPException(502, "The AI is unavailable right now. Try again in a moment.")
    if not out or out.lower().startswith(("error", "all ai providers failed", "no ai provider")):
        raise HTTPException(502, "The AI is unavailable right now. Check the AI keys in settings.")
    return out


WRITER = ("You write website copy for small product launches. Plain, specific, confident; no hype words "
          "(revolutionary, game-changing, unlock, seamless, elevate, supercharge), no emojis, no exclamation marks. "
          "Short sentences. Write for the product's real customer. Reply with JSON only.")


def _clip(v, n=600):
    return v[:n] if isinstance(v, str) else v


@router.post("/{pid}/ai/section")
def ai_section(pid: str, body: AISectionBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    if body.type in ("custom",):
        raise HTTPException(400, "Use the code generator for custom sections.")
    prompt = (f"{_context(doc)}\n\nRewrite the content of one '{body.type}' section of the product's website.\n"
              f"Current content (JSON):\n{json.dumps(body.data, default=str)[:4000]}\n\n"
              + (f"Instruction from the creator: {body.instruction.strip()[:500]}\n\n" if (body.instruction or '').strip() else "")
              + "Return a JSON object with exactly the same keys and shape as the current content (same list item keys, "
                "links stay objects with label and href, keep existing hrefs and image URLs unless asked). Fill empty text fields. "
                "Lists: 3 to 6 items unless the instruction says otherwise.")
    data = _json_from(_llm(prompt, WRITER, _keys(request), 1800))
    if not isinstance(data, dict):
        raise HTTPException(502, "The AI reply couldn't be read. Try again.")
    # keep only known keys, keep URLs the AI wasn't asked to change
    out = {k: data.get(k, v) for k, v in body.data.items()} if body.data else data
    for k in ("image",):
        if k in body.data and body.data.get(k) and not (body.instruction or "").lower().count("image"):
            out[k] = body.data[k]
    return {"data": out}


@router.post("/{pid}/ai/page")
def ai_page(pid: str, body: AIPageBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    secs = [s for s in body.sections if isinstance(s, dict) and s.get("id") and s.get("type") not in ("custom",)][:16]
    if not secs:
        raise HTTPException(400, "Add some sections first.")
    slim = [{"id": s["id"], "type": s["type"], "data": s.get("data") or {}} for s in secs]
    prompt = (f"{_context(doc)}\n\nRewrite all the copy on the product's website so it reads as one clear story.\n"
              f"Sections (JSON):\n{json.dumps(slim, default=str)[:12000]}\n\n"
              + (f"Instruction from the creator: {body.instruction.strip()[:500]}\n\n" if (body.instruction or '').strip() else "")
              + "Return a JSON array of {\"id\", \"data\"} with the same ids. Each data keeps the same keys and shape as before "
                "(links stay {label, href}; keep hrefs and image URLs as they are). Fill empty text fields.")
    arr = _json_from(_llm(prompt, WRITER, _keys(request), 6000))
    if isinstance(arr, dict):
        arr = arr.get("sections") or []
    byid = {s["id"]: s for s in slim}
    out = {}
    for item in arr if isinstance(arr, list) else []:
        if not isinstance(item, dict) or item.get("id") not in byid or not isinstance(item.get("data"), dict):
            continue
        old = byid[item["id"]]["data"]
        new = {k: item["data"].get(k, v) for k, v in old.items()} if old else item["data"]
        if old.get("image"):
            new["image"] = old["image"]
        out[item["id"]] = new
    if not out:
        raise HTTPException(502, "The AI reply couldn't be read. Try again.")
    return {"sections": out}


CODER = ("You build one self-contained section of a marketing web page as plain HTML and CSS. No JavaScript, no <script>, "
         "no external CSS or fonts, no forms that post anywhere. Images only from https URLs the user gives, otherwise use CSS shapes "
         "or gradients. Scope every CSS rule under the class .cx. Responsive (works at 360px wide). Square corners. "
         "Reply with JSON only: {\"html\": \"...\", \"css\": \"...\", \"height\": <estimated px height at desktop>}.")


@router.post("/{pid}/ai/code")
def ai_code(pid: str, body: AICodeBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    if len((body.prompt or "").strip()) < 4:
        raise HTTPException(400, "Describe the section you want.")
    t = body.theme or {}
    prompt = (f"{_context(doc)}\n\nPage theme: accent {t.get('accent') or '#c81e3c'}, {'dark' if t.get('mode') != 'light' else 'light'} background "
              f"(use var(--acc), var(--fg), var(--bg), var(--mut), var(--line), var(--head) and var(--body) for colours and fonts; they are defined).\n\n"
              f"Request: {body.prompt.strip()[:1200]}\n"
              + (f"\nCurrent HTML to change:\n{(body.html or '')[:6000]}\nCurrent CSS:\n{(body.css or '')[:4000]}\n" if body.html else "")
              + "\nWrap everything in <div class=\"cx\">…</div>.")
    data = _json_from(_llm(prompt, CODER, _keys(request), 5000))
    html = str(data.get("html") or "")
    css = str(data.get("css") or "")
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    if not html.strip():
        raise HTTPException(502, "The AI returned an empty section. Try describing it differently.")
    try:
        height = max(120, min(int(data.get("height") or 480), 2400))
    except Exception:
        height = 480
    return {"html": html[:40000], "css": css[:20000], "height": height}


LOGO_SVG = ("You design simple, distinctive logo marks (symbols) as SVG. Each mark: viewBox=\"0 0 64 64\", a compact geometric "
            "symbol only, NO text or letters-as-words (a single initial is fine), centred, filling most of the box. Use the main colour "
            "plus at most one supporting tint of it, so it reads on both light and dark backgrounds. Flat colours only, no gradients, "
            "no filters, no images, no scripts, no external references, no background rectangle. "
            "Reply with JSON only: {\"logos\": [\"<svg …>…</svg>\", …]} with 3 clearly different concepts.")


def _clean_svg(svg: str) -> Optional[str]:
    s = (svg or "").strip()
    if not s.startswith("<svg") or "</svg>" not in s:
        return None
    if re.search(r"<script|<foreignObject|on\w+\s*=|javascript:|xlink:href=\"http|href=\"http|<image", s, flags=re.I):
        return None
    if "xmlns=" not in s[:200]:
        s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    return s[:20000]


def _upload_logo(data: bytes, pid: str, ext: str) -> str:
    from app.integrations.cloudinary_service import upload_media_to_cloudinary
    import secrets as _s
    res = upload_media_to_cloudinary(file_data=data, public_id=f"logo_{pid}_{_s.token_hex(4)}", folder="colaunch_sites", resource_type="image", tags=["site_logo"])
    url = res.get("secure_url") if res.get("success") else None
    if not url or not str(url).startswith("http"):
        raise HTTPException(503, "Couldn't store the logo. Try again shortly.")
    return url


@router.post("/{pid}/ai/logo")
def ai_logo(pid: str, body: AILogoBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    real = _pid(doc)
    name = doc.get("productName") or doc.get("product_name") or "Product"
    accent = body.accent if re.match(r"^#[0-9a-fA-F]{6}$", body.accent or "") else "#c81e3c"
    brief = (body.brief or "").strip()[:400]
    if body.style == "image":
        import base64
        from app.services.campaign_media import _get_openai_client
        try:
            client = _get_openai_client(request.headers.get("X-OpenAI-Key") or None)
        except Exception:
            raise HTTPException(503, "Image logos need an OpenAI key. Use the 'Vector' style instead.")
        prompt = (f"A clean, modern logo mark for a product called \"{name}\". {brief} "
                  f"Flat vector style, single main colour {accent} with one neutral, simple geometric symbol, centred on a plain white background, "
                  "no text, no mockup, no shadows, high contrast, works small as an app icon.")
        urls = []
        for model in IMAGE_MODELS:
            try:
                res = client.images.generate(model=model, prompt=prompt, n=1, size="1024x1024")
                d = res.data[0] if res.data else None
                if d is not None and getattr(d, "b64_json", None):
                    urls.append(_upload_logo(base64.b64decode(d.b64_json), real, "png"))
                elif d is not None and getattr(d, "url", None):
                    import httpx
                    urls.append(_upload_logo(httpx.get(d.url, timeout=30).content, real, "png"))
                if urls:
                    break
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"[Sites AI] logo image {model} failed: {e}")
        if not urls:
            raise HTTPException(502, "The image model couldn't make a logo right now. Try the 'Vector' style.")
        return {"logos": urls}

    prompt = (f"{_context(doc)}\n\nProduct name to show: {name}\nMain colour: {accent}\n"
              + (f"Creator's direction: {brief}\n" if brief else "")
              + "Make 3 logo concepts.")
    data = _json_from(_llm(prompt, LOGO_SVG, _keys(request), 4000))
    svgs = [x for x in (_clean_svg(s) for s in (data.get("logos") if isinstance(data, dict) else data) or []) if x][:3]
    if not svgs:
        raise HTTPException(502, "The AI didn't return usable logos. Try again.")
    return {"logos": [_upload_logo(s.encode("utf-8"), real, "svg") for s in svgs]}


# ── AI: build the whole site (theme, home sections, extra pages, blog) ────────────────────────────

class AISiteBody(BaseModel):
    brief: Optional[str] = None
    pages: list = []          # extra page kinds the creator wants, e.g. ["about", "blog", "contact"]


class AIImageBody(BaseModel):
    prompt: str
    aspect: str = "wide"      # wide | square | tall


SITE_ARCHITECT = (
    "You are a senior web designer and copywriter building a complete, polished marketing website for one product. "
    "Plain, specific, confident copy; no hype words (revolutionary, game-changing, unlock, seamless, elevate, supercharge), "
    "no emojis, no exclamation marks. Never invent customer quotes, reviews, user counts, awards or press logos. "
    "Do not design sign-in, log-in or account screens: this is a marketing site, the product lives elsewhere. "
    "Reply with JSON only.")

_SECTION_SHAPES = """Section types and their data (use these keys exactly):
- nav: {brand, links:[{label, href}], cta:{label, href}}
- hero: {eyebrow, title, subtitle, cta:{label, href}, secondary:{label, href}, layout:"split"|"center"|"cover", imagePrompt}
- features: {title, subtitle, items:[{title, text}]}   (3 to 6 items)
- text: {title, body, imagePrompt}   (body may use blank lines between paragraphs; imagePrompt optional)
- gallery: {title, images:[{caption, imagePrompt}]}   (2 to 4 images)
- steps: {title, subtitle, items:[{title, text}]}   (how it works, 3 to 4 steps)
- stats: {title, items:[{value, label}]}   (only facts from the product brief, e.g. price, time to set up; never invented usage numbers)
- pricing: {title, subtitle, plans:[{name, price, period, features (one per line), cta:{label, href}, highlight}]}
- faq: {title, items:[{q, a}]}   (4 to 6)
- signup: {title, text, button, success}
- cta: {title, text, button:{label, href}}
- posts: {title, subtitle}   (blog index; posts are listed automatically)
- footer: {text, links:[{label, href}]}"""


IMAGE_MODELS = ("gpt-image-2", "chatgpt-image-latest", "gpt-image-1.5", "gpt-image-1-mini", "gpt-image-1")


def _image_for(prompt: str, aspect: str, real: str, key: Optional[str]) -> str:
    import base64
    from app.services.campaign_media import _get_openai_client
    try:
        client = _get_openai_client(key or None)
    except Exception:
        raise HTTPException(503, "Image generation needs an OpenAI key in settings.")
    size = {"wide": "1536x1024", "tall": "1024x1536"}.get(aspect, "1024x1024")
    full = (f"{prompt.strip()[:900]}. Editorial, high-end product website photography or clean 3D illustration, "
            "natural light, no text, no words, no logos, no watermarks, no UI screenshots with text.")
    last = None
    for model, sz in [(m, size) for m in IMAGE_MODELS] + [("gpt-image-1.5", None)]:
        try:
            extra = {"size": sz} if sz else {}
            if model.startswith("gpt-image"):
                extra["quality"] = "medium"
            res = client.images.generate(model=model, prompt=full, n=1, **extra)
            d = res.data[0] if res.data else None
            if d is not None and getattr(d, "b64_json", None):
                data = base64.b64decode(d.b64_json)
            elif d is not None and getattr(d, "url", None):
                import httpx
                data = httpx.get(d.url, timeout=60).content
            else:
                continue
            from app.integrations.cloudinary_service import upload_media_to_cloudinary
            import secrets as _s
            up = upload_media_to_cloudinary(file_data=data, public_id=f"site_{real}_{_s.token_hex(5)}", folder="colaunch_sites", resource_type="image", tags=["site_image"])
            url = up.get("secure_url") if up.get("success") else None
            if url and str(url).startswith("http"):
                return url
            raise HTTPException(503, "Couldn't store the image. Try again shortly.")
        except HTTPException:
            raise
        except Exception as e:
            last = e
            logger.warning(f"[Sites AI] image {model} failed: {e}")
    raise HTTPException(502, "The image model couldn't make that image right now. Try again.")


@router.post("/{pid}/ai/image")
def ai_image(pid: str, body: AIImageBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    doc = _resolve(pid)
    if len((body.prompt or "").strip()) < 4:
        raise HTTPException(400, "Describe the image you want.")
    name = doc.get("productName") or doc.get("product_name") or ""
    url = _image_for(f"For the website of {name}: {body.prompt}", body.aspect, _pid(doc), request.headers.get("X-OpenAI-Key"))
    return {"url": url}


def _sid() -> str:
    import secrets as _s
    return _s.token_hex(4)


_ALLOWED = {"dashboard", "nav", "hero", "features", "text", "gallery", "steps", "stats", "pricing", "faq", "signup", "cta", "posts", "footer"}


def _link(v, fix) -> Dict[str, str]:
    if not isinstance(v, dict):
        return {"label": "", "href": ""}
    return {"label": str(v.get("label") or "")[:60], "href": fix(str(v.get("href") or ""))}


@router.post("/{pid}/ai/site")
def ai_site(pid: str, body: AISiteBody, request: Request, user: AuthenticatedUser = Depends(require_project_owner)):
    """Designs a full site: theme picked for the product, a rich home page, extra pages and a blog with posts.
    Every link is checked so menus and buttons go somewhere real (a section, a page, or the pre-order page)."""
    doc = _resolve(pid)
    slug = doc.get("slug") or _pid(doc)
    buy = f"/preorder/{slug}"
    wanted = [p for p in (body.pages or []) if p in ("dashboard", "about", "blog", "contact", "faq", "pricing", "terms", "privacy")] or ["dashboard", "about", "blog", "contact", "terms", "privacy"]
    p1 = _phase1(doc)
    look = (f"dark interface aesthetic: deep near-black background (#08090c) with soft {p1['color']} accent light, calm, premium, "
            "modern software product for real people")
    ctx = (f"{_context(doc)}\n\n"
           "This website must match the product approved in Phase 1 exactly: same name, headline (the tagline), problem, "
           f"customer, features and prices. Brand colour {p1['color']} on a dark theme. Never rename the product or its features, "
           "never change prices. Image prompts describe scenes in this style: " + look + ".\n\n"
           + (f"What the creator wants: {body.brief.strip()[:1200]}\n\n" if (body.brief or "").strip() else "")
           + f"{_SECTION_SHAPES}\n\n"
           "Rules for links (href): a home section is \"#<anchor>\"; another page is \"/<page path>\"; buying or pre-ordering is "
           f"\"{buy}\"; joining the list is \"#signup\". Use nothing else. "
           f"The site's pages are: home, {', '.join(wanted)}.\n"
           "Never put text inside images.\n\n")
    home_prompt = ctx + (
        "Design the HOME page and the look of the site.\n"
        "The hero shows the Phase 1 product mockup, so it needs no imagePrompt.\n"
        "The home page must have, in this order: nav; hero; a text section about the problem (with imagePrompt); "
        "features (exactly the Phase 1 features, same titles, each text 2 sentences); steps (3 or 4); a text section showing the product in use (with imagePrompt); "
        "gallery (3 images with imagePrompt and caption); pricing; faq (5 or 6); "
        "signup; cta; footer. Pricing uses only the price(s) in the brief above: one plan unless the brief lists more; never invent "
        "tiers or prices. Step titles have no numbers (the page numbers them). Every section has real, specific copy (no placeholders). Section text fields are 1 to 3 sentences, "
        "text section bodies 2 short paragraphs.\n"
        "Return JSON: {\"theme\": {\"accent\", \"mode\", \"font\"}, \"settings\": {\"title\" (<= 60 chars), \"description\" (<= 155 chars)}, "
        "\"home\": [{\"type\", \"anchor\", \"data\"}]}. Nav links point to 3 or 4 key home sections (#anchors); the nav cta goes to the pre-order page.")
    pages_prompt = ctx + (
        "Write the OTHER pages of the site (not the home page).\n"
        f"Pages: {', '.join(w for w in wanted if w != 'dashboard')}. Each page has 2 to 4 sections from the list (no nav or footer) with real, specific copy:\n"
        "- about: a text section with the story (3 paragraphs, imagePrompt), features as 'what we believe' (3 items), cta.\n"
        "- contact: text with how to reach the team and response time, faq (3 support questions), signup.\n"
        "- faq: faq with 8 questions.\n- pricing: pricing and faq.\n"
        "- terms / privacy: one text section with a plain-language draft (6 to 8 short headed paragraphs), ending with a note to have it checked.\n"
        "- blog: a posts section (title and subtitle) and a signup section.\n"
        "Menu: about, blog, contact, faq, pricing go in the header; terms and privacy in the footer.\n"
        + "Return JSON: {\"pages\": [{\"kind\", \"title\", \"path\", \"menu\", \"sections\": [{\"type\", \"data\"}]}]}")
    posts_prompt = (f"{_context(doc)}\n\nWrite 3 blog posts for this product's website that are genuinely useful to its customers: practical "
                    "advice about the problem the product solves. Mention the product naturally at most once per post. Each post: title, "
                    "excerpt (1 sentence), body (5 to 7 paragraphs separated by blank lines, no headings, no markdown), imagePrompt (one vivid "
                    "sentence for a photo that fits, no text in the image).\nReturn JSON: {\"posts\": [{\"title\", \"excerpt\", \"body\", \"imagePrompt\"}]}")
    from concurrent.futures import ThreadPoolExecutor
    keys = _keys(request)
    dash_prompt = (f"{_context(doc)}\n\nDesign the sample data for an interactive preview of this product's app dashboard, in the product's own "
                   "terms (what a real user of THIS product would see). Return JSON: {\"title\": \"Inside <product>\", \"subtitle\" (1 sentence), "
                   "\"kpis\": [4 x {\"label\" (<= 24 chars), \"value\" (e.g. 1,284 or 68%), \"delta\" (e.g. +12% or -4%; one may be negative)}], "
                   "\"chartTitle\", \"donutTitle\", \"segments\": [3 x {\"label\", \"value\" (percent, they add to 100)}], "
                   "\"assistant\": {\"title\", \"question\" (a realistic user question), \"answer\" (3 practical sentences)}, "
                   "\"listTitle\", \"items\": [5 x {\"title\" (<= 30 chars), \"status\": \"Active\" or \"Draft\"}]}")

    def soft(f):
        try:
            r = f.result()
            return r if isinstance(r, dict) else {}
        except HTTPException:
            return {}
    with ThreadPoolExecutor(3) as ex:
        fh = ex.submit(lambda: _json_from(_llm(home_prompt, SITE_ARCHITECT, keys, 9000)))
        fp = ex.submit(lambda: _json_from(_llm(pages_prompt, SITE_ARCHITECT, keys, 9000)))
        fb = ex.submit(lambda: _json_from(_llm(posts_prompt, SITE_ARCHITECT, keys, 8000))) if "blog" in wanted else None
        fd = ex.submit(lambda: _json_from(_llm(dash_prompt, SITE_ARCHITECT, keys, 3000))) if "dashboard" in wanted else None
        raw = fh.result()
        extra = soft(fp)
        extra["posts"] = soft(fb).get("posts") or [] if fb else []
        dash = soft(fd) if fd else None
    if not isinstance(raw, dict) or not isinstance(raw.get("home"), list):
        raise HTTPException(502, "The AI reply couldn't be read. Try again.")
    if isinstance(extra, dict):
        raw["pages"] = extra.get("pages") or []
        raw["posts"] = extra.get("posts") or []

    # Pages first, so links can be checked against real paths.
    pages, paths = [], set()
    for pg in (raw.get("pages") or [])[:8]:
        if not isinstance(pg, dict):
            continue
        if _slugify(pg.get("kind") or pg.get("path") or "") not in wanted and _slugify(pg.get("path") or "") not in wanted:
            continue
        path = _slugify(pg.get("path") or pg.get("title") or pg.get("kind") or "page")[:40] or "page"
        while path in paths:
            path += "-2"
        paths.add(path)
        pages.append({"_raw": pg, "path": path})
    if "blog" in wanted and not any(pg["_raw"].get("kind") == "blog" or pg["path"] == "blog" for pg in pages):
        paths.add("blog")
        pages.append({"_raw": {"kind": "blog", "title": "Blog", "menu": "header", "sections": [
            {"type": "posts", "data": {"title": "Blog", "subtitle": "Practical notes for getting more out of your day."}},
            {"type": "signup", "data": {"title": "Get new posts by email", "text": "One email when a new post is out.", "button": "Subscribe", "success": "You're subscribed."}}]}, "path": "blog"})
    posts_raw = [p for p in (raw.get("posts") or []) if isinstance(p, dict) and p.get("title")][:6] if any(
        (pg["_raw"].get("kind") == "blog" or pg["path"] == "blog") for pg in pages) else []
    post_paths = []
    for p in posts_raw:
        path = ("blog-" + _slugify(p["title"]))[:48]
        while path in paths:
            path += "-2"
        paths.add(path)
        post_paths.append(path)

    home_raw = [s for s in raw["home"] if isinstance(s, dict) and s.get("type") in _ALLOWED][:14]
    anchors = set()
    for s in home_raw:
        a = _slugify(str(s.get("anchor") or s.get("type")))[:30] or s["type"]
        while a in anchors:
            a += "-2"
        anchors.add(a)
        s["_anchor"] = a
    has_signup = any(s["type"] == "signup" for s in home_raw)

    def fix(h: str) -> str:
        h = (h or "").strip()
        if h.startswith("#"):
            a = _slugify(h[1:])
            return f"#{a}" if a in anchors else ("#signup" if a == "signup" and has_signup else buy)
        if h.startswith("/preorder") or h == buy:
            return buy
        if re.match(r"^https?://", h) or h.startswith("mailto:"):
            return h[:300]
        p = _slugify(h.lstrip("/").split("#")[0].split("?")[0])
        if p in paths:
            return f"/{p}"
        if p in anchors:
            return f"#{p}"
        return buy

    def txt(v, n=1200):
        return str(v or "")[:n] if not isinstance(v, (dict, list)) else ""

    def clean(sec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        t = sec.get("type")
        d = sec.get("data") if isinstance(sec.get("data"), dict) else {}
        L = lambda k: [x for x in (d.get(k) or []) if isinstance(x, dict)]
        if t == "nav":
            data = {"brand": txt(d.get("brand"), 60), "links": [_link(x, fix) for x in L("links")][:6], "cta": _link(d.get("cta"), fix)}
        elif t == "hero":
            lay = d.get("layout") if d.get("layout") in ("split", "center", "cover") else "split"
            data = {"eyebrow": txt(d.get("eyebrow"), 80), "title": txt(d.get("title"), 160), "subtitle": txt(d.get("subtitle"), 400),
                    "cta": _link(d.get("cta"), fix), "secondary": _link(d.get("secondary"), fix), "layout": lay, "image": "", "imagePrompt": txt(d.get("imagePrompt"), 400)}
        elif t in ("features", "steps"):
            data = {"title": txt(d.get("title"), 120), "subtitle": txt(d.get("subtitle"), 400), "items": [{"title": txt(x.get("title"), 80), "text": txt(x.get("text"), 400)} for x in L("items")][:6]}
        elif t == "stats":
            data = {"title": txt(d.get("title"), 120), "items": [{"value": txt(x.get("value"), 20), "label": txt(x.get("label"), 80)} for x in L("items")][:4]}
        elif t == "text":
            data = {"title": txt(d.get("title"), 120), "body": txt(d.get("body"), 4000), "image": "", "imagePrompt": txt(d.get("imagePrompt"), 400)}
        elif t == "gallery":
            data = {"title": txt(d.get("title"), 120), "images": [{"url": "", "caption": txt(x.get("caption"), 120), "imagePrompt": txt(x.get("imagePrompt"), 400)} for x in L("images")][:4]}
        elif t == "pricing":
            data = {"title": txt(d.get("title"), 120), "subtitle": txt(d.get("subtitle"), 400), "plans": [{
                "name": txt(x.get("name"), 60), "price": txt(x.get("price"), 20), "period": txt(x.get("period"), 30),
                "features": txt(x.get("features") if isinstance(x.get("features"), str) else "\n".join(map(str, x.get("features") or [])), 800),
                "cta": {"label": _link(x.get("cta"), fix)["label"] or "Pre-order", "href": buy}, "highlight": bool(x.get("highlight"))} for x in L("plans")][:3]}
        elif t == "faq":
            data = {"title": txt(d.get("title"), 120), "items": [{"q": txt(x.get("q"), 200), "a": txt(x.get("a"), 800)} for x in L("items")][:8]}
        elif t == "signup":
            data = {"title": txt(d.get("title"), 120), "text": txt(d.get("text"), 300), "button": txt(d.get("button"), 30) or "Join", "success": txt(d.get("success"), 160) or "Thanks, you're in."}
        elif t == "cta":
            data = {"title": txt(d.get("title"), 120), "text": txt(d.get("text"), 300), "button": _link(d.get("button"), fix)}
        elif t == "posts":
            data = {"title": txt(d.get("title"), 120) or "Blog", "subtitle": txt(d.get("subtitle"), 300)}
        elif t == "dashboard":
            return None
        elif t == "footer":
            data = {"text": txt(d.get("text"), 200), "links": [_link(x, fix) for x in L("links")][:6]}
        else:
            return None
        return data

    real_prices = set(re.findall(r"\$\s?\d[\d,.]*", _context(doc)))
    norm = lambda p: re.sub(r"[\s,]", "", p).rstrip(".")
    real_n = {norm(p) for p in real_prices}

    def tidy(t: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if t == "steps":
            for it in data["items"]:
                it["title"] = re.sub(r"^\s*(step\s*)?\d+\s*[.):-]\s*", "", it["title"], flags=re.I)
        if t == "stats":
            return None  # facts strips invite made-up numbers
        if t == "pricing" and real_n:
            ok = [pl for pl in data["plans"] if any(norm(x) in real_n for x in re.findall(r"\$\s?\d[\d,.]*", pl["price"]))]
            if not ok and data["plans"]:
                first = data["plans"][0]
                first["price"] = sorted(real_prices, key=len)[0].replace(" ", "")
                ok = [first]
            data["plans"] = ok
            if len(ok) == 1:
                ok[0]["highlight"] = True
        return data

    home = []
    for s in home_raw:
        data = clean(s)
        data = tidy(s["type"], data) if data is not None else None
        if data is None:
            continue
        home.append({"id": _sid(), "type": s["type"], "hidden": False, "anchor": s["_anchor"], "data": data})
    if not any(s["type"] == "hero" for s in home):
        raise HTTPException(502, "The AI didn't design a usable home page. Try again.")
    name = doc.get("productName") or doc.get("product_name") or "Product"
    if not home or home[0]["type"] != "nav":
        home.insert(0, {"id": _sid(), "type": "nav", "hidden": False, "anchor": "nav", "data": {"brand": name, "links": [], "cta": {"label": "Pre-order", "href": buy}}})
    if home[-1]["type"] != "footer":
        home.append({"id": _sid(), "type": "footer", "hidden": False, "anchor": "footer", "data": {"text": f"© {datetime.utcnow().year} {name}", "links": []}})
    # Header/footer page links are added by the renderer from page.menu, so drop duplicates the AI put in by hand.
    for s in home:
        if s["type"] in ("nav", "footer"):
            s["data"]["links"] = [l for l in s["data"]["links"] if l["label"] and l["href"].lstrip("/") not in paths]
            s["data"]["brand" if s["type"] == "nav" else "text"] = s["data"].get("brand" if s["type"] == "nav" else "text") or name

    out_pages = []
    for pg in pages:
        r = pg["_raw"]
        secs = []
        for s in (r.get("sections") or [])[:6]:
            if isinstance(s, dict) and s.get("type") in _ALLOWED - {"nav", "footer"}:
                data = clean(s)
                data = tidy(s["type"], data) if data is not None else None
                if data is not None:
                    secs.append({"id": _sid(), "type": s["type"], "hidden": False, "data": data})
        is_blog = r.get("kind") == "blog" or pg["path"] == "blog"
        if is_blog and not any(s["type"] == "posts" for s in secs):
            secs.insert(0, {"id": _sid(), "type": "posts", "hidden": False, "data": {"title": "Blog", "subtitle": ""}})
        if not secs:
            continue
        menu = r.get("menu") if r.get("menu") in ("header", "footer") else ("footer" if pg["path"] in ("terms", "privacy") else "header")
        out_pages.append({"id": _sid(), "title": txt(r.get("title"), 40) or pg["path"].title(), "path": pg["path"], "menu": menu,
                          "kind": "blog" if is_blog else (r.get("kind") or "page"), "sections": secs})
    if dash is not None:
        def lst(k, keys, n):
            return [{kk: txt(x.get(kk), 300) for kk in keys} for x in (dash.get(k) or []) if isinstance(x, dict)][:n]
        a = dash.get("assistant") if isinstance(dash.get("assistant"), dict) else {}
        ddata = {"title": txt(dash.get("title"), 80) or f"Inside {name}", "subtitle": txt(dash.get("subtitle"), 240),
                 "nav": [f["title"] for f in p1["features"]][:5],
                 "kpis": lst("kpis", ("label", "value", "delta"), 4), "chartTitle": txt(dash.get("chartTitle"), 60) or "Last 30 days",
                 "donutTitle": txt(dash.get("donutTitle"), 60) or "Distribution", "segments": lst("segments", ("label", "value"), 4),
                 "assistant": {"title": txt(a.get("title"), 60) or "AI assistant", "question": txt(a.get("question"), 200), "answer": txt(a.get("answer"), 800)},
                 "listTitle": txt(dash.get("listTitle"), 60), "items": lst("items", ("title", "status"), 6),
                 "caption": "Interactive preview with sample data. Your own data appears once you sign up."}
        if len(ddata["kpis"]) >= 2:
            path = "dashboard"
            out_pages.insert(0, {"id": _sid(), "title": "Dashboard", "path": path, "menu": "header", "kind": "dashboard",
                                 "sections": [{"id": _sid(), "type": "dashboard", "hidden": False, "data": ddata}]})
            paths.add(path)
            for sec in home:
                if sec["type"] == "hero":
                    sec["data"]["secondary"] = {"label": "Try the app", "href": "/dashboard"}
    # Menu labels stay short so the header never wraps.
    for pg in out_pages:
        if pg["menu"] in ("header", "footer") and len(pg["title"]) > 14:
            pg["title"] = {"about": "About", "blog": "Blog", "contact": "Contact", "faq": "FAQ", "pricing": "Pricing", "terms": "Terms", "privacy": "Privacy"}.get(pg["path"], pg["title"][:14])
    for p, path in zip(posts_raw, post_paths):
        out_pages.append({"id": _sid(), "title": txt(p.get("title"), 120), "path": path, "menu": "none", "kind": "post",
                          "excerpt": txt(p.get("excerpt"), 300), "date": _iso()[:10], "image": "", "imagePrompt": txt(p.get("imagePrompt"), 400),
                          "sections": [{"id": _sid(), "type": "article", "hidden": False, "data": {"title": txt(p.get("title"), 160), "body": txt(p.get("body"), 8000), "image": "", "imagePrompt": txt(p.get("imagePrompt"), 400)}}]})

    # Phase 1 is the source of truth: hero, features and look come from the approved concept.
    for sec in home:
        d = sec["data"]
        if sec["type"] == "hero":
            d.update({"eyebrow": p1["label"], "title": p1["tagline"] or d["title"], "subtitle": p1["problem"] or d["subtitle"],
                      "cta": {"label": "Get early access", "href": buy}, "secondary": {"label": "", "href": ""},
                      "note": p1["pricing"], "byline": f"Made with {p1['creator']}" if p1["creator"] else "", "avatar": p1["avatar"],
                      "layout": "mockup", "image": "", "imagePrompt": "",
                      "mockup": {"name": p1["name"], "tagline": p1["tagline"], "archetype": p1["archetype"], "landingIndex": p1["landingIndex"],
                                 "keyFeatures": [f["title"] for f in p1["features"]]}})
        elif sec["type"] == "features" and p1["features"]:
            ai = {i["title"].lower(): i["text"] for i in d.get("items") or []}
            d["items"] = [{"title": f["title"], "text": ai.get(f["title"].lower()) or f["text"]} for f in p1["features"]]
        elif sec["type"] == "nav":
            d["brand"] = p1["name"]
            if d.get("cta", {}).get("label"):
                d["cta"] = {"label": "Get early access", "href": buy}
    th = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {"accent": p1["color"], "mode": "dark", "font": "modern"}
    st = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    site = {"version": 1, "template": "ai", "theme": theme,
            "settings": {"title": txt(st.get("title"), 70) or name, "description": txt(st.get("description"), 160), "logoUrl": "", "faviconUrl": ""},
            "sections": home, "pages": out_pages}
    return {"site": site}


# ── Revision history ──────────────────────────────────────────────────────────────────────────────

@router.get("/{pid}/revisions")
def list_revisions(pid: str, user: AuthenticatedUser = Depends(require_project_owner)):
    real = _pid(_resolve(pid))
    rc = _coll("site_revisions")
    items = list(rc.find({"project_id": real}, {"draft": 0}).sort("at", -1).limit(REVISION_KEEP)) if rc is not None else []
    return {"revisions": [{"id": r["_id"], "rev": r.get("rev"), "kind": r.get("kind"), "at": r.get("at"), "by": r.get("by"),
                           "sections": r.get("sections"), "pages": r.get("pages")} for r in items]}


@router.get("/{pid}/revisions/{rid}")
def get_revision(pid: str, rid: str, user: AuthenticatedUser = Depends(require_project_owner)):
    real = _pid(_resolve(pid))
    rc = _coll("site_revisions")
    r = rc.find_one({"_id": rid, "project_id": real}) if rc is not None else None
    if not r:
        raise HTTPException(404, "That version no longer exists.")
    return {"id": r["_id"], "at": r.get("at"), "kind": r.get("kind"), "draft": r.get("draft")}
