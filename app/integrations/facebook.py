"""
Facebook Pages Graph API integration.
Publishes posts, links, and photos directly to a Facebook Page using a Page Access Token.
"""
import httpx


class FacebookIntegration:
    platform = "facebook"

    async def publish_feed_post(self, page_id: str, access_token: str, message: str, link: str = None) -> str:
        """
        Publish a post to the Facebook Page feed: POST /{page_id}/feed
        """
        if not page_id or not access_token:
            raise ValueError("Facebook page_id and access_token are required.")

        url = f"https://graph.facebook.com/v21.0/{page_id}/feed"
        payload = {
            "message": message,
            "access_token": access_token
        }
        if link:
            payload["link"] = link

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=payload)
            data = resp.json()

        if "error" in data:
            raise Exception(f"Facebook publish failed: {data['error'].get('message')}")

        return data.get("id")

    async def publish_photo(self, page_id: str, access_token: str, caption: str, photo_url: str) -> str:
        """
        Publish a photo to the Facebook Page: POST /{page_id}/photos
        """
        if not page_id or not access_token:
            raise ValueError("Facebook page_id and access_token are required.")

        url = f"https://graph.facebook.com/v21.0/{page_id}/photos"
        payload = {
            "caption": caption,
            "url": photo_url,
            "access_token": access_token
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=payload)
            data = resp.json()

        if "error" in data:
            raise Exception(f"Facebook photo publish failed: {data['error'].get('message')}")

        return data.get("id")

    async def get_page_details(self, page_id: str, access_token: str) -> dict:
        """
        Fetch basic page metadata: name, followers, verification, category.
        """
        url = f"https://graph.facebook.com/v21.0/{page_id}"
        params = {
            "fields": "id,name,category,fan_count,link,verification_status",
            "access_token": access_token
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            return resp.json()


facebook = FacebookIntegration()
