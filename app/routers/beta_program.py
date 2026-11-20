# -*- coding: utf-8 -*-
"""
Phase 2 · Step 3 beta program (public, tester-facing).

Testers arrive at /beta/<slug>?token=beta_xxxx from their invite email. These endpoints let them
identify themselves, record usage and send feedback without operator credentials. Feedback and
usage live in their own collections so no project save can wipe them:

  beta_feedback  {_id, project_id, token, verified, author, email, category, rating, message, createdAt}
  beta_events    {_id, project_id, token, type: "open", at}
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/beta", tags=["beta"])

CATEGORIES = ["Bug", "Feature request", "Objection", "Support question", "Onboarding / UX", "Praise"]


def _coll(name: str):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def email_token(email: str) -> str:
    """FNV-1a 32-bit of the lower-cased email. Mirrored in the frontend (utils/betaTokens.js)."""
    h = 0x811C9DC5
    for ch in (email or "").strip().lower().encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"beta_e{h:08x}"


def reservation_token(res_id: str) -> str:
    return "beta_" + re.sub(r"[^a-zA-Z0-9]", "", str(res_id or ""))[-6:]


def find_project_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    coll = _coll("co_launch_projects")
    if coll is None or not slug:
        return None
    clean = slug.lower().strip()
    doc = coll.find_one({"$or": [{"slug": clean}, {"id": slug}, {"_id": slug}, {"productSlug": clean}]})
    if doc:
        return doc
    pattern = "^" + re.escape(clean).replace("\\-", "[^a-z0-9]+") + "$"
    return coll.find_one({"$or": [
        {"productName": {"$regex": pattern, "$options": "i"}},
        {"product_name": {"$regex": pattern, "$options": "i"}},
    ]})


def tester_directory(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """token -> {name, email, source}. Pre-order backers, cohort entries, waitlist and survey supporters."""
    out: Dict[str, Dict[str, Any]] = {}

    def add(token, name, email, source):
        if token and token not in out:
            out[token] = {"name": name or "Beta tester", "email": email or "", "source": source}

    meta = doc.get("metadataInfo") or doc.get("metadata_info") or {}
    for r in doc.get("reservations") or meta.get("reservations") or []:
        if not isinstance(r, dict):
            continue
        add(r.get("token"), r.get("name"), r.get("email"), "presale")
        add(reservation_token(r.get("id")), r.get("name"), r.get("email"), "presale")
        if r.get("email"):
            add(email_token(r["email"]), r.get("name"), r.get("email"), "presale")
    for b in doc.get("betaCohort") or meta.get("betaCohort") or []:
        if isinstance(b, dict):
            add(b.get("token"), b.get("name"), b.get("email"), b.get("source") or "invited")
            if b.get("email"):
                add(email_token(b["email"]), b.get("name"), b.get("email"), b.get("source") or "invited")
    for w in doc.get("waitlist") or meta.get("waitlist") or []:
        email = w.get("email") if isinstance(w, dict) else (w if isinstance(w, str) else "")
        if email:
            add(email_token(email), (w.get("name") if isinstance(w, dict) else "") or email.split("@")[0], email, "waitlist")
    for s in doc.get("surveyResponses") or meta.get("survey_responses") or []:
        if isinstance(s, dict) and s.get("email") and (s.get("support") or {}).get("decision") == "yes":
            add(email_token(s["email"]), s.get("name"), s.get("email"), "survey")
    return out


def _mask(email: str) -> str:
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    return f"{local[:2]}{'•' * max(1, len(local) - 2)}@{domain}"


def _public_feedback(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: item.get(k) for k in ("id", "category", "rating", "message", "createdAt")} | {"author": (item.get("author") or "Tester").split(" ")[0]}


# ── Overlay used by the operator project response ─────────────────────────────────────────
def beta_overlay(project_id: str, embedded_feedback: List[Dict[str, Any]]) -> Dict[str, Any]:
    fb_coll = _coll("beta_feedback")
    ev_coll = _coll("beta_events")
    stored: List[Dict[str, Any]] = []
    try:
        if fb_coll is not None:
            stored = list(fb_coll.find({"project_id": project_id}))
    except Exception:
        stored = []
    seen = set()
    merged = []
    for f in stored + [x for x in (embedded_feedback or []) if isinstance(x, dict)]:
        fid = str(f.get("id") or f.get("_id") or "")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        item = {k: v for k, v in f.items() if k not in ("_id", "project_id")}
        item["id"] = fid
        item.setdefault("type", item.get("category"))
        item.setdefault("timestamp", item.get("createdAt") or item.get("date"))
        merged.append(item)
    merged.sort(key=lambda x: str(x.get("createdAt") or x.get("date") or ""), reverse=True)

    usage: Dict[str, Any] = {"opens": 0, "testersActive": 0, "lastOpenAt": None, "perTester": {}}
    try:
        if ev_coll is not None:
            for e in ev_coll.find({"project_id": project_id}):
                tok = e.get("token") or "anonymous"
                pt = usage["perTester"].setdefault(tok, {"opens": 0, "lastSeen": None, "feedback": 0})
                pt["opens"] += 1
                if not pt["lastSeen"] or str(e.get("at")) > str(pt["lastSeen"]):
                    pt["lastSeen"] = e.get("at")
                usage["opens"] += 1
                if not usage["lastOpenAt"] or str(e.get("at")) > str(usage["lastOpenAt"]):
                    usage["lastOpenAt"] = e.get("at")
    except Exception:
        pass
    for f in merged:
        tok = f.get("token")
        if tok:
            usage["perTester"].setdefault(tok, {"opens": 0, "lastSeen": None, "feedback": 0})["feedback"] += 1
    usage["testersActive"] = len([t for t in usage["perTester"] if t != "anonymous"])
    usage["feedbackCount"] = len(merged)
    return {"betaFeedback": merged, "betaUsage": usage}


# ── Public endpoints ─────────────────────────────────────────────────────────────────────
@router.get("/session")
def beta_session(slug: str, token: Optional[str] = None):
    doc = find_project_by_slug(slug)
    if not doc:
        raise HTTPException(404, "Beta not found")
    pid = str(doc.get("_id") or doc.get("id"))
    tester = tester_directory(doc).get(token or "") if token else None
    mine = []
    fb_coll = _coll("beta_feedback")
    if tester and fb_coll is not None:
        mine = [_public_feedback(f) for f in fb_coll.find({"project_id": pid, "token": token}).sort("createdAt", -1).limit(20)]
    from app.routers.projects import _filter_public_project_dict
    return {
        "project": _filter_public_project_dict(dict(doc)),
        "projectId": pid,
        "verified": bool(tester),
        "tester": {"name": tester["name"], "email": _mask(tester["email"]), "source": tester["source"]} if tester else None,
        "categories": CATEGORIES,
        "myFeedback": mine,
        "mvpVersion": doc.get("mvpVersion") or (doc.get("metadataInfo") or {}).get("mvpVersion"),
        # Testers run the staged build only (never the operator's working files)
        "staging": staging_for(pid, with_files=bool(tester)),
    }


class BetaEventRequest(BaseModel):
    slug: str
    token: Optional[str] = None
    type: str = "open"


@router.post("/event")
def beta_event(body: BetaEventRequest):
    doc = find_project_by_slug(body.slug)
    if not doc:
        raise HTTPException(404, "Beta not found")
    ev = _coll("beta_events")
    if ev is not None and body.type in ("open",):
        ev.insert_one({"_id": uuid.uuid4().hex, "project_id": str(doc.get("_id") or doc.get("id")),
                       "token": (body.token or "")[:40] or None, "type": body.type, "at": datetime.utcnow().isoformat()})
    return {"ok": True}


class BetaFeedbackRequest(BaseModel):
    slug: str
    token: Optional[str] = None
    author: Optional[str] = None
    email: Optional[str] = None
    category: str = "Bug"
    rating: Optional[int] = None
    message: str


_fb_rate: Dict[str, list] = {}


@router.post("/feedback")
def beta_feedback(body: BetaFeedbackRequest, request: Request):
    # Public endpoint: at most 10 reports a minute per address so it can't be flooded
    import time as _t
    ip = request.client.host if request.client else "?"
    now = _t.time()
    hits = [t for t in _fb_rate.get(ip, []) if now - t < 60]
    if len(hits) >= 10:
        raise HTTPException(429, "You're sending reports very quickly. Wait a minute and try again.")
    _fb_rate[ip] = hits + [now]
    msg = (body.message or "").strip()
    if len(msg) < 3:
        raise HTTPException(400, "Write a little more so the team can act on it.")
    doc = find_project_by_slug(body.slug)
    if not doc:
        raise HTTPException(404, "Beta not found")
    pid = str(doc.get("_id") or doc.get("id"))
    tester = tester_directory(doc).get(body.token or "") if body.token else None
    category = body.category if body.category in CATEGORIES else "Bug"
    now = datetime.utcnow()
    item = {
        "_id": f"fb-{uuid.uuid4().hex[:12]}",
        "project_id": pid,
        "token": (body.token or None) if tester else None,
        "verified": bool(tester),
        "author": (tester or {}).get("name") or (body.author or "").strip()[:60] or "Beta tester",
        "email": (tester or {}).get("email") or (body.email or "").strip()[:120],
        "source": (tester or {}).get("source") or "unverified",
        "category": category,
        "rating": max(1, min(5, int(body.rating))) if body.rating else None,
        "message": msg[:4000],
        "createdAt": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
    }
    fb = _coll("beta_feedback")
    if fb is None:
        raise HTTPException(503, "Feedback storage unavailable")
    fb.insert_one(item)
    item["id"] = item.pop("_id")
    return {"ok": True, "feedback": _public_feedback(item), "verified": item["verified"]}


# ── Staging deployment (operator) ────────────────────────────────────────────────────────
from fastapi import Depends  # noqa: E402
from app.auth import require_operator, require_project_owner  # noqa: E402

MAX_STAGING_BYTES = 600_000


class StagingDeployRequest(BaseModel):
    version: str
    entry: Optional[str] = None
    files: List[Dict[str, Any]]
    notes: Optional[str] = None
    runtime: Optional[str] = None  # 'studio' = multi-file React app from the Build Studio


def _pick_entry(files: List[Dict[str, Any]], preferred: Optional[str]) -> Optional[str]:
    names = [f.get("name") for f in files if f.get("name")]
    if preferred and preferred in names:
        return preferred
    for cand in ("index.html", "App.jsx", "app.jsx", "main.jsx"):
        for n in names:
            if n.split("/")[-1] == cand:
                return n
    return next((n for n in names if n.endswith((".html", ".jsx"))), names[0] if names else None)


@router.post("/staging/{project_id}")
def deploy_staging(project_id: str, body: StagingDeployRequest, user=Depends(require_project_owner)):
    files = []
    total = 0
    for f in body.files or []:
        name = str(f.get("name") or f.get("path") or "").strip()
        content = f.get("content")
        if not name or not isinstance(content, str) or re.search(r"\.(png|jpe?g|gif|webp|ico)$", name, re.I):
            continue
        total += len(content.encode("utf-8"))
        if total > MAX_STAGING_BYTES:
            raise HTTPException(413, "Staging build is larger than 600 KB of source. Remove unused files and retry.")
        files.append({"name": name, "content": content, "language": f.get("language") or ""})
    if not files:
        raise HTTPException(400, "No code files to deploy. Generate code in the Build step first.")
    entry = _pick_entry(files, body.entry)
    record = {
        "project_id": project_id,
        "version": (body.version or "v0.1.0")[:20],
        "entry": entry,
        "files": files,
        "notes": (body.notes or "")[:500],
        "runtime": body.runtime if body.runtime in ("studio",) else "legacy",
        "deployedAt": datetime.utcnow().isoformat(),
        "fileCount": len(files),
        "bytes": total,
    }
    st = _coll("staging_deployments")
    if st is None:
        raise HTTPException(503, "Storage unavailable")
    st.update_one({"_id": project_id}, {"$set": record}, upsert=True)
    hist = _coll("staging_history")
    if hist is not None:
        hist.insert_one({"_id": uuid.uuid4().hex, **{k: v for k, v in record.items() if k != "files"}})
    return {"ok": True, "staging": {k: v for k, v in record.items() if k != "files"}}


def staging_for(project_id: str, with_files: bool = False) -> Optional[Dict[str, Any]]:
    st = _coll("staging_deployments")
    if st is None:
        return None
    doc = st.find_one({"_id": project_id}, None if with_files else {"files": 0})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


@router.delete("/feedback/{project_id}/{feedback_id}")
def delete_beta_feedback(project_id: str, feedback_id: str, user=Depends(require_project_owner)):
    fb = _coll("beta_feedback")
    if fb is not None:
        fb.delete_one({"_id": feedback_id, "project_id": project_id})
    return {"ok": True}
