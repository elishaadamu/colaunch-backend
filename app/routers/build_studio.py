# -*- coding: utf-8 -*-
"""
Phase 2 · Build Studio (AI Studio-style vibe coding).

The operator chats with a code model that writes a runnable multi-file React + Tailwind frontend,
streamed live. The backend is never coded: a separate advisor suggests how to structure it.

  POST /api/studio/{pid}/generate        stream (SSE) a build/edit turn; files + checkpoint saved at the end
  POST /api/studio/{pid}/backend-advice  stream (SSE) backend structure advice (markdown, no code)
  GET  /api/studio/{pid}                 files, messages, checkpoints (without file bodies), latest advice
  POST /api/studio/{pid}/restore/{cp}    restore a checkpoint

Model: settings.BUILD_STUDIO_MODEL (default gpt-5.5) with fallbacks.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import require_operator, require_project_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio", tags=["build-studio"])

MODELS = [m for m in [os.getenv("BUILD_STUDIO_MODEL", "").strip(), "gpt-5.5", "gpt-5.4", "gpt-4.1"] if m]
MAX_FILES_CHARS = 160_000
MAX_CHECKPOINTS = 30


def _coll(name):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def _now():
    return datetime.utcnow().isoformat()


def _client():
    from openai import OpenAI
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        try:
            from app.config import settings
            key = (settings.OPENAI_API_KEY or "").strip()
        except Exception:
            key = ""
    if not key:
        raise HTTPException(503, "OPENAI_API_KEY isn't set on the server.")
    return OpenAI(api_key=key)


def _project(pid):
    from app.routers.projects import _find_mongo_project
    doc = _find_mongo_project(pid)
    if not doc:
        raise HTTPException(404, "Project not found")
    return doc


def _state(pid) -> Dict[str, Any]:
    c = _coll("studio_projects")
    doc = (c.find_one({"_id": pid}) if c is not None else None) or {}
    doc.setdefault("files", [])
    doc.setdefault("messages", [])
    doc.setdefault("checkpoints", [])
    return doc


def _brief(doc) -> str:
    plan = doc.get("mvpBuildPlan") or {}
    spec = plan.get("productSpec") or {}
    scope = plan.get("scopeBoundaries") or {}
    concept = doc.get("selectedConcept") or {}
    brand = concept.get("landingBrandColor") or doc.get("brandColor") or "#c81e3c"
    parts = {
        "product": doc.get("productName"),
        "tagline": doc.get("productTagline") or concept.get("tagline"),
        "creator": doc.get("creatorName"),
        "brandColor": brand,
        "customer": spec.get("targetCustomer") or concept.get("customer"),
        "problem": spec.get("coreProblem") or concept.get("problem"),
        "valueProposition": spec.get("valueProposition"),
        "features": spec.get("features") or concept.get("keyFeatures"),
        "userFlows": spec.get("userFlows"),
        "screens": spec.get("screens"),
        "inScope": scope.get("includedInMVP"),
        "outOfScope": scope.get("excludedFromMVP"),
        "pricing": (spec.get("payments") or {}).get("model") or doc.get("pricing"),
    }
    return json.dumps({k: v for k, v in parts.items() if v}, indent=2, default=str)


BUILD_SYSTEM = """You are a senior product engineer building the FRONTEND of a real product in a live studio.
The app runs in the browser with no build step: React 18 + Tailwind (CDN). It is previewed live while you write.

OUTPUT FORMAT (strict, nothing outside these tags):
<plan>
One short line per step you will take (4-10 lines).
</plan>
<file path="src/App.jsx">
complete file content
</file>
...more <file> blocks...
<delete path="src/old.jsx"/>   (only to remove a file)
<summary>
2-4 sentences: what you built or changed and what to try next.
</summary>

RULES
- Entry is src/App.jsx with a default export. Organise into src/components/*, src/screens/*, src/data/*, src/lib/*.
- Always output the COMPLETE content of every file you create or change. On edits, output only changed files.
- Imports: relative paths between files; bare imports allowed for react, react-dom/client, lucide-react (icons),
  and when truly useful: recharts, date-fns, clsx, react-router-dom (use HashRouter). Nothing else.
- Styling: Tailwind utility classes only. Use the brand colour as the primary accent via arbitrary values, e.g. bg-[#c81e3c].
- NO backend code and NO network calls. Put realistic, domain-specific sample data in src/data/*.js
  (real-looking names, numbers and dates for THIS product's customers, never lorem ipsum).
  Wrap data access in src/lib/api.js functions that return Promises (simulate latency) and add a
  comment above each: // API: METHOD /path - what the real backend will provide
  Persist user-created data in localStorage so the app feels real.
- Build every screen and user flow in the brief. Include loading, empty and error states. Responsive and accessible.
- Production quality: clear hierarchy, consistent spacing, no placeholder text, no TODOs left in the UI."""


ADVICE_SYSTEM = """You are a principal backend architect advising a team. You DO NOT write backend code.
You recommend how to structure the backend for this product, based on its spec and on what the frontend needs
(every `// API:` comment in the frontend is a contract the backend must satisfy).
Write clear markdown with these sections:
## Recommended stack (and why for this product)
## Data model (each entity: fields with types, relations, indexes)
## API endpoints (table: method, path, purpose, request, response; map each frontend `// API:` to one)
## Auth & roles
## Payments (Stripe products/prices, checkout, webhooks, entitlements)
## Integrations & background jobs
## Environments & secrets (env vars to set, never values)
## Security & data checklist
## Build order (numbered, smallest shippable steps)
Keep it specific to this product. Short schemas are fine; no implementation code."""


FILE_RE = re.compile(r'<file path="([^"]+)">\n?(.*?)</file>', re.S)
DEL_RE = re.compile(r'<delete path="([^"]+)"\s*/>')


def _strip_fences(code: str) -> str:
    code = code.strip("\n")
    m = re.match(r"^```[a-zA-Z0-9]*\n(.*)\n```$", code, re.S)
    return (m.group(1) if m else code) + "\n"


def _apply(files: List[Dict[str, str]], output: str):
    by_path = {f["path"]: f["content"] for f in files}
    changed = []
    for path, body in FILE_RE.findall(output):
        path = path.strip().lstrip("/")
        by_path[path] = _strip_fences(body)
        changed.append(path)
    deleted = [p.strip().lstrip("/") for p in DEL_RE.findall(output)]
    for p in deleted:
        by_path.pop(p, None)
    plan = (re.search(r"<plan>(.*?)</plan>", output, re.S) or [None, ""])[1].strip() if re.search(r"<plan>(.*?)</plan>", output, re.S) else ""
    summary = (re.search(r"<summary>(.*?)</summary>", output, re.S).group(1).strip() if re.search(r"<summary>(.*?)</summary>", output, re.S) else "")
    return [{"path": k, "content": v} for k, v in sorted(by_path.items())], changed, deleted, plan, summary


def _files_context(files):
    out, total = [], 0
    for f in files:
        chunk = f'<file path="{f["path"]}">\n{f["content"]}</file>'
        total += len(chunk)
        if total > MAX_FILES_CHARS:
            out.append(f"<!-- {f['path']} omitted (size) -->")
            continue
        out.append(chunk)
    return "\n".join(out)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _stream(messages, max_tokens):
    """Yield text deltas from the first model that works."""
    client = _client()
    last_err = None
    for model in MODELS:
        try:
            kwargs = {"model": model, "messages": messages, "stream": True, "max_completion_tokens": max_tokens}
            if model.startswith("gpt-5"):
                kwargs["reasoning_effort"] = "medium"
            stream = client.chat.completions.create(**kwargs)
            yield ("model", model)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield ("delta", chunk.choices[0].delta.content)
            return
        except Exception as e:
            last_err = e
            logger.warning(f"[Studio] model {model} failed: {e}")
            continue
    raise RuntimeError(f"No build model available: {last_err}")


class GenerateRequest(BaseModel):
    prompt: str
    previewError: Optional[str] = None


@router.post("/{pid}/generate")
def generate(pid: str, body: GenerateRequest, user=Depends(require_project_owner)):
    doc = _project(pid)
    st = _state(pid)
    files = st["files"]
    history = [m for m in st["messages"] if m.get("role") in ("user", "assistant")][-8:]
    user_turn = body.prompt.strip()
    if body.previewError:
        user_turn += f"\n\nThe live preview shows this error, fix its cause:\n{body.previewError[:2000]}"
    msgs = [{"role": "system", "content": BUILD_SYSTEM},
            {"role": "user", "content": f"PRODUCT BRIEF (from the validated spec):\n{_brief(doc)}"}]
    if files:
        msgs.append({"role": "user", "content": f"CURRENT FILES:\n{_files_context(files)}"})
    for m in history:
        msgs.append({"role": m["role"], "content": m.get("text") if m["role"] == "user" else (m.get("summary") or "Done.")})
    msgs.append({"role": "user", "content": user_turn + ("\n\nOutput only the files you change." if files else "\n\nBuild the first complete version.")})

    def run():
        out = []
        model_used = None
        try:
            yield _sse("status", {"stage": "thinking"})
            for kind, val in _stream(msgs, 32000):
                if kind == "model":
                    model_used = val
                    yield _sse("model", {"model": val})
                else:
                    out.append(val)
                    yield _sse("delta", {"t": val})
            text = "".join(out)
            new_files, changed, deleted, plan, summary = _apply(files, text)
            if not changed and not deleted:
                yield _sse("error", {"message": "The model didn't return any files. Try rephrasing the request."})
                return
            cp = {"id": uuid.uuid4().hex[:10], "prompt": body.prompt[:300], "summary": summary, "changed": changed,
                  "deleted": deleted, "files": new_files, "model": model_used, "at": _now()}
            msg_user = {"role": "user", "text": body.prompt, "at": _now()}
            msg_ai = {"role": "assistant", "plan": plan, "summary": summary, "changed": changed, "deleted": deleted,
                      "checkpoint": cp["id"], "model": model_used, "at": _now()}
            c = _coll("studio_projects")
            if c is not None:
                cps = (st["checkpoints"] + [cp])[-MAX_CHECKPOINTS:]
                c.update_one({"_id": pid}, {"$set": {"files": new_files, "checkpoints": cps,
                                                     "messages": (st["messages"] + [msg_user, msg_ai])[-80:], "updatedAt": _now()}}, upsert=True)
            yield _sse("done", {"files": new_files, "changed": changed, "deleted": deleted, "plan": plan, "summary": summary,
                                "checkpoint": {k: v for k, v in cp.items() if k != "files"}})
        except Exception as e:
            logger.error(f"[Studio] generate failed: {e}")
            yield _sse("error", {"message": str(e)[:400]})

    return StreamingResponse(run(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{pid}/backend-advice")
def backend_advice(pid: str, user=Depends(require_project_owner)):
    doc = _project(pid)
    st = _state(pid)
    api_lines = []
    for f in st["files"]:
        api_lines += [ln.strip() for ln in f["content"].splitlines() if "// API:" in ln]
    msgs = [{"role": "system", "content": ADVICE_SYSTEM},
            {"role": "user", "content": f"PRODUCT BRIEF:\n{_brief(doc)}\n\nTECHNICAL PLAN NOTES:\n{json.dumps((doc.get('mvpBuildPlan') or {}).get('technicalPlan') or {}, default=str)[:8000]}\n\nFRONTEND API CONTRACTS:\n" + ("\n".join(api_lines) or "(frontend not built yet)")}]

    def run():
        out = []
        try:
            yield _sse("status", {"stage": "thinking"})
            for kind, val in _stream(msgs, 16000):
                if kind == "model":
                    yield _sse("model", {"model": val})
                else:
                    out.append(val)
                    yield _sse("delta", {"t": val})
            md = "".join(out).strip()
            c = _coll("studio_projects")
            if c is not None:
                c.update_one({"_id": pid}, {"$set": {"advice": {"markdown": md, "at": _now(), "apiContracts": len(api_lines)}}}, upsert=True)
            yield _sse("done", {"markdown": md})
        except Exception as e:
            yield _sse("error", {"message": str(e)[:400]})

    return StreamingResponse(run(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{pid}")
def studio_state(pid: str, user=Depends(require_project_owner)):
    st = _state(pid)
    return {"files": st["files"], "messages": st["messages"][-60:], "advice": st.get("advice"),
            "checkpoints": [{k: v for k, v in cp.items() if k != "files"} for cp in st["checkpoints"]],
            "models": MODELS}


@router.post("/{pid}/restore/{cp_id}")
def restore(pid: str, cp_id: str, user=Depends(require_project_owner)):
    st = _state(pid)
    cp = next((c for c in st["checkpoints"] if c["id"] == cp_id), None)
    if not cp:
        raise HTTPException(404, "Checkpoint not found")
    c = _coll("studio_projects")
    msg = {"role": "system", "text": f"Restored checkpoint from {cp['at'][:16].replace('T', ' ')}", "at": _now()}
    c.update_one({"_id": pid}, {"$set": {"files": cp["files"], "messages": (st["messages"] + [msg])[-80:], "updatedAt": _now()}})
    return {"files": cp["files"]}
