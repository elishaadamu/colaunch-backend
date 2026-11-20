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
        for model in ("gpt-image-1", "dall-e-3"):
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
