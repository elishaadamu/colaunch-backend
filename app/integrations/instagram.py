"""
Instagram Graph API integration stub.
Requires a Facebook Developer App with instagram_basic permission.
Only accesses public profile data — no private message scraping.
"""
from app.config import settings
from app.integrations.base import BasePlatformIntegration, IntegrationNotConfiguredError


class InstagramIntegration(BasePlatformIntegration):
    platform = "instagram"

    def is_configured(self) -> bool:
        return bool(settings.INSTAGRAM_ACCESS_TOKEN)

    def search_creators(self, query: str, min_followers: int = 100_000, max_results: int = 20) -> list[dict]:
        if not self.is_configured():
            raise IntegrationNotConfiguredError("INSTAGRAM_ACCESS_TOKEN not set")
        # NOTE: Instagram Graph API does not support general creator search.
        # Use third-party social listening APIs (Modash, HypeAuditor, Creator.co)
        # or manual import CSVs for discovery.
        return []

    def get_creator_profile(self, handle: str) -> dict:
        if not self.is_configured():
            raise IntegrationNotConfiguredError("INSTAGRAM_ACCESS_TOKEN not set")
        # TODO: GET graph.facebook.com/v18.0/{username}?fields=biography,followers_count,website
        return {}

    def get_recent_posts(self, handle: str, limit: int = 20) -> list[dict]:
        if not self.is_configured():
            raise IntegrationNotConfiguredError("INSTAGRAM_ACCESS_TOKEN not set")
        # TODO: GET media?fields=id,caption,like_count,comments_count,timestamp,permalink
        return []

    def get_public_contact_info(self, handle: str) -> dict:
        if not self.is_configured():
            raise IntegrationNotConfiguredError("INSTAGRAM_ACCESS_TOKEN not set")
        # Extract from biography field and website field only
        return {}

    async def publish_post(self, access_token: str, business_id: str, caption: str, media_url: str = None) -> str:
        """
        Publish an image post to Instagram Graph API.
        1. Create media container: POST /{business_id}/media
        2. Publish container: POST /{business_id}/media_publish
        """
        import httpx
        if not access_token or not business_id:
            raise ValueError("Instagram credentials (access token and business ID) are required.")

        # Default fallback image if none provided (premium abstract graphic)
        if not media_url:
            media_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&auto=format&fit=crop"

        # Step 1: Create media container
        container_url = f"https://graph.facebook.com/v21.0/{business_id}/media"
        container_data = {
            "image_url": media_url,
            "caption": caption,
            "access_token": access_token
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(container_url, data=container_data)
            resp_data = resp.json()

        if "error" in resp_data:
            raise Exception(f"Instagram container creation failed: {resp_data['error'].get('message')}")

        creation_id = resp_data.get("id")
        if not creation_id:
            raise Exception("No creation ID returned from Instagram API.")

        # Step 2: Publish media container
        publish_url = f"https://graph.facebook.com/v21.0/{business_id}/media_publish"
        publish_data = {
            "creation_id": creation_id,
            "access_token": access_token
        }

        async with httpx.AsyncClient() as client:
            pub_resp = await client.post(publish_url, data=publish_data)
            pub_resp_data = pub_resp.json()

        if "error" in pub_resp_data:
            raise Exception(f"Instagram media publish failed: {pub_resp_data['error'].get('message')}")

        return pub_resp_data.get("id")


instagram = InstagramIntegration()

