# -*- coding: utf-8 -*-
"""
Phase 2 · real builds with a coding agent (Claude Code, Cursor, …) over MCP, verified by GitHub CI.

  Operator (Phase 2 UI):  connect a GitHub repo, create an agent key, copy the MCP command
  Coding agent (MCP):     POST /mcp  (JSON-RPC, Streamable HTTP, Authorization: Bearer clk_…)
      get_build_context → get_next_task → write code in the real repo → run real tests →
      report_test_run (repeat until green) → push → submit_commit → check_ci → next task
  Verification:           a task is verified only when GitHub checks for its commit pass
                          (or, with no repo connected, when the agent's real test run passes).

Collections:
  build_agent_keys   {_id: sha256(key), project_id, label, prefix, createdAt, lastUsedAt, revoked}
  build_github       {_id: project_id, repo, branch, token_enc, connectedAt}
  build_task_state   {_id: "<pid>:<taskId>", project_id, task_id, status, runs, lastRun, commit, ci, verified, ...}
  build_tasks_extra  {_id: "<pid>:<taskId>", project_id, task: {...}}    # tasks the agent created
  build_runs         {_id, project_id, task_id, n, command, passed, failed, total, ok, output, commitSha, at}
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.auth import require_operator

logger = logging.getLogger(__name__)
router = APIRouter(tags=["build-agent"])

GH = "https://api.github.com"
DONE = {"Completed", "done", "Done"}


def _coll(name):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception:
        return None


def _now():
    return datetime.utcnow().isoformat()


# ── Secrets ──────────────────────────────────────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    from app.config import settings
    key = hashlib.sha256(("colaunch-build:" + (settings.SECRET_KEY or "dev")).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _enc(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def _dec(s: str) -> str:
    try:
        return _fernet().decrypt(s.encode()).decode()
    except Exception:
        return ""


def _hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


# ── Project + tasks (overlay so page saves can't wipe agent progress) ─────────────────────
def _project(pid: str) -> Optional[Dict[str, Any]]:
    from app.routers.projects import _find_mongo_project
    return _find_mongo_project(pid)


def _pid(doc) -> str:
    return str(doc.get("_id") or doc.get("id"))


def overlay_tasks(pid: str, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    st, ex = _coll("build_task_state"), _coll("build_tasks_extra")
    out = [dict(t) for t in (tasks or []) if isinstance(t, dict)]
    try:
        if ex is not None:
            have = {str(t.get("id")) for t in out}
            for e in ex.find({"project_id": pid}):
                t = e.get("task") or {}
                if str(t.get("id")) not in have:
                    out.append(dict(t))
        if st is not None:
            states = {s["task_id"]: s for s in st.find({"project_id": pid})}
            for t in out:
                s = states.get(str(t.get("id")))
                if not s:
                    continue
                for k in ("status", "runs", "lastRun", "commit", "ci", "agentNote", "blockedReason", "claimedAt"):
                    if s.get(k) is not None:
                        t[k] = s[k]
                if s.get("verified") is not None:
                    t["verified"] = s["verified"]
                    t["verifiedBy"] = s.get("verifiedBy")
                    t["verifiedAt"] = s.get("verifiedAt")
                t["agentManaged"] = True
    except Exception as e:
        logger.debug(f"[Build] overlay: {e}")
    return out


def _tasks(doc) -> List[Dict[str, Any]]:
    return overlay_tasks(_pid(doc), doc.get("engineeringTasks") or (doc.get("mvpBuildPlan") or {}).get("technicalPlan", {}).get("engineeringTasks") or [])


def _set_state(pid: str, task_id: str, **fields):
    st = _coll("build_task_state")
    if st is None:
        return
    fields["updatedAt"] = _now()
    st.update_one({"_id": f"{pid}:{task_id}"}, {"$set": {"project_id": pid, "task_id": str(task_id), **fields}}, upsert=True)


def _task_view(t: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "title", "category", "assignedTo", "status", "estimate", "notes", "acceptanceCriteria", "dependsOn", "milestone",
            "priority", "verified", "verifiedBy", "runs", "lastRun", "commit", "ci", "fromClusterId", "fromLaunchManager", "blockedReason")
    return {k: t.get(k) for k in keys if t.get(k) not in (None, [], "")}


def _is_done(t) -> bool:
    return bool(t.get("verified")) or (t.get("status") in DONE and not t.get("agentManaged"))


# ── GitHub ───────────────────────────────────────────────────────────────────────────────
def github_config(pid: str) -> Dict[str, Any]:
    c = _coll("build_github")
    doc = c.find_one({"_id": pid}) if c is not None else None
    return doc or {}


def _gh_headers(token: str):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "CoLaunch-Build"}


def ci_status(pid: str, sha: str) -> Dict[str, Any]:
    cfg = github_config(pid)
    if not cfg.get("repo") or not cfg.get("token_enc"):
        return {"state": "no_repo", "detail": "No GitHub repo connected"}
    token = _dec(cfg["token_enc"])
    repo = cfg["repo"]
    try:
        with httpx.Client(timeout=15, headers=_gh_headers(token)) as cl:
            commit = cl.get(f"{GH}/repos/{repo}/commits/{sha}")
            if commit.status_code == 404 or commit.status_code == 422:
                return {"state": "not_found", "detail": f"Commit {sha[:7]} isn't on GitHub yet. Push it first."}
            commit.raise_for_status()
            runs = cl.get(f"{GH}/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100}).json().get("check_runs", [])
            combined = cl.get(f"{GH}/repos/{repo}/commits/{sha}/status").json()
    except httpx.HTTPStatusError as e:
        return {"state": "error", "detail": f"GitHub said {e.response.status_code}. Check the token's repo access."}
    except Exception as e:
        return {"state": "error", "detail": f"GitHub unreachable: {e}"}
    checks = [{"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion"), "url": r.get("html_url")} for r in runs]
    statuses = combined.get("statuses") or []
    bad = {"failure", "timed_out", "action_required", "startup_failure"}
    succeeded = {c["name"] for c in checks if c["conclusion"] == "success"}
    # A cancelled run only counts against the commit if that check never succeeded (superseded runs are common)
    failed = [c for c in checks if c["conclusion"] in bad or (c["conclusion"] == "cancelled" and c["name"] not in succeeded)]
    if failed or (combined.get("state") in ("failure", "error") and statuses):
        state = "failure"
    elif not checks and not statuses:
        state = "no_ci"
    elif all(c["status"] == "completed" for c in checks) and (not statuses or combined.get("state") == "success"):
        state = "success"
    else:
        state = "pending"
    msg = (commit.json().get("commit") or {}).get("message", "")
    return {"state": state, "checks": checks[:20], "failing": [c["name"] for c in failed][:10], "statusCount": len(statuses), "sha": sha, "message": msg[:200],
            "url": f"https://github.com/{repo}/commit/{sha}", "checkedAt": _now(),
            "detail": {"success": "All checks passed", "failure": "Checks failed", "pending": "Checks still running",
                       "no_ci": "No CI ran on this commit. Add a GitHub Actions workflow that runs the tests."}[state]}


def verify_task_ci(pid: str, task_id: str, sha: str) -> Dict[str, Any]:
    ci = ci_status(pid, sha)
    verified = ci["state"] == "success"
    fields = {"ci": ci}
    if verified:
        fields.update(verified=True, verifiedBy="github_ci", verifiedAt=_now(), status="Completed")
    elif ci["state"] == "failure":
        fields.update(verified=False, status="Needs fixes")
    _set_state(pid, task_id, **fields)
    return ci


def run_ci_tick():
    st = _coll("build_task_state")
    if st is None:
        return
    for s in st.find({"ci.state": "pending"}):
        sha = (s.get("commit") or {}).get("sha")
        if sha:
            verify_task_ci(s["project_id"], s["task_id"], sha)


async def start_ci_loop(interval_seconds: int = 120):
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(run_ci_tick)
        except Exception as e:
            logger.debug(f"[Build] ci tick: {e}")
        await asyncio.sleep(interval_seconds)


# ── Operator endpoints ───────────────────────────────────────────────────────────────────
class GithubConnect(BaseModel):
    repo: str
    token: Optional[str] = None
    branch: Optional[str] = "main"


@router.put("/api/build/{pid}/github")
def connect_github(pid: str, body: GithubConnect, user=Depends(require_operator)):
    repo = re.sub(r"^https?://github\.com/", "", body.repo.strip()).strip("/").removesuffix(".git")
    if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
        raise HTTPException(400, "Use the form owner/repo")
    c = _coll("build_github")
    prev = github_config(pid)
    token = (body.token or "").strip() or _dec(prev.get("token_enc", ""))
    if not token:
        raise HTTPException(400, "A GitHub token with read access to the repo is required")
    try:
        r = httpx.get(f"{GH}/repos/{repo}", headers=_gh_headers(token), timeout=15)
    except Exception as e:
        raise HTTPException(502, f"GitHub unreachable: {e}")
    if r.status_code != 200:
        raise HTTPException(400, f"GitHub returned {r.status_code} for {repo}. Check the name and the token's access.")
    info = r.json()
    c.update_one({"_id": pid}, {"$set": {"repo": repo, "branch": body.branch or info.get("default_branch") or "main",
                                          "token_enc": _enc(token), "private": info.get("private"), "url": info.get("html_url"),
                                          "connectedAt": _now()}}, upsert=True)
    return {"ok": True, "repo": repo, "url": info.get("html_url"), "defaultBranch": info.get("default_branch")}


@router.delete("/api/build/{pid}/github")
def disconnect_github(pid: str, user=Depends(require_operator)):
    c = _coll("build_github")
    if c is not None:
        c.delete_one({"_id": pid})
    return {"ok": True}


class KeyCreate(BaseModel):
    label: Optional[str] = "Coding agent"


@router.post("/api/build/{pid}/keys")
def create_key(pid: str, body: KeyCreate, user=Depends(require_operator)):
    if not _project(pid):
        raise HTTPException(404, "Project not found")
    key = "clk_" + secrets.token_urlsafe(32)
    _coll("build_agent_keys").insert_one({"_id": _hash_key(key), "project_id": pid, "label": (body.label or "Coding agent")[:60],
                                          "prefix": key[:10], "createdAt": _now(), "lastUsedAt": None, "revoked": False})
    return {"key": key, "prefix": key[:10]}


@router.delete("/api/build/{pid}/keys/{prefix}")
def revoke_key(pid: str, prefix: str, user=Depends(require_operator)):
    _coll("build_agent_keys").update_many({"project_id": pid, "prefix": prefix}, {"$set": {"revoked": True}})
    return {"ok": True}


@router.get("/api/build/{pid}/status")
def build_status(pid: str, user=Depends(require_operator)):
    kc, rc = _coll("build_agent_keys"), _coll("build_runs")
    keys = [{k: v for k, v in d.items() if k != "_id"} for d in kc.find({"project_id": pid, "revoked": False})] if kc is not None else []
    gh = github_config(pid)
    runs = list(rc.find({"project_id": pid}, {"_id": 0, "output": 0}).sort("at", -1).limit(30)) if rc is not None else []
    return {"github": {"repo": gh.get("repo"), "branch": gh.get("branch"), "url": gh.get("url"), "connectedAt": gh.get("connectedAt")} if gh else None,
            "keys": keys, "recentRuns": runs}


@router.post("/api/build/{pid}/github/sync")
def sync_ci(pid: str, user=Depends(require_operator)):
    st = _coll("build_task_state")
    n = 0
    for s in (st.find({"project_id": pid, "commit.sha": {"$exists": True}}) if st is not None else []):
        if not s.get("verified"):
            verify_task_ci(pid, s["task_id"], s["commit"]["sha"])
            n += 1
    return {"ok": True, "rechecked": n}


@router.get("/api/build/{pid}/runs/{task_id}")
def task_runs(pid: str, task_id: str, user=Depends(require_operator)):
    rc = _coll("build_runs")
    return {"runs": list(rc.find({"project_id": pid, "task_id": task_id}, {"_id": 0}).sort("at", -1).limit(20)) if rc is not None else []}


# ── MCP tools ────────────────────────────────────────────────────────────────────────────
INSTRUCTIONS = """You are building a real product for CoLaunch. Work in the user's local Git repository.
Loop until no tasks are left:
1. get_build_context once (spec, architecture, scope, repo, conventions).
2. get_next_task: it returns the highest-priority task whose dependencies are done and marks it in progress.
3. Implement it in the repo so every acceptance criterion is met. Write or update automated tests that prove each criterion.
4. Run the real test command. Call report_test_run with the exact counts and the tail of the output, every run.
   If anything fails, fix and run again. Keep reporting each run.
5. When tests pass: commit, push to GitHub, then call submit_commit with the SHA.
6. Call check_ci until the state is success. If it fails, read the failing checks, fix, push, submit_commit again.
7. If something needs a human (credentials, payments setup, a product decision), call mark_blocked with the reason, then take the next task.
Bugs you discover: create_task. Beta and launch issues: get_feedback. When a deploy preview exists: set_staging.
Never mark work done yourself; CoLaunch verifies it from CI."""


def _tool(name, desc, props=None, required=None):
    return {"name": name, "description": desc, "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("get_build_context", "Product spec, technical plan, MVP scope, GitHub repo and how to work. Call this first."),
    _tool("list_tasks", "All engineering tasks with status, verification and last test run.", {"status": {"type": "string", "description": "Optional filter, e.g. Ready, In Progress, Needs fixes, Completed"}}),
    _tool("get_next_task", "Claim the next buildable task (dependencies done, highest priority) and mark it in progress.", {"assignee": {"type": "string", "enum": ["AI Agent", "any"], "description": "Only AI-suitable tasks (default) or any"}}),
    _tool("get_task", "Full detail for one task, including acceptance criteria and recent test runs.", {"task_id": {"type": "string"}}, ["task_id"]),
    _tool("start_task", "Mark a specific task in progress.", {"task_id": {"type": "string"}}, ["task_id"]),
    _tool("report_test_run", "Report one real test run for a task. Call after every run, passing or failing.", {
        "task_id": {"type": "string"}, "command": {"type": "string"}, "passed": {"type": "integer"}, "failed": {"type": "integer"},
        "total": {"type": "integer"}, "output": {"type": "string", "description": "Last ~150 lines of output"}, "commit_sha": {"type": "string"},
        "duration_ms": {"type": "integer"}}, ["task_id", "command", "passed", "failed", "total"]),
    _tool("submit_commit", "Link the pushed commit (and PR) that implements a task. CoLaunch then verifies it with GitHub CI.", {
        "task_id": {"type": "string"}, "sha": {"type": "string"}, "branch": {"type": "string"}, "pr_url": {"type": "string"}, "message": {"type": "string"}}, ["task_id", "sha"]),
    _tool("check_ci", "Current GitHub CI result for the task's commit (success, failure, pending, no_ci, not_found).", {"task_id": {"type": "string"}}, ["task_id"]),
    _tool("mark_blocked", "Mark a task blocked and say what a human must do.", {"task_id": {"type": "string"}, "reason": {"type": "string"}, "needs_human": {"type": "boolean"}}, ["task_id", "reason"]),
    _tool("create_task", "Add a task you discovered (bug, missing piece).", {
        "title": {"type": "string"}, "category": {"type": "string"}, "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "depends_on": {"type": "array", "items": {"type": "string"}}, "assigned_to": {"type": "string", "enum": ["AI Agent", "Human Engineer"]}, "notes": {"type": "string"}}, ["title", "acceptance_criteria"]),
    _tool("get_feedback", "Open beta issues (grouped feedback with user counts) and recent tester reports to fix."),
    _tool("set_staging", "Publish the staging URL (e.g. a Vercel preview) that beta testers will use.", {
        "url": {"type": "string"}, "version": {"type": "string"}, "commit_sha": {"type": "string"}}, ["url", "version"]),
]


def _find_task(doc, task_id):
    t = next((x for x in _tasks(doc) if str(x.get("id")) == str(task_id)), None)
    if not t:
        raise ValueError(f"No task with id {task_id}")
    return t


def call_tool(pid: str, name: str, args: Dict[str, Any]) -> Any:
    doc = _project(pid)
    if not doc:
        raise ValueError("Project not found")
    tasks = _tasks(doc)
    gh = github_config(pid)

    if name == "get_build_context":
        plan = doc.get("mvpBuildPlan") or {}
        spec, tech, scope = plan.get("productSpec") or {}, plan.get("technicalPlan") or {}, plan.get("scopeBoundaries") or {}
        return {
            "product": {"name": doc.get("productName"), "tagline": doc.get("productTagline"), "creator": doc.get("creatorName")},
            "productSpec": spec, "technicalPlan": {k: v for k, v in tech.items() if k != "engineeringTasks"}, "scope": scope,
            "repo": {"github": gh.get("repo"), "branch": gh.get("branch"), "url": gh.get("url")} if gh.get("repo") else None,
            "progress": {"total": len(tasks), "verified": sum(1 for t in tasks if t.get("verified")), "inProgress": sum(1 for t in tasks if t.get("status") == "In Progress")},
            "rules": INSTRUCTIONS,
        }
    if name == "list_tasks":
        f = (args.get("status") or "").lower()
        return [_task_view(t) for t in tasks if not f or str(t.get("status", "")).lower() == f]
    if name == "get_next_task":
        done = {str(t.get("id")) for t in tasks if _is_done(t)}
        only_ai = (args.get("assignee") or "AI Agent") != "any"
        cands = [t for t in tasks if not _is_done(t) and t.get("status") not in ("Blocked",)
                 and all(str(d) in done for d in (t.get("dependsOn") or []))
                 and (not only_ai or t.get("assignedTo", "AI Agent") == "AI Agent")]
        cands.sort(key=lambda t: (0 if t.get("status") in ("In Progress", "Needs fixes") else 1, t.get("priority") or 99))
        if not cands:
            waiting = [t["title"] for t in tasks if not _is_done(t)]
            return {"task": None, "message": "No buildable task right now." + (f" Waiting on: {', '.join(waiting[:5])}" if waiting else " Everything is verified.")}
        t = cands[0]
        _set_state(pid, t["id"], status="In Progress", claimedAt=_now())
        t["status"] = "In Progress"
        return {"task": _task_view(t), "remaining": len(cands) - 1}
    if name == "get_task":
        t = _find_task(doc, args["task_id"])
        rc = _coll("build_runs")
        runs = list(rc.find({"project_id": pid, "task_id": str(t["id"])}, {"_id": 0}).sort("at", -1).limit(5)) if rc is not None else []
        return {"task": _task_view(t), "recentRuns": runs}
    if name == "start_task":
        t = _find_task(doc, args["task_id"])
        _set_state(pid, t["id"], status="In Progress", claimedAt=_now())
        return {"ok": True}
    if name == "report_test_run":
        t = _find_task(doc, args["task_id"])
        passed, failed, total = int(args.get("passed") or 0), int(args.get("failed") or 0), int(args.get("total") or 0)
        rc = _coll("build_runs")
        n = (rc.count_documents({"project_id": pid, "task_id": str(t["id"])}) if rc is not None else 0) + 1
        ok = failed == 0 and total > 0 and passed > 0
        run = {"_id": uuid.uuid4().hex, "project_id": pid, "task_id": str(t["id"]), "n": n, "command": str(args.get("command"))[:300],
               "passed": passed, "failed": failed, "total": total, "ok": ok, "output": str(args.get("output") or "")[-8000:],
               "commitSha": args.get("commit_sha"), "durationMs": args.get("duration_ms"), "at": _now()}
        if rc is not None:
            rc.insert_one(run)
        fields = {"runs": n, "lastRun": {k: run[k] for k in ("n", "passed", "failed", "total", "ok", "at", "command")}}
        if not ok:
            fields["status"] = "Needs fixes" if n > 1 else "In Progress"
        elif not gh.get("repo"):
            # No repo to verify against: a real passing run is the evidence (labelled as such)
            fields.update(verified=True, verifiedBy="agent_tests", verifiedAt=_now(), status="Completed")
        _set_state(pid, t["id"], **fields)
        return {"run": n, "ok": ok, "next": ("Commit, push and call submit_commit." if ok and gh.get("repo") else "Verified by your test run." if ok else "Fix the failures and run the tests again.")}
    if name == "submit_commit":
        t = _find_task(doc, args["task_id"])
        sha = str(args["sha"]).strip()
        if not re.match(r"^[0-9a-f]{7,40}$", sha):
            raise ValueError("sha must be a git commit hash")
        commit = {"sha": sha, "branch": args.get("branch"), "prUrl": args.get("pr_url"), "message": (args.get("message") or "")[:200], "at": _now()}
        _set_state(pid, t["id"], commit=commit, status="In Review")
        ci = verify_task_ci(pid, t["id"], sha) if gh.get("repo") else {"state": "no_repo", "detail": "Connect GitHub in Phase 2 so CI can verify this commit."}
        return {"ci": ci}
    if name == "check_ci":
        t = _find_task(doc, args["task_id"])
        sha = (t.get("commit") or {}).get("sha")
        if not sha:
            return {"state": "no_commit", "detail": "Call submit_commit first."}
        return verify_task_ci(pid, t["id"], sha)
    if name == "mark_blocked":
        t = _find_task(doc, args["task_id"])
        _set_state(pid, t["id"], status="Blocked", blockedReason=str(args.get("reason"))[:500], agentNote="Needs a human" if args.get("needs_human", True) else None)
        return {"ok": True}
    if name == "create_task":
        tid = f"agent-{uuid.uuid4().hex[:8]}"
        task = {"id": tid, "title": str(args["title"])[:160], "category": args.get("category") or "Bug fix", "assignedTo": args.get("assigned_to") or "AI Agent",
                "status": "Ready", "estimate": "4 Hours", "acceptanceCriteria": args.get("acceptance_criteria") or [], "dependsOn": args.get("depends_on") or [],
                "notes": args.get("notes") or "Created by the coding agent", "createdBy": "agent", "createdAt": _now()}
        _coll("build_tasks_extra").update_one({"_id": f"{pid}:{tid}"}, {"$set": {"project_id": pid, "task": task}}, upsert=True)
        return {"task": _task_view(task)}
    if name == "get_feedback":
        from app.routers.beta_program import beta_overlay
        fb = beta_overlay(pid, doc.get("betaFeedback") or [])
        clusters = [{"issue": c.get("title"), "people": c.get("count"), "severity": c.get("severity"), "status": c.get("status"), "example": c.get("exampleQuote")}
                    for c in (doc.get("feedbackClusters") or []) if c.get("status") != "Resolved"]
        recent = [{"category": f.get("category"), "message": f.get("message"), "at": f.get("createdAt")} for f in fb["betaFeedback"][:15]]
        return {"openIssues": clusters, "recentReports": recent}
    if name == "set_staging":
        url = str(args["url"]).strip()
        if not re.match(r"^https://", url):
            raise ValueError("Staging URL must start with https://")
        _coll("staging_deployments").update_one({"_id": pid}, {"$set": {"project_id": pid, "url": url, "version": str(args.get("version"))[:20],
                                                                         "commitSha": args.get("commit_sha"), "source": "agent", "files": [],
                                                                         "deployedAt": _now()}}, upsert=True)
        return {"ok": True, "betaLink": f"/beta/{doc.get('slug') or ''}"}
    raise ValueError(f"Unknown tool {name}")


# ── MCP endpoint (JSON-RPC 2.0 over Streamable HTTP, JSON responses) ──────────────────────
SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]


def _auth_agent(request: Request) -> str:
    raw = request.headers.get("authorization") or ""
    key = raw[7:].strip() if raw.lower().startswith("bearer ") else (request.headers.get("x-colaunch-key") or "")
    kc = _coll("build_agent_keys")
    rec = kc.find_one({"_id": _hash_key(key)}) if (kc is not None and key.startswith("clk_")) else None
    if not rec or rec.get("revoked"):
        raise HTTPException(401, "Invalid or revoked CoLaunch agent key")
    kc.update_one({"_id": rec["_id"]}, {"$set": {"lastUsedAt": _now()}})
    return rec["project_id"]


def _handle(pid: str, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mid = msg.get("id")
    method = msg.get("method")
    if mid is None:  # notification
        return None
    try:
        if method == "initialize":
            req_v = (msg.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": req_v if req_v in SUPPORTED else SUPPORTED[0], "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "colaunch-build", "version": "1.0.0"}, "instructions": INSTRUCTIONS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                data = call_tool(pid, p.get("name"), p.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(data, default=str, indent=2)}], "isError": False}
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    pid = _auth_agent(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    msgs = body if isinstance(body, list) else [body]
    out = [r for r in (await asyncio.to_thread(lambda: [_handle(pid, m) for m in msgs])) if r is not None]
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(body, list) else out[0])


@router.get("/mcp")
def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})
