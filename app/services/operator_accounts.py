# -*- coding: utf-8 -*-
"""
Operator accounts (Acquisition OS / Project OS studio logins).

Passwords are stored as salted PBKDF2-SHA256 hashes in MongoDB (`operator_accounts`).
Anyone can self-register (set OPERATOR_SIGNUP_DISABLED=true to close sign-up).
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

COLLECTION = "operator_accounts"
PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _collection():
    from app.mongodb import get_collection
    coll = get_collection(COLLECTION)
    try:
        coll.create_index("email", unique=True)
    except Exception as e:  # index creation is best-effort
        logger.debug(f"[OperatorAccounts] index ensure skipped: {e}")
    return coll


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def validate_new_account(name: str, email: str, password: str) -> tuple[str, str]:
    clean_email = (email or "").strip().lower()
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > 80:
        raise SignupError(422, "Please enter your name (up to 80 characters).")
    if not EMAIL_RE.match(clean_email) or len(clean_email) > 254:
        raise SignupError(422, "Please enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise SignupError(422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 256:
        raise SignupError(422, "Password is too long.")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise SignupError(422, "Password must contain both letters and numbers.")
    return clean_name, clean_email


def create_operator_account(name: str, email: str, password: str, created_by: str) -> dict:
    clean_name, clean_email = validate_new_account(name, email, password)
    coll = _collection()
    if coll.find_one({"email": clean_email}):
        raise SignupError(409, "An account with this email already exists. Sign in instead.")

    salt = secrets.token_bytes(16)
    doc = {
        "email": clean_email,
        "name": clean_name,
        "role": "operator_admin",
        "password_hash": _hash_password(password, salt),
        "password_salt": salt.hex(),
        "password_iterations": PBKDF2_ITERATIONS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": created_by,
        "active": True,
    }
    try:
        coll.insert_one(doc)
    except Exception as e:
        if "duplicate key" in str(e).lower():
            raise SignupError(409, "An account with this email already exists. Sign in instead.")
        logger.error(f"[OperatorAccounts] insert failed: {e}")
        raise SignupError(500, "Could not create the account. Please try again.")
    logger.info(f"[OperatorAccounts] created operator account for {clean_email} (by {created_by})")
    return {"email": clean_email, "name": clean_name, "role": "operator_admin"}


def authenticate_operator_account(email: str, password: str) -> Optional[dict]:
    """Return the account's public profile if the email/password match an active account."""
    clean_email = (email or "").strip().lower()
    if not clean_email or not password:
        return None
    try:
        doc = _collection().find_one({"email": clean_email, "active": {"$ne": False}})
    except Exception as e:
        logger.warning(f"[OperatorAccounts] lookup failed: {e}")
        return None
    if not doc:
        # Spend comparable time so response timing doesn't reveal which emails exist.
        _hash_password(password, b"\x00" * 16)
        return None
    try:
        salt = bytes.fromhex(doc["password_salt"])
        iterations = int(doc.get("password_iterations") or PBKDF2_ITERATIONS)
        candidate = _hash_password(password, salt, iterations)
    except (KeyError, ValueError):
        return None
    if not hmac.compare_digest(candidate, doc.get("password_hash", "")):
        return None
    return {"email": doc["email"], "name": doc.get("name") or "Operator", "role": doc.get("role") or "operator_admin"}
