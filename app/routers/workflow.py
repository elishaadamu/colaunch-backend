import copy
import json
import logging
from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Depends
from app.auth import require_operator
from pydantic import BaseModel

from app.mongodb import get_collection

import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflow-state", tags=["workflow-state"])

_workflow_cache: Optional[dict] = None
_workflow_cache_time: float = 0.0
_CACHE_TTL: float = 2.0  # Ultra-short cache so MongoDB reads are always fresh on page refresh


class WorkflowStateUpdate(BaseModel):
    active_section: Optional[str] = None
    active_step: Optional[int] = None
    selected_creator_id: Optional[str] = None
    active_project_id: Optional[str] = None
    pitch_sent_map: Optional[dict[str, Any]] = None
    ai_choice_map: Optional[dict[str, Any]] = None
    answer_sent_map: Optional[dict[str, Any]] = None
    persuasion_sent_map: Optional[dict[str, Any]] = None
    creator_stage_map: Optional[dict[str, Any]] = None
    extra_state: Optional[dict[str, Any]] = None
    default_pass_price: Optional[float] = None
    cobuilder_pass_price: Optional[float] = None
    replace: Optional[bool] = False


def _safe_dict(val: Any) -> dict:
    if isinstance(val, dict):
        return dict(val)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _get_mongo_workflow_state(force_refresh: bool = False) -> dict:
    global _workflow_cache, _workflow_cache_time
    now = time.time()
    if not force_refresh and _workflow_cache is not None and (now - _workflow_cache_time) < _CACHE_TTL:
        return copy.deepcopy(_workflow_cache)

    coll = None
    doc = None
    try:
        coll = get_collection("workflow_states")
        if coll is not None:
            doc = coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]})
    except Exception as err:
        logger.warning(f"[WorkflowState] MongoDB fetch warning: {err}")
        if _workflow_cache is not None:
            return copy.deepcopy(_workflow_cache)

    if not doc:
        default_doc = {
            "_id": "default",
            "id": "default",
            "active_section": "section1",
            "active_step": 1,
            "selected_creator_id": None,
            "active_project_id": None,
            "pitch_sent_map": {},
            "ai_choice_map": {},
            "answer_sent_map": {},
            "persuasion_sent_map": {},
            "creator_stage_map": {},
            "extra_state": {},
            "default_pass_price": 0.0,
            "cobuilder_pass_price": 0.0,
            "updated_at": datetime.utcnow().isoformat(),
        }
        try:
            if coll is not None:
                coll.replace_one({"_id": "default"}, default_doc, upsert=True)
        except Exception:
            pass
        doc = default_doc

    res = copy.deepcopy(doc)
    res.pop("_id", None)
    for m_k in ["pitch_sent_map", "ai_choice_map", "answer_sent_map", "persuasion_sent_map", "creator_stage_map"]:
        val = res.get(m_k)
        if isinstance(val, dict) and "0" in val and "1" in val:
            res[m_k] = {}
        elif not isinstance(val, dict):
            res[m_k] = {}
    extra = _safe_dict(res.get("extra_state"))
    res["extra_state"] = extra

    # Resolve pass price: if not in DB, return 0.0
    fee = None
    if res.get("default_pass_price") is not None:
        fee = res["default_pass_price"]
    elif res.get("cobuilder_pass_price") is not None:
        fee = res["cobuilder_pass_price"]
    elif extra.get("default_pass_price") is not None:
        fee = extra["default_pass_price"]
    elif extra.get("cobuilder_pass_price") is not None:
        fee = extra["cobuilder_pass_price"]

    if fee is not None:
        res["default_pass_price"] = float(fee)
        res["cobuilder_pass_price"] = float(fee)
    else:
        res["default_pass_price"] = 0.0
        res["cobuilder_pass_price"] = 0.0

    _workflow_cache = copy.deepcopy(res)
    _workflow_cache_time = now
    return res


@router.get("")
def get_workflow_state():
    """Retrieve global workflow state instantly with sub-millisecond cached reads."""
    try:
        return _get_mongo_workflow_state()
    except Exception as e:
        logger.error(f"[MongoDB] get_workflow_state error: {e}")
        if _workflow_cache is not None:
            return copy.deepcopy(_workflow_cache)
        raise HTTPException(500, f"MongoDB error: {e}")


@router.delete("")
def reset_workflow_state(_op=Depends(require_operator)):
    """Reset global workflow state back to pristine empty baseline in MongoDB Atlas, preserving any user-configured pass fee."""
    try:
        coll = get_collection("workflow_states")
        cur_wf = coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) or {} if coll is not None else {}
        saved_fee = cur_wf.get("default_pass_price") or cur_wf.get("cobuilder_pass_price") or cur_wf.get("extra_state", {}).get("default_pass_price") or cur_wf.get("extra_state", {}).get("cobuilder_pass_price") or 0.0
        clean_doc = {
            "_id": "default",
            "id": "default",
            "active_section": "section1",
            "active_step": 1,
            "selected_creator_id": None,
            "active_project_id": None,
            "pitch_sent_map": {},
            "ai_choice_map": {},
            "answer_sent_map": {},
            "persuasion_sent_map": {},
            "creator_stage_map": {},
            "extra_state": {"default_pass_price": saved_fee, "cobuilder_pass_price": saved_fee} if saved_fee > 0 else {},
            "default_pass_price": saved_fee,
            "cobuilder_pass_price": saved_fee,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if coll is not None:
            coll.replace_one({"_id": "default"}, clean_doc, upsert=True)
        res = copy.deepcopy(clean_doc)
        res.pop("_id", None)
        global _workflow_cache, _workflow_cache_time
        _workflow_cache = copy.deepcopy(res)
        _workflow_cache_time = time.time()
        return res
    except Exception as e:
        logger.error(f"[MongoDB] reset_workflow_state error: {e}")
        raise HTTPException(500, f"MongoDB error: {e}")


@router.post("")
@router.patch("")
@router.put("")
def update_workflow_state(body: WorkflowStateUpdate):
    """Update and synchronize global workflow state directly and exclusively in MongoDB Atlas."""
    try:
        coll = get_collection("workflow_states")
        doc = coll.find_one({"$or": [{"_id": "default"}, {"id": "default"}]}) or {
            "_id": "default",
            "id": "default",
            "active_section": "section1",
            "active_step": 1,
            "pitch_sent_map": {},
            "ai_choice_map": {},
            "answer_sent_map": {},
            "persuasion_sent_map": {},
            "creator_stage_map": {},
            "extra_state": {},
        }

        if body.active_section is not None:
            doc["active_section"] = body.active_section
        if body.active_step is not None:
            doc["active_step"] = body.active_step
        if body.selected_creator_id is not None:
            doc["selected_creator_id"] = body.selected_creator_id
        if body.active_project_id is not None:
            doc["active_project_id"] = body.active_project_id

        for fld in ["pitch_sent_map", "ai_choice_map", "answer_sent_map", "persuasion_sent_map", "creator_stage_map", "extra_state"]:
            val = getattr(body, fld, None)
            if val is not None:
                if body.replace:
                    doc[fld] = _safe_dict(val)
                else:
                    m = _safe_dict(doc.get(fld))
                    m.update(_safe_dict(val))
                    doc[fld] = m

        # Pass fee updates: preserve whatever the user explicitly set
        fee_to_set = None
        if body.default_pass_price is not None:
            fee_to_set = float(body.default_pass_price)
        elif body.cobuilder_pass_price is not None:
            fee_to_set = float(body.cobuilder_pass_price)
        elif body.extra_state:
            extra_in = _safe_dict(body.extra_state)
            if extra_in.get("default_pass_price") is not None:
                fee_to_set = float(extra_in["default_pass_price"])
            elif extra_in.get("cobuilder_pass_price") is not None:
                fee_to_set = float(extra_in["cobuilder_pass_price"])

        if fee_to_set is not None:
            doc["default_pass_price"] = fee_to_set
            doc["cobuilder_pass_price"] = fee_to_set
            extra = _safe_dict(doc.get("extra_state"))
            extra["default_pass_price"] = fee_to_set
            extra["cobuilder_pass_price"] = fee_to_set
            doc["extra_state"] = extra

        doc["updated_at"] = datetime.utcnow().isoformat()
        doc["id"] = "default"
        doc["_id"] = "default"
        if coll is not None:
            coll.replace_one({"_id": "default"}, doc, upsert=True)

        res = copy.deepcopy(doc)
        res.pop("_id", None)
        _workflow_cache = copy.deepcopy(res)
        _workflow_cache_time = time.time()
        return res
    except Exception as e:
        logger.error(f"[MongoDB] update_workflow_state error: {e}")
        raise HTTPException(500, f"MongoDB error: {e}")
