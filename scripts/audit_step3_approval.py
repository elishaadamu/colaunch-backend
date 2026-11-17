#!/usr/bin/env python3
"""
Audit script: List projects where Step 3 is marked done but the creator never approved it.
STRICT AUDIT ONLY: Does NOT modify any database records.
"""

import os
import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

def run_audit():
    print("=" * 70)
    print("Step 3 (Campaign) Approval Audit")
    print("Checking projects where Step 3 is marked done without creator approval...")
    print("=" * 70)

    projects = []

    # 1. Try MongoDB Atlas
    try:
        from app.mongodb import get_mongo_client
        client = get_mongo_client()
        db = client.get_default_database() or client["creator_forge"]
        col = db["co_launch_projects"]
        projects = list(col.find({}))
        print(f"Connected to MongoDB Atlas: retrieved {len(projects)} projects.")
    except Exception as e:
        print(f"MongoDB connection notice: {e}")
        # Try local json fallback if exists
        fallback_json = backend_dir / "data" / "co_launch_projects.json"
        if fallback_json.exists():
            import json
            with open(fallback_json, "r") as f:
                projects = json.load(f)
            print(f"Loaded {len(projects)} projects from local JSON fallback.")

    unapproved_done_projects = []
    total_projects = len(projects)

    for p in projects:
        p_id = p.get("id") or str(p.get("_id", "unknown"))
        name = p.get("productName") or p.get("name") or "Unnamed Project"
        creator = p.get("creatorHandle") or p.get("creatorName") or "Unknown Creator"

        is_step3_marked = bool(
            p.get("campaignApproved") is True or
            p.get("phase1Step3Done") is True or
            p.get("step3Done") is True
        )

        has_approval_timestamp = bool(
            p.get("campaignApprovedAt") or
            p.get("campaign_approved_at") or
            p.get("phase1Step3ApprovedAt") or
            p.get("step3ApprovedAt")
        )

        gate_decisions = p.get("gateDecisions") or p.get("gate_decisions") or []
        has_gate_decision = bool(
            isinstance(gate_decisions, list) and len(gate_decisions) > 0 and
            any(
                isinstance(d, dict) and d.get("decision") in ("pass_to_phase2", "pass", "greenlight")
                for d in gate_decisions
            )
        )

        phase = int(p.get("currentPhase") or p.get("current_phase") or 1)

        # Flag if Step 3 marked done but no approval timestamp, no gate decision, and project is still in Phase 1
        if is_step3_marked and not has_approval_timestamp and not has_gate_decision and phase == 1:
            unapproved_done_projects.append({
                "id": p_id,
                "name": name,
                "creator": creator,
                "phase": phase,
                "campaignApproved": p.get("campaignApproved"),
                "phase1Step3Done": p.get("phase1Step3Done"),
                "step3Done": p.get("step3Done"),
                "hasKit": bool(p.get("campaignKit")),
            })

    print("-" * 70)
    print(f"Total projects examined: {total_projects}")
    print(f"Projects with Step 3 marked done WITHOUT creator approval: {len(unapproved_done_projects)}")
    print("-" * 70)

    if unapproved_done_projects:
        print("\nDetails of affected projects:")
        for idx, item in enumerate(unapproved_done_projects, 1):
            print(f" {idx}. [{item['id']}] {item['name']} (@{item['creator']})")
            print(f"    campaignApproved={item['campaignApproved']}, phase1Step3Done={item['phase1Step3Done']}, step3Done={item['step3Done']}, hasKit={item['hasKit']}")
    else:
        print("\nNo unapproved Step 3 projects found. Database is consistent.")

    print("\n[NOTE] Read-only audit complete. No database records were modified.")
    return len(unapproved_done_projects)

if __name__ == "__main__":
    run_audit()
