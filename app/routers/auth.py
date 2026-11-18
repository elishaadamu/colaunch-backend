import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from app.config import settings
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.creator import UserProfile

router = APIRouter(prefix="/api/auth", tags=["Auth"])

def get_frontend_url(request: Request) -> str:
    """
    Get the frontend dashboard URL dynamically based on settings.FRONTEND_URL
    or Request headers as a fallback.
    """
    if settings.FRONTEND_URL:
        base = settings.FRONTEND_URL.rstrip("/")
        if not base.endswith("/dashboard"):
            return f"{base}/dashboard"
        return base

    referer = request.headers.get("referer") or request.headers.get("origin")
    if referer:
        parsed = urlparse(referer)
        # Prevent redirecting back to facebook domain
        if "facebook.com" not in parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/dashboard"
    return "http://localhost:5173/dashboard"

def get_callback_uri(request: Request) -> str:
    """
    Get the callback redirect URI matching uvicorn's bound host and port.
    """
    return str(request.base_url).rstrip("/") + "/api/auth/instagram/callback"

@router.get("/instagram/login")
async def instagram_login(request: Request, handle: str = "default", db: Session = Depends(get_db)):
    """
    Initiate the Meta/Instagram OAuth flow.
    """
    frontend_url = get_frontend_url(request)
    client_id = None

    # Try looking up custom Meta App credentials for this creator
    if handle and handle != "default":
        users = db.query(UserProfile).all()
        for u in users:
            if u.creator_data and isinstance(u.creator_data, dict):
                c_handle = u.creator_data.get("handle")
                if c_handle and c_handle.lower() == handle.lower():
                    client_id = u.creator_data.get("meta_client_id")
                    break

    # Fallback to backend config settings
    if not client_id:
        client_id = settings.META_CLIENT_ID

    if not client_id or client_id == "YOUR_META_APP_ID_HERE":
        # If the user hasn't set up the Meta App yet, we can simulate success for demo purposes
        # Or we could raise an error. Let's redirect to frontend with a fake success parameter 
        # so the UI still works even without a real Meta App configured.
        return RedirectResponse(f"{frontend_url}?tab=accounts&ig_connected=true&demo_mode=true")

    # The required scopes for Instagram publishing
    scopes = "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list"
    
    # We pass the user's handle in the state parameter so we know who they are when they return
    state = handle
    redirect_uri = get_callback_uri(request)

    oauth_url = (
        f"https://www.facebook.com/v21.0/dialog/oauth"
        f"?client_id={client_id}"
        f"&display=page"
        f"&extras={{\"setup\":{{\"channel\":\"IG_API\"}}}}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={scopes}"
        f"&state={state}"
    )
    return RedirectResponse(oauth_url)


@router.get("/instagram/callback")
async def instagram_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
    db: Session = Depends(get_db)
):
    """
    Handle the callback from Meta after the user authenticates.
    """
    frontend_url = get_frontend_url(request)
    redirect_uri = get_callback_uri(request)

    if error:
        print(f"Meta OAuth Error: {error} - {error_description}")
        return RedirectResponse(f"{frontend_url}?tab=accounts&ig_error=true")

    if not code:
        raise HTTPException(status_code=400, detail="No code provided")

    handle = state or "default"
    client_id = None
    client_secret = None

    # Try looking up custom Meta App credentials for this creator
    if handle and handle != "default":
        users = db.query(UserProfile).all()
        for u in users:
            if u.creator_data and isinstance(u.creator_data, dict):
                c_handle = u.creator_data.get("handle")
                if c_handle and c_handle.lower() == handle.lower():
                    client_id = u.creator_data.get("meta_client_id")
                    client_secret = u.creator_data.get("meta_client_secret")
                    break

    # Fallback to backend config settings
    if not client_id:
        client_id = settings.META_CLIENT_ID
    if not client_secret:
        client_secret = settings.META_CLIENT_SECRET

    # If keys are missing (which shouldn't happen unless deleted mid-flow), raise error
    if not client_id or not client_secret or client_id == "YOUR_META_APP_ID_HERE":
        print("Meta Client ID or Secret missing in callback lookup.")
        return RedirectResponse(f"{frontend_url}?tab=accounts&ig_error=true")

    # 1. Exchange code for short-lived access token
    token_url = "https://graph.facebook.com/v21.0/oauth/access_token"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "client_secret": client_secret,
        "code": code
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(token_url, params=params)
        data = resp.json()

    if "error" in data:
        print(f"Error fetching token: {data}")
        return RedirectResponse(f"{frontend_url}?tab=accounts&ig_error=true")

    short_lived_token = data.get("access_token")

    # 2. Exchange short-lived token for long-lived token (60 days)
    long_lived_url = "https://graph.facebook.com/v21.0/oauth/access_token"
    ll_params = {
        "grant_type": "fb_exchange_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "fb_exchange_token": short_lived_token
    }

    async with httpx.AsyncClient() as client:
        ll_resp = await client.get(long_lived_url, params=ll_params)
        ll_data = ll_resp.json()

    long_lived_token = ll_data.get("access_token")

    if long_lived_token:
        print(f"Successfully obtained long-lived token for {handle}: {long_lived_token[:15]}...")
        # Save token directly to the user's profile database!
        if handle and handle != "default":
            users = db.query(UserProfile).all()
            for u in users:
                if u.creator_data and isinstance(u.creator_data, dict):
                    c_handle = u.creator_data.get("handle")
                    if c_handle and c_handle.lower() == handle.lower():
                        c_data = dict(u.creator_data)
                        c_data["instagram_access_token"] = long_lived_token
                        u.creator_data = c_data
                        db.commit()
                        break
    
    return RedirectResponse(f"{frontend_url}?tab=accounts&ig_connected=true")


from pydantic import BaseModel

class LaunchLoginRequest(BaseModel):
    email: str
    password: str

@router.post("/launch-login")
def launch_login(payload: LaunchLoginRequest):
    """
    Validate operator credentials for /launch and internal administration routes.
    Restricted to authorized operator email with 1-hour session duration.
    """
    from app.auth import verify_operator_credentials, issue_operator_token, OPERATOR_TOKEN_TTL_SECONDS
    import time

    email_clean = (payload.email or "").strip().lower()
    if verify_operator_credentials(email_clean, payload.password or ""):
        return {
            "status": "success",
            "message": "Operator authenticated successfully",
            "token": issue_operator_token(),
            "user": {
                "email": email_clean,
                "name": "Operator Admin",
                "role": "operator_admin"
            },
            "authenticated_at": int(time.time() * 1000),
            "expires_in_seconds": OPERATOR_TOKEN_TTL_SECONDS
        }

    raise HTTPException(status_code=401, detail="Invalid operator credentials. Access restricted.")

