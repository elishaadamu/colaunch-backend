# -*- coding: utf-8 -*-
"""
MongoDB persistence for the SQLAlchemy outreach tables.

MongoDB Atlas is the single source of truth. The SQLAlchemy layer that the outreach /
inbox code uses is only an in-process working copy (in-memory SQLite), so it is:

  * written through: every committed INSERT / UPDATE / DELETE on a mirrored table is
    upserted into (or removed from) the MongoDB collection of the same name, and
  * restored on startup: mirrored tables are reloaded from MongoDB, and the `creators`
    working copy is rebuilt from the MongoDB `creators` collection.

Nothing here touches MongoDB documents of collections that are not mirrored.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session
from sqlalchemy.types import DateTime

logger = logging.getLogger(__name__)

# SQL tables whose rows are persisted 1:1 into the MongoDB collection of the same name.
MIRRORED_TABLES = {
    "outreach_messages",
    "threads",
    "replies",
    "follow_ups",
    "contacts",
    "suppression_list",
}

_PENDING_KEY = "_mongo_pending_ops"
_registered = False


def _collection(name: str):
    try:
        from app.mongodb import get_collection
        return get_collection(name)
    except Exception as e:  # Mongo not configured / unreachable
        logger.debug(f"[MongoPersistence] collection {name} unavailable: {e}")
        return None


def _row_to_doc(obj) -> Dict[str, Any]:
    mapper = sa_inspect(obj).mapper
    doc = {}
    for col in mapper.columns:
        doc[col.name] = getattr(obj, col.key, None)
    pk_vals = [doc.get(c.name) for c in mapper.primary_key]
    doc["_id"] = str(pk_vals[0]) if len(pk_vals) == 1 else "|".join(str(v) for v in pk_vals)
    return doc


def _table_of(obj) -> str:
    try:
        return obj.__table__.name
    except AttributeError:
        return ""


# ── write-through ─────────────────────────────────────────────────────────────
def _after_flush(session: Session, flush_context):
    ops = session.info.setdefault(_PENDING_KEY, [])
    for obj in list(session.new) + list(session.dirty):
        if _table_of(obj) in MIRRORED_TABLES:
            try:
                ops.append(("upsert", _table_of(obj), _row_to_doc(obj)))
            except Exception as e:
                logger.debug(f"[MongoPersistence] serialise failed: {e}")
    for obj in session.deleted:
        if _table_of(obj) in MIRRORED_TABLES:
            try:
                ops.append(("delete", _table_of(obj), {"_id": _row_to_doc(obj)["_id"]}))
            except Exception:
                pass


def _after_commit(session: Session):
    ops = session.info.pop(_PENDING_KEY, None)
    if not ops:
        return
    for op, table, doc in ops:
        coll = _collection(table)
        if coll is None:
            continue
        try:
            if op == "upsert":
                coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
            else:
                coll.delete_one({"_id": doc["_id"]})
        except Exception as e:
            logger.warning(f"[MongoPersistence] {op} {table}/{doc.get('_id')} failed: {e}")


def _after_rollback(session: Session):
    session.info.pop(_PENDING_KEY, None)


def register_write_through():
    global _registered
    if _registered:
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    _registered = True
    logger.info("[MongoPersistence] Write-through to MongoDB enabled for: " + ", ".join(sorted(MIRRORED_TABLES)))


# ── restore on startup ────────────────────────────────────────────────────────
def _coerce(value, column):
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return value


def _restore_table(engine, table) -> int:
    coll = _collection(table.name)
    if coll is None:
        return 0
    with engine.connect() as conn:
        if conn.execute(table.select().limit(1)).first() is not None:
            return 0  # already populated (e.g. real SQL database) — nothing to restore
        restored = 0
        for doc in coll.find({}):
            row = {c.name: _coerce(doc.get(c.name), c) for c in table.columns if c.name in doc}
            pk = [c.name for c in table.primary_key.columns]
            if len(pk) == 1 and row.get(pk[0]) is None and doc.get("_id") is not None:
                row[pk[0]] = str(doc["_id"])
            try:
                conn.execute(table.insert().values(**row))
                restored += 1
            except Exception as e:
                logger.debug(f"[MongoPersistence] skip {table.name}/{doc.get('_id')}: {e}")
        conn.commit()
    return restored


_SQL_CREATOR_STATUSES = {
    "discovered", "qualified", "disqualified", "in_review", "approved", "rejected",
    "suppressed", "contacted", "pitched", "partnered", "launched",
}
_SQL_PLATFORMS = {"instagram", "youtube", "tiktok", "twitter", "linkedin", "podcast"}


def _restore_creators(engine) -> int:
    """Rebuild the SQL creators working copy from MongoDB `creators` (the real records)."""
    coll = _collection("creators")
    if coll is None:
        return 0
    from app.models.creator import Creator
    table = Creator.__table__
    restored = 0
    with engine.connect() as conn:
        existing = {r[0] for r in conn.execute(table.select().with_only_columns(table.c.id))}
        for doc in coll.find({}, {"_id": 1, "id": 1, "handle": 1, "platform": 1, "display_name": 1, "name": 1,
                                   "email_public": 1, "email": 1, "status": 1, "follower_count": 1, "niche": 1,
                                   "bio": 1, "profile_url": 1, "avatar_url": 1, "discovery_notes": 1}):
            c_id = str(doc.get("id") or doc.get("_id"))
            if not c_id or c_id in existing:
                continue
            status = str(doc.get("status") or "discovered").lower()
            platform = str(doc.get("platform") or "youtube").lower()
            niche = doc.get("niche") or []
            if isinstance(niche, str):
                niche = [niche]
            try:
                followers = int(doc.get("follower_count") or 0)
            except (TypeError, ValueError):
                followers = 0
            try:
                conn.execute(table.insert().values(
                    id=c_id,
                    handle=str(doc.get("handle") or c_id).lstrip("@"),
                    platform=platform if platform in _SQL_PLATFORMS else "youtube",
                    display_name=doc.get("display_name") or doc.get("name") or doc.get("handle"),
                    email_public=doc.get("email_public") or doc.get("email"),
                    status=status if status in _SQL_CREATOR_STATUSES else "contacted",
                    follower_count=followers,
                    niche=niche,
                    bio=doc.get("bio"),
                    profile_url=doc.get("profile_url"),
                    avatar_url=doc.get("avatar_url"),
                    discovery_notes=doc.get("discovery_notes"),
                ))
                restored += 1
            except Exception as e:
                logger.debug(f"[MongoPersistence] skip creator {c_id}: {e}")
        conn.commit()
    return restored


def restore_from_mongo(engine) -> Dict[str, int]:
    """Load mirrored tables + creators from MongoDB into the SQL working copy."""
    from app.database import Base
    summary: Dict[str, int] = {}
    try:
        summary["creators"] = _restore_creators(engine)
    except Exception as e:
        logger.warning(f"[MongoPersistence] creators restore failed: {e}")
    # Parents before children (threads before replies/messages).
    order: List[str] = ["contacts", "suppression_list", "threads", "outreach_messages", "replies", "follow_ups"]
    for name in order:
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        try:
            summary[name] = _restore_table(engine, table)
        except Exception as e:
            logger.warning(f"[MongoPersistence] restore {name} failed: {e}")
    logger.info(f"[MongoPersistence] Restored from MongoDB: {summary}")
    return summary
