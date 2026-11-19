# -*- coding: utf-8 -*-
"""
Phase 3 · AI that advises, streamed so the operator watches it work. The key never leaves the server.

  POST /api/launch-ai/{pid}/plan     launch strategist: date, channels, offers, messaging, every creator post,
                                     checklists, FAQs and the welcome email (tagged blocks, parsed at the end)
  POST /api/launch-ai/{pid}/manager  launch manager: reads live signals and recommends actions with drafts;
                                     the operator accepts each one (nothing is created automatically)
  POST /api/launch-ai/{pid}/report   launch analyst: plain-language report from computed numbers
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import require_operator
from app.routers.build_studio import _stream, _sse, _project, _coll

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/launch-ai", tags=["launch-ai"])

TAG_ATTRS = re.compile(r'(\w+)="([^"]*)"')


def _blocks(text: str, tag: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(rf"<{tag}((?:\s+\w+=\"[^\"]*\")*)\s*>(.*?)</{tag}>", text, re.S):
        attrs = dict(TAG_ATTRS.findall(m.group(1)))
        attrs["body"] = m.group(2).strip()
        out.append(attrs)
    return out


def _one(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else ""


def _respond(gen):
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run(messages, max_tokens, finish):
    """Stream deltas, then call finish(full_text) for the parsed 'done' payload."""
    out = []
    try:
        yield _sse("status", {"stage": "thinking"})
        for kind, val in _stream(messages, max_tokens):
            if kind == "model":
                yield _sse("model", {"model": val})
            else:
                out.append(val)
                yield _sse("delta", {"t": val})
        yield _sse("done", finish("".join(out)))
    except Exception as e:
        logger.error(f"[LaunchAI] {e}")
        yield _sse("error", {"message": str(e)[:400]})


# ── Launch strategist ───────────────────────────────────────────────────────────────────
PLAN_SYSTEM = """You are a launch strategist for a creator-led software product. You plan from real evidence only
and you write every creator post in full. You never invent numbers.

Write in this exact tagged format, in this order, nothing else:
<step>one line per piece of analysis you do, as you do it (5-10 steps)</step> (repeat the tag per step)
<summary>2 sentences: the launch in plain words</summary>
<launchDate>YYYY-MM-DD</launchDate>
<channel name="instagram|video|x|newsletter|dm" role="what it does in the launch">why, citing the evidence numbers</channel>
<offer name="..." price="number" deadline="e.g. 72 hours">who it's for and what they get</offer>
<message angle="..." objection="the objection it answers">opening line</message>
<post day="0" time="09:00" channel="instagram" draftKey="storySequence|videoScript|announcementPost|newsletterDraft|directMessageScript" title="short title">complete ready-to-post copy with the tracking link</post>   (5-8 posts, day 0 to day 7)
<task owner="creator|colaunch" when="Day -2">task</task>   (6-12 tasks across both owners)
<faq q="question">answer</faq>   (4-8, real questions from the evidence first)
<welcome subject="email subject" firstStep="the single first action a new customer should take">welcome email body using {{first_name}}, {{product}}, {{link}}, {{first_step}}, {{creator}}</welcome>"""


class PlanRequest(BaseModel):
    evidence: Dict[str, Any]


@router.post("/{pid}/plan")
def launch_plan(pid: str, body: PlanRequest, user=Depends(require_operator)):
    doc = _project(pid)
    ev = body.evidence or {}
    base = (ev.get("publicUrl") or "").rstrip("/")
    msgs = [{"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": f"Product: {doc.get('productName')} by {doc.get('creatorName')}.\n"
                                        f"Tracking link format: {base}/preorder/{ev.get('slug')}?ref=launch_<channel>\n"
                                        f"Launch price: {ev.get('launchPrice') or 'not set yet: propose one from the evidence and say why'}\n\n"
                                        f"EVIDENCE:\n{json.dumps(ev, indent=2, default=str)[:24000]}"}]

    def finish(text):
        plan = {
            "summary": _one(text, "summary"),
            "launchDate": _one(text, "launchDate"),
            "steps": [s["body"] for s in _blocks(text, "step")],
            "channels": [{"channel": c.get("name"), "role": c.get("role"), "why": c["body"]} for c in _blocks(text, "channel")],
            "offers": [{"name": o.get("name"), "price": float(re.sub(r"[^0-9.]", "", o.get("price") or "0") or 0), "deadline": o.get("deadline"), "terms": o["body"]} for o in _blocks(text, "offer")],
            "messaging": [{"angle": m.get("angle"), "objection": m.get("objection"), "hook": m["body"]} for m in _blocks(text, "message")],
            "schedule": [{"day": int(re.sub(r"[^0-9-]", "", p.get("day") or "0") or 0), "time": p.get("time"), "channel": p.get("channel"),
                          "draftKey": p.get("draftKey"), "title": p.get("title"), "content": p["body"]} for p in _blocks(text, "post")],
            "creatorChecklist": [{"id": f"cc-{i}", "title": t["body"], "when": t.get("when"), "done": False} for i, t in enumerate(x for x in _blocks(text, "task") if x.get("owner") == "creator")],
            "opsChecklist": [{"id": f"oc-{i}", "title": t["body"], "when": t.get("when"), "done": False} for i, t in enumerate(x for x in _blocks(text, "task") if x.get("owner") != "creator")],
            "faqs": [{"q": f.get("q"), "a": f["body"]} for f in _blocks(text, "faq")],
            "generatedAt": datetime.utcnow().isoformat(),
        }
        w = _blocks(text, "welcome")
        if w:
            plan["onboarding"] = {"subject": w[0].get("subject"), "firstStep": w[0].get("firstStep"), "body": w[0]["body"]}
        if not plan["schedule"]:
            return {"error": "The plan came back without posts. Try again."}
        return {"plan": plan}

    return _respond(_run(msgs, 16000, finish))


# ── Launch manager (advisor) ────────────────────────────────────────────────────────────
MANAGER_SYSTEM = """You are the launch manager for a live product. You read the live numbers and the detected signals,
explain what is happening, and recommend what to do. You advise; a human decides. Never invent numbers.

Format, in order, nothing else:
<step>one line per thing you check, as you check it (3-8)</step>
<read>3-5 sentences: what the numbers say right now, in plain language</read>
For each signal worth acting on (skip ones that don't need action and say why in <read>):
<rec signal="the signal key" kind="creator|engineering" priority="high|medium|low" title="what to do">why, citing the numbers</rec>
<draft signal="the same key">for creator: the complete post, ready to publish, with the tracking link given. For engineering: a precise task with 2-4 testable acceptance criteria as lines starting with "- "</draft>"""


class ManagerRequest(BaseModel):
    metrics: Dict[str, Any]
    signals: List[Dict[str, Any]]
    links: Optional[Dict[str, str]] = None
    offer: Optional[str] = None
    quotes: Optional[List[str]] = None


@router.post("/{pid}/manager")
def launch_manager(pid: str, body: ManagerRequest, user=Depends(require_operator)):
    doc = _project(pid)
    msgs = [{"role": "system", "content": MANAGER_SYSTEM},
            {"role": "user", "content": f"Product: {doc.get('productName')} by {doc.get('creatorName')}. Offer: {body.offer or 'see metrics'}.\n"
                                        f"Tracking links per channel: {json.dumps(body.links or {})}\n"
                                        f"Customer language: {json.dumps((body.quotes or [])[:6])}\n\n"
                                        f"LIVE METRICS:\n{json.dumps(body.metrics, default=str)[:12000]}\n\n"
                                        f"DETECTED SIGNALS:\n{json.dumps(body.signals, default=str)[:6000]}"}]

    def finish(text):
        drafts = {d.get("signal"): d["body"] for d in _blocks(text, "draft")}
        recs = [{"signal": r.get("signal"), "kind": r.get("kind"), "priority": r.get("priority"), "title": r.get("title"),
                 "why": r["body"], "draft": drafts.get(r.get("signal"), "")} for r in _blocks(text, "rec")]
        return {"read": _one(text, "read"), "steps": [s["body"] for s in _blocks(text, "step")], "recommendations": recs, "at": datetime.utcnow().isoformat()}

    return _respond(_run(msgs, 8000, finish))


# ── Launch analyst (report) ─────────────────────────────────────────────────────────────
REPORT_SYSTEM = """You are a launch analyst. You explain computed launch results plainly and draw specific lessons.
Never invent numbers; use only the ones given.
Format, in order, nothing else:
<step>one line per thing you analyse (3-6)</step>
<summary>3 sentences</summary>
<learning>one specific learning with a number</learning>   (3-6)
<creator>1-2 sentences on how the creator's posts performed</creator>
<usage>1-2 sentences on product usage and activation</usage>
<theme>a recurring feedback theme</theme>   (0-5)
<next>the single most useful next move, and why</next>"""


class ReportRequest(BaseModel):
    report: Dict[str, Any]


@router.post("/{pid}/report")
def launch_report(pid: str, body: ReportRequest, user=Depends(require_operator)):
    doc = _project(pid)
    msgs = [{"role": "system", "content": REPORT_SYSTEM},
            {"role": "user", "content": f"Product: {doc.get('productName')}.\nRESULTS:\n{json.dumps(body.report, default=str)[:16000]}"}]

    def finish(text):
        return {"narrative": {"summary": _one(text, "summary"), "learnings": [b["body"] for b in _blocks(text, "learning")],
                              "creatorPerformance": _one(text, "creator"), "productUsage": _one(text, "usage"),
                              "feedbackThemes": [b["body"] for b in _blocks(text, "theme")], "next": _one(text, "next"),
                              "steps": [s["body"] for s in _blocks(text, "step")]}}

    return _respond(_run(msgs, 6000, finish))
