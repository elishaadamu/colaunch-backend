# -*- coding: utf-8 -*-
"""
Authentication and Authorization dependencies for CoLaunch API.
Distinguishes between:
  1. Operator (admin studio access, full control)
  2. Creator (portal access, strictly limited to their own project)
  3. Public (rate-limited, public landing pages & event tracking only)
"""
import os
import re
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import Request, Header, Query, HTTPException, Depends

logger = logging.getLogger(__name__)


class AuthRole:
    OPERATOR = "operator"
    CREATOR = "creator"
    PUBLIC = "public"


class AuthenticatedUser(BaseModel):
    role: str
    identifier: str
    project_id: Optional[str] = None
    slug: Optional[str] = None
    creator_handle: Optional[str] = None


def _extract_token(
    request: Request,
    authorization: Optional[str] = None,
    x_operator_token: Optional[str] = None,
    x_portal_token: Optional[str] = None,
    token: Optional[str] = None,
    portal_token: Optional[str] = None,
) -> Optional[str]:
    """Extract auth token from headers or query parameters."""
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        if len(parts) == 1:
            return parts[0].strip()

    if x_operator_token:
        return x_operator_token.strip()

    if x_portal_token:
        return x_portal_token.strip()

    if token:
        return token.strip()

    if portal_token:
        return portal_token.strip()

    query_params = request.query_params
    for key in ("token", "portal_token", "auth_token", "key"):
        val = query_params.get(key)
        if val:
            return val.strip()

    return None


def get_current_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_operator_token: Optional[str] = Header(None, alias="X-Operator-Token"),
    x_portal_token: Optional[str] = Header(None, alias="X-Portal-Token"),
    token: Optional[str] = Query(None),
    portal_token: Optional[str] = Query(None),
) -> AuthenticatedUser:
    """
    Authenticate incoming request and resolve role:
    - Operator: via 'forge_op_' session token, 'op_' token, or configured operator secret.
    - Creator: via matching portal token mapped to their specific project in MongoDB.
    - Public: unauthenticated requests.
    """
    raw_token = _extract_token(
        request,
        authorization=authorization,
        x_operator_token=x_operator_token,
        x_portal_token=x_portal_token,
        token=token,
        portal_token=portal_token,
    )

    if not raw_token:
        return AuthenticatedUser(role=AuthRole.PUBLIC, identifier="public")

    # 1. Operator validation
    operator_secret = os.getenv("OPERATOR_SECRET_KEY", os.getenv("OPERATOR_TOKEN", "")).strip()
    is_operator = False

    if raw_token.startswith("forge_op_") or raw_token.startswith("op_"):
        is_operator = True
    elif operator_secret and raw_token == operator_secret:
        is_operator = True

    if is_operator:
        return AuthenticatedUser(role=AuthRole.OPERATOR, identifier="operator")

    # 2. Creator validation (lookup in MongoDB co_launch_projects)
    try:
        from app.mongodb import get_collection
        coll = get_collection("co_launch_projects")
        if coll is not None:
            clean_token = raw_token.strip()
            # Match portal_token or project ID/slug directly
            query_conditions = [
                {"portal_token": clean_token},
                {"portalToken": clean_token},
                {"portal_access_token": clean_token},
                {"metadataInfo.portal_token": clean_token},
                {"metadata_info.portal_token": clean_token},
                {"metadataInfo.portalToken": clean_token},
                {"metadata_info.portalToken": clean_token},
            ]
            
            # Support cf_sec_live / cf_sec_<slug> portal link patterns
            if clean_token.startswith("cf_sec_"):
                slug_part = clean_token[7:].lower().strip()
                if slug_part and slug_part != "live":
                    query_conditions.extend([
                        {"slug": slug_part},
                        {"id": slug_part},
                        {"creator_handle": slug_part},
                        {"creatorHandle": slug_part},
                    ])
                elif slug_part == "live":
                    # cf_sec_live: match token or check if project is explicitly linked
                    query_conditions.extend([
                        {"portal_token": "cf_sec_live"},
                        {"portalToken": "cf_sec_live"},
                    ])

            doc = coll.find_one({"$or": query_conditions})
            if doc:
                p_id = str(doc.get("id") or doc.get("_id") or "")
                slug = str(doc.get("slug") or "")
                c_handle = str(doc.get("creator_handle") or doc.get("creatorHandle") or "").lstrip("@")
                return AuthenticatedUser(
                    role=AuthRole.CREATOR,
                    identifier=p_id,
                    project_id=p_id,
                    slug=slug,
                    creator_handle=c_handle,
                )
    except Exception as e:
        logger.warning(f"[Auth] Creator token lookup error: {e}")

    # Invalid token provided
    return AuthenticatedUser(role=AuthRole.PUBLIC, identifier="public")


def require_operator(user: AuthenticatedUser = Depends(get_current_auth)) -> AuthenticatedUser:
    """Enforce operator-only access."""
    if user.role != AuthRole.OPERATOR:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Operator credentials required to perform this action.",
        )
    return user


def require_project_access(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_auth),
) -> AuthenticatedUser:
    """
    Enforce project-scoped access:
    - Operators can access any project.
    - Creators can strictly access their own project.
    - Public / unauthenticated users are rejected.
    """
    if user.role == AuthRole.OPERATOR:
        return user

    if user.role == AuthRole.CREATOR:
        target_clean = project_id.replace("@", "").lower().strip()
        user_p_id = (user.project_id or "").replace("@", "").lower().strip()
        user_slug = (user.slug or "").lower().strip()
        user_handle = (user.creator_handle or "").lower().strip()

        if target_clean in (user_p_id, user_slug, user_handle):
            return user

        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to access or modify this project.",
        )

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Authentication required. Please provide a valid operator token or creator portal token.",
    )
