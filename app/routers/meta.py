"""
Meta Graph API (Facebook Pages & Instagram Professional) router.
Supports verifying user/page access tokens, discovering connected pages & IG accounts,
and saving credentials to creator profile.
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
import httpx

from app.database import get_db
from app.models.creator import UserProfile, Creator

router = APIRouter(prefix="/api/meta", tags=["meta"])


class VerifyTokenRequest(BaseModel):
    access_token: str


class SavePageRequest(BaseModel):
    handle: Optional[str] = None
    username: Optional[str] = None
    page_id: str
    page_name: str
    page_access_token: str
    user_access_token: Optional[str] = None
    instagram_business_id: Optional[str] = None
    instagram_username: Optional[str] = None


@router.post("/verify")
async def verify_meta_token(payload: VerifyTokenRequest):
    token = payload.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Access token is required")

    async with httpx.AsyncClient() as client:
        # 1. Fetch user identity
        user_res = await client.get(
            "https://graph.facebook.com/v21.0/me",
            params={"fields": "id,name", "access_token": token}
        )
        user_data = user_res.json()
        if "error" in user_data:
            raise HTTPException(status_code=400, detail=user_data["error"].get("message", "Invalid Meta token"))

        # 2. Fetch pages & associated Instagram accounts
        accounts_res = await client.get(
            "https://graph.facebook.com/v21.0/me/accounts",
            params={
                "fields": "id,name,category,access_token,tasks,instagram_business_account{id,username,name,profile_picture_url}",
                "access_token": token
            }
        )
        accounts_data = accounts_res.json()
        pages = accounts_data.get("data", [])

        return {
            "success": True,
            "user": user_data,
            "pages": pages,
            "page_count": len(pages)
        }


@router.post("/save-selection")
def save_meta_selection(payload: SavePageRequest, db: Session = Depends(get_db)):
    profiles = db.query(UserProfile).all()
    target_user = None
    if payload.handle:
        clean_handle = payload.handle.strip().lower().lstrip("@")
        for u in profiles:
            if u.creator_data and isinstance(u.creator_data, dict):
                h = (u.creator_data.get("handle") or "").strip().lower().lstrip("@")
                if h == clean_handle:
                    target_user = u
                    break
    if not target_user and profiles:
        target_user = profiles[0]

    if not target_user:
        return {"success": False, "detail": "No profile found to update"}

    creator_data = dict(target_user.creator_data or {})
    creator_data["facebook_connected"] = True
    creator_data["facebook_page_id"] = payload.page_id
    creator_data["facebook_page_name"] = payload.page_name
    creator_data["facebook_access_token"] = payload.page_access_token
    if payload.user_access_token:
        creator_data["meta_user_token"] = payload.user_access_token

    if payload.instagram_business_id:
        creator_data["instagram_connected"] = True
        creator_data["instagram_business_id"] = payload.instagram_business_id
        creator_data["instagram_access_token"] = payload.page_access_token
        if payload.instagram_username:
            creator_data["instagram_handle"] = payload.instagram_username

    target_user.creator_data = creator_data
    db.add(target_user)
    db.commit()

    # Also persist permanently to MongoDB Atlas user_profiles
    try:
        from app.mongodb import get_collection
        u_coll = get_collection("user_profiles")
        if u_coll is not None:
            meta_conn_doc = {
                "facebook": {
                    "connected": True,
                    "pageId": payload.page_id,
                    "pageName": payload.page_name,
                    "accessToken": payload.page_access_token
                }
            }
            if payload.instagram_business_id:
                meta_conn_doc["instagram"] = {
                    "connected": True,
                    "businessId": payload.instagram_business_id,
                    "username": payload.instagram_username or "@instagram",
                    "accessToken": payload.page_access_token
                }

            clean_h = (payload.handle or "default").replace("@", "").lower().strip()
            clean_u = (payload.username or "").replace("@", "").lower().strip()
            update_data = {
                "meta_connection": meta_conn_doc,
                "creator_data.facebook_connected": True,
                "creator_data.facebook_page_id": payload.page_id,
                "creator_data.facebook_page_name": payload.page_name,
                "creator_data.facebook_access_token": payload.page_access_token,
                "accounts_platforms.facebook.connected": True,
                "accounts_platforms.facebook.pageId": payload.page_id,
                "accounts_platforms.facebook.pageName": payload.page_name,
                "accounts_platforms.facebook.handle": payload.page_name,
            }
            if payload.instagram_business_id:
                update_data["creator_data.instagram_connected"] = True
                update_data["creator_data.instagram_business_id"] = payload.instagram_business_id
                update_data["creator_data.instagram_access_token"] = payload.page_access_token
                update_data["accounts_platforms.instagram.connected"] = True
                update_data["accounts_platforms.instagram.businessId"] = payload.instagram_business_id
                update_data["accounts_platforms.instagram.handle"] = payload.instagram_username or "@instagram"

            match_query = [
                {"creator_data.handle": f"@{clean_h}"},
                {"creator_data.handle": clean_h},
                {"username": clean_h},
                {"email": clean_h}
            ]
            if clean_u:
                match_query.extend([
                    {"username": clean_u},
                    {"email": clean_u}
                ])

            res = u_coll.update_many(
                {"$or": match_query},
                {"$set": update_data}
            )
            if res.matched_count == 0:
                recent = u_coll.find_one(sort=[("_id", -1)])
                if recent:
                    u_coll.update_one({"_id": recent["_id"]}, {"$set": update_data})
    except Exception as mongo_err:
        print(f"[Meta] MongoDB save warning: {mongo_err}")

    return {
        "success": True,
        "facebook_page_name": payload.page_name,
        "facebook_page_id": payload.page_id,
        "instagram_business_id": payload.instagram_business_id
    }


@router.get("/connection")
def get_meta_connection(handle: Optional[str] = "default", username: Optional[str] = None):
    """Retrieve saved Meta / Facebook connection from MongoDB Atlas."""
    try:
        from app.mongodb import get_collection
        u_coll = get_collection("user_profiles")
        if u_coll is not None:
            clean_h = (handle or "default").replace("@", "").lower().strip()
            clean_u = (username or "").replace("@", "").lower().strip()
            query = []
            if clean_u:
                query.extend([{"username": clean_u}, {"email": clean_u}])
            if clean_h and clean_h != "default":
                query.extend([
                    {"username": clean_h},
                    {"email": clean_h},
                    {"creator_data.handle": f"@{clean_h}"},
                    {"creator_data.handle": clean_h}
                ])
            doc = None
            if query:
                doc = u_coll.find_one({"$or": query, "meta_connection.facebook.connected": True})
                if not doc:
                    doc = u_coll.find_one({"$or": query})
            if not doc:
                doc = u_coll.find_one({"meta_connection.facebook.connected": True}, sort=[("_id", -1)])
            if doc and doc.get("meta_connection"):
                return {"success": True, "connection": doc["meta_connection"]}
    except Exception as e:
        print(f"[Meta] Connection lookup warning: {e}")
    return {"success": False, "connection": None}

