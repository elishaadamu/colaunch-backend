import os
import re
import json
import uuid
import copy
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection

logger = logging.getLogger("creator_forge.mongodb")

BASE_DIR = Path(__file__).resolve().parent.parent

# DNS resolver patch to prevent macOS local router DNS timeouts
try:
    import dns.resolver
    _orig_resolver_init = dns.resolver.Resolver.__init__
    def _patched_resolver_init(self, *args, **kwargs):
        kwargs["configure"] = False
        _orig_resolver_init(self, *args, **kwargs)
        self.nameservers = ["8.8.8.8", "1.1.1.1", "8.8.4.4", "1.0.0.1"]
        self.lifetime = 10.0
        self.timeout = 5.0
    dns.resolver.Resolver.__init__ = _patched_resolver_init
except Exception:
    pass


def get_mongo_uri() -> str:
    uri = os.getenv("MONGODB_URI", os.getenv("MONGO_URL", "")).strip().strip('"\'')
    if not uri:
        try:
            from app.config import settings
            uri = getattr(settings, "MONGODB_URI", "").strip().strip('"\'')
        except Exception:
            pass
    if not uri:
        uri = "mongodb+srv://creatorforgeweb_db_user:radLPHdUYy7g3tGB@cluster0.nnewamw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    return uri.strip().strip('"\'')



SCHEMA_COLLECTIONS = [
    "analyses", "audit_logs", "autonomous_campaigns", "campaigns",
    "chat_messages", "co_launch_projects", "contacts", "content_samples", "creator_campaign_tasks",
    "creators", "decks", "follow_ups", "media_images", "metrics_snapshots",
    "outreach_messages", "partnerships", "post_suggestions", "product_recommendations",
    "replies", "reviews", "support_messages", "suppression_list", "target_niches", "threads",
    "user_profiles", "validation_campaigns", "validation_gate_decisions",
    "validation_plans", "validation_telemetry", "workflow_states",
]

_client: Optional[MongoClient] = None
_db: Optional[Database] = None
_lock = threading.Lock()


def get_mongo_client() -> MongoClient:
    """Returns singleton MongoDB Atlas client connected to cluster."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is not None:
            return _client
        uri = get_mongo_uri()
        kwargs: Dict[str, Any] = {
            "serverSelectionTimeoutMS": 30000,
            "connectTimeoutMS": 30000,
            "socketTimeoutMS": 45000,
            "maxPoolSize": 50,
            "minPoolSize": 1,
            "retryWrites": True,
            "retryReads": True,
            "readPreference": "primaryPreferred",
        }
        try:
            import certifi
            kwargs["tlsCAFile"] = certifi.where()
        except Exception:
            pass
        _client = MongoClient(uri, **kwargs)
        logger.info("[MongoDB] Connected directly to MongoDB Atlas cluster.")
        return _client


def get_mongo_db() -> Database:
    """Returns primary MongoDB database."""
    global _db
    if _db is not None:
        return _db
    client = get_mongo_client()
    db_name = os.getenv("MONGODB_DB_NAME", "creator_forge").strip()
    _db = client[db_name]
    return _db


get_mongo_database = get_mongo_db


def get_collection(name: str) -> Collection:
    """Primary accessor for MongoDB Atlas collections. Always queries real MongoDB."""
    db = get_mongo_db()
    return db[name]


def check_mongo_connection() -> Dict[str, Any]:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        db = get_mongo_db()
        return {
            "status": "connected",
            "database": db.name,
            "collections": db.list_collection_names(),
            "mode": "atlas"
        }
    except Exception as e:
        logger.warning(f"[MongoDB] Connection check warning: {e}")
        return {
            "status": "error",
            "database": os.getenv("MONGODB_DB_NAME", "creator_forge"),
            "error": str(e),
            "mode": "atlas"
        }


def seed_from_backup_if_empty():
    """No-op: All data is maintained purely in MongoDB Atlas."""
    pass
