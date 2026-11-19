from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models.creator import Creator, ProductRecommendation, PostSuggestion
from app.services import content_calendar as calendar_svc


router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class GenerateCalendarRequest(BaseModel):
    creator_id: str
    product_recommendation_id: str


class SuggestionResponse(BaseModel):
    id: str
    creator_id: str
    product_recommendation_id: str
    hook: str
    body: str
    platform: str
    status: str
    scheduled_for: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/{creator_id}", response_model=List[SuggestionResponse])
def get_creator_calendar(creator_id: str, db: Session = Depends(get_db)):
    """Retrieve all content suggestions (calendar posts) for a creator."""
    posts = (
        db.query(PostSuggestion)
        .filter(PostSuggestion.creator_id == creator_id)
        .order_by(PostSuggestion.scheduled_for.asc())
        .all()
    )
    return posts


@router.post("/generate", response_model=List[SuggestionResponse])
def generate_creator_calendar(body: GenerateCalendarRequest, db: Session = Depends(get_db)):
    """Generate 5 fresh AI post suggestions to promote a product idea."""
    try:
        posts = calendar_svc.generate_calendar(
            db=db,
            creator_id=body.creator_id,
            product_rec_id=body.product_recommendation_id,
            actor="ops_dashboard"
        )
        return posts
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error generating calendar: {str(e)}")


@router.post("/{post_id}/approve", response_model=SuggestionResponse)
def approve_calendar_post(post_id: str, db: Session = Depends(get_db)):
    """Approve a post draft to move it to 'approved' status."""
    post = db.get(PostSuggestion, post_id)
    if not post:
        raise HTTPException(404, "Post suggestion not found")
    post.status = "approved"
    db.commit()
    db.refresh(post)
    return post


@router.post("/{post_id}/queue", response_model=SuggestionResponse)
def queue_calendar_post(post_id: str, db: Session = Depends(get_db)):
    """Queue an approved post suggestion for automatic publishing."""
    post = db.get(PostSuggestion, post_id)
    if not post:
        raise HTTPException(404, "Post suggestion not found")
    if post.status != "approved":
        post.status = "approved"  # Auto approve if not already
    post.status = "queued"
    db.commit()
    db.refresh(post)
    return post


@router.post("/{post_id}/post", response_model=SuggestionResponse)
def publish_calendar_post(post_id: str, db: Session = Depends(get_db)):
    """Publish the post to the connected social account. Marked 'posted' only when the platform accepts it."""
    published_id = None
    publish_error = None
    post = db.get(PostSuggestion, post_id)
    if not post:
        raise HTTPException(404, "Post suggestion not found")
    
    # Check if this is an Instagram or Facebook post and check if user has configured custom credentials
    if post.platform.lower() == "instagram":
        from app.models.creator import UserProfile, Creator
        from app.integrations.instagram import instagram as instagram_api
        import asyncio

        creator = db.get(Creator, post.creator_id)
        if creator:
            user_profiles = db.query(UserProfile).all()
            matching_user = None
            for up in user_profiles:
                if up.creator_data and isinstance(up.creator_data, dict):
                    h1 = (up.creator_data.get("handle") or "").strip().lower().lstrip("@")
                    h2 = creator.handle.strip().lower().lstrip("@")
                    if h1 == h2:
                        matching_user = up
                        break
            
            if matching_user:
                ig_token = matching_user.creator_data.get("instagram_access_token")
                ig_business_id = matching_user.creator_data.get("instagram_business_id")
                
                if ig_token and ig_business_id:
                    try:
                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                        
                        caption_text = f"{post.hook}\n\n{post.body}"
                        media_id = loop.run_until_complete(instagram_api.publish_post(
                            access_token=ig_token,
                            business_id=ig_business_id,
                            caption=caption_text
                        ))
                        published_id = media_id
                    except Exception as e:
                        publish_error = f"Instagram rejected the post: {e}"

    elif post.platform.lower() in ("facebook", "fb"):
        from app.models.creator import UserProfile, Creator
        from app.integrations.facebook import facebook as facebook_api
        import asyncio

        creator = db.get(Creator, post.creator_id)
        if creator:
            user_profiles = db.query(UserProfile).all()
            matching_user = None
            for up in user_profiles:
                if up.creator_data and isinstance(up.creator_data, dict):
                    h1 = (up.creator_data.get("handle") or "").strip().lower().lstrip("@")
                    h2 = creator.handle.strip().lower().lstrip("@")
                    if h1 == h2:
                        matching_user = up
                        break
            
            if matching_user:
                fb_token = matching_user.creator_data.get("facebook_access_token")
                fb_page_id = matching_user.creator_data.get("facebook_page_id")
                
                if fb_token and fb_page_id:
                    try:
                        try:
                            loop = asyncio.get_event_loop()
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                        
                        message_text = f"{post.hook}\n\n{post.body}"
                        fb_post_id = loop.run_until_complete(facebook_api.publish_feed_post(
                            page_id=fb_page_id,
                            access_token=fb_token,
                            message=message_text
                        ))
                        published_id = fb_post_id
                    except Exception as e:
                        publish_error = f"Facebook rejected the post: {e}"

    if not published_id:
        raise HTTPException(400, publish_error or f"{post.platform} isn't connected for this creator, so nothing was posted. Connect the account first.")
    post.status = "posted"
    db.commit()
    db.refresh(post)
    return post

