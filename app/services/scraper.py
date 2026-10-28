"""
Public profile scraper — YouTube, Instagram, TikTok.
Only reads publicly visible page data. No login, no private info.
"""
import json
import re
import httpx
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+433;",
}

# Module-level connection pool for fast, lightweight I/O without socket exhaustion
_HTTP_CLIENT = httpx.Client(
    timeout=httpx.Timeout(connect=3.0, read=4.0, write=3.0, pool=5.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=30.0),
    headers=HEADERS,
    follow_redirects=True,
)


def _http_get(url: str, headers: dict = None, timeout: float = 4.0, **kwargs):
    h = headers or HEADERS
    return _HTTP_CLIENT.get(url, headers=h, timeout=timeout, **kwargs)


def _http_post(url: str, json: dict = None, headers: dict = None, timeout: float = 4.0, **kwargs):
    h = headers or HEADERS
    return _HTTP_CLIENT.post(url, json=json, headers=h, timeout=timeout, **kwargs)


def _num(s: str) -> int:
    """Parse '2.4M', '890K', '1,234' → int."""
    if not s:
        return 0
    s = s.strip().replace(",", "").replace(" subscribers", "").replace(" subscriber", "").replace(" followers", "").replace(" follower", "")
    try:
        if s.endswith("M") or s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("K") or s.endswith("k"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("B") or s.endswith("b"):
            return int(float(s[:-1]) * 1_000_000_000)
        return int(float(s))
    except Exception:
        return 0


def _follower_count(value) -> int:
    """Normalize platform follower fields that may be numbers or abbreviated strings."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return _num(str(value))


def _first_value(data: dict, *keys):
    """Return the first populated value from a provider response."""
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return 0


def _apify_run(actor_id: str, run_input: dict, api_key: str, timeout_secs: int = 25) -> list:
    """
    Run an Apify actor and return dataset items.
    Uses POST /runs with waitForFinish and dataset items extraction,
    with run-sync fallback.
    """
    import urllib.parse
    import time
    if not api_key:
        return []
    safe_actor = actor_id.replace("/", "~")
    token_str = api_key.strip()

    # 1. Start run with waitForFinish parameter (official Apify pattern)
    try:
        run_url = f"https://api.apify.com/v2/acts/{urllib.parse.quote(safe_actor)}/runs?token={token_str}&waitForFinish={int(timeout_secs)}"
        r = _http_post(run_url, json=run_input, timeout=float(timeout_secs + 5))
        if r.status_code in (200, 201):
            run_data = r.json().get("data", {})
            status = run_data.get("status")
            dataset_id = run_data.get("defaultDatasetId")
            run_id = run_data.get("id")

            if status == "SUCCEEDED" and dataset_id:
                items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token_str}"
                ir = _http_get(items_url, timeout=10.0)
                if ir.status_code == 200:
                    items = ir.json()
                    if isinstance(items, list) and len(items) > 0:
                        return items

            # If still running asynchronously, poll until completion
            if run_id and dataset_id and status not in ("FAILED", "ABORTED", "TIMED-OUT"):
                start_t = time.time()
                while time.time() - start_t < timeout_secs:
                    time.sleep(2.0)
                    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token_str}"
                    pr = _http_get(poll_url, timeout=6.0)
                    if pr.status_code == 200:
                        p_status = pr.json().get("data", {}).get("status")
                        if p_status == "SUCCEEDED":
                            items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token_str}"
                            ir = _http_get(items_url, timeout=10.0)
                            if ir.status_code == 200:
                                items = ir.json()
                                if isinstance(items, list) and len(items) > 0:
                                    return items
                        elif p_status in ("FAILED", "ABORTED", "TIMED-OUT"):
                            break
    except Exception as e:
        logger.warning(f"[Apify Run] Notice for {actor_id}: {e}")

    # 2. Fallback: Direct run-sync-get-dataset-items
    try:
        sync_url = f"https://api.apify.com/v2/acts/{urllib.parse.quote(safe_actor)}/run-sync-get-dataset-items?token={token_str}&timeout={int(timeout_secs)}"
        r = _http_post(sync_url, json=run_input, timeout=float(timeout_secs + 5))
        if r.status_code in (200, 201):
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return data
            elif isinstance(data, dict) and "items" in data:
                return data["items"]
    except Exception as e:
        logger.debug(f"[Apify Fallback] Sync notice: {e}")

    return []


def apify_scrape_tiktok_profiles(handles: list, apify_token: str = None, timeout_secs: int = 90) -> list:
    """
    Run Apify TikTok Profile Scraper (clockworks/tiktok-profile-scraper / 0FXVyOXXEmdGcV88a).
    """
    from app.config import settings
    token = apify_token or settings.APIFY_API_KEY
    if not token or not handles:
        return []

    actor_id = getattr(settings, "APIFY_TIKTOK_ACTOR", "0FXVyOXXEmdGcV88a")
    cleaned_profiles = [str(h).lstrip("@").strip() for h in handles if str(h).strip()]
    if not cleaned_profiles:
        return []

    run_input = {
        "profiles": cleaned_profiles,
        "profileScrapeSections": ["videos"],
        "profileSorting": "latest",
        "resultsPerPage": 10,
        "excludePinnedPosts": False,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
    }

    raw_items = _apify_run(actor_id, run_input, token, timeout_secs=timeout_secs)
    results = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        meta = item.get("authorMeta") or item
        raw_h = meta.get("name") or meta.get("username") or item.get("name") or ""
        clean_h = str(raw_h).lstrip("@").strip()
        if not clean_h or clean_h in seen:
            continue
        seen.add(clean_h)

        display_name = meta.get("nickName") or meta.get("nickname") or item.get("nickname") or clean_h
        fans = int(meta.get("fans") or meta.get("followerCount") or item.get("followerCount") or 0)
        bio = meta.get("signature") or item.get("signature") or meta.get("bio") or ""
        avatar = meta.get("avatar") or meta.get("avatarLarger") or meta.get("avatarMedium") or ""

        # Extract public email from bio or meta
        contacts = _extract_contacts_from_text(bio)
        email = meta.get("email") or meta.get("publicEmail") or (contacts["emails"][0] if contacts["emails"] else "")

        digg_count = int(meta.get("digg") or meta.get("heart") or item.get("heart") or 0)
        video_count = int(meta.get("video") or item.get("videoCount") or 0)
        bio_link = meta.get("bioLink") or item.get("bioLink") or meta.get("externalUrl") or ""
        website = bio_link.get("link", "") if isinstance(bio_link, dict) else str(bio_link or "")

        raw_videos = item.get("latestPosts") or item.get("videos") or item.get("itemList") or []
        recent_posts = []
        for v in (raw_videos if isinstance(raw_videos, list) else []):
            if isinstance(v, dict):
                meta = v.get("videoMeta") or {}
                recent_posts.append({
                    "title": v.get("text") or v.get("desc") or "",
                    "thumbnail": meta.get("coverUrl") or v.get("coverUrl") or "",
                    "url": v.get("webVideoUrl") or "",
                    "likes": v.get("diggCount") or 0,
                    "comments": v.get("commentCount") or 0,
                    "views": v.get("playCount") or 0,
                })

        results.append({
            "handle": clean_h,
            "platform": "tiktok",
            "display_name": display_name,
            "follower_count": fans,
            "followerStr": f"{fans/1_000_000:.1f}M" if fans >= 1_000_000 else f"{fans/1_000:.0f}K" if fans >= 1000 else str(fans),
            "bio": bio,
            "avatar_url": avatar or f"https://ui-avatars.com/api/?name={clean_h}&background=06b6d4&color=fff",
            "email_public": email,
            "email": email,
            "website": website,
            "website_url": website,
            "profile_url": f"https://www.tiktok.com/@{clean_h}",
            "video_count": video_count,
            "recent_posts": recent_posts,
            "recentPosts": recent_posts,
            "raw_apify_data": item,
        })

    return results


def apify_scrape_instagram_profiles(handles: list, apify_token: str = None, timeout_secs: int = 90) -> list:
    """
    Run Apify Instagram Profile Scraper (apify/instagram-profile-scraper / dSCLg0C3YEZ83HzYX).
    """
    from app.config import settings
    token = apify_token or settings.APIFY_API_KEY
    if not token or not handles:
        return []

    actor_id = getattr(settings, "APIFY_INSTAGRAM_ACTOR", "dSCLg0C3YEZ83HzYX")
    cleaned_handles = [str(h).lstrip("@").strip() for h in handles if str(h).strip()]
    if not cleaned_handles:
        return []

    run_input = {
        "usernames": cleaned_handles,
        "includeAboutSection": False,
    }

    raw_items = _apify_run(actor_id, run_input, token, timeout_secs=timeout_secs)
    results = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        raw_h = item.get("username") or item.get("name") or ""
        clean_h = str(raw_h).lstrip("@").strip()
        if not clean_h or clean_h in seen:
            continue
        seen.add(clean_h)

        display_name = item.get("fullName") or item.get("name") or clean_h
        bio = item.get("biography") or item.get("bio") or ""
        followers = int(item.get("followersCount") or item.get("followers") or 0)
        avatar = item.get("profilePicUrlHD") or item.get("profilePicUrl") or ""

        # Extract email
        contacts = _extract_contacts_from_text(bio)
        email = (
            item.get("businessEmail")
            or item.get("publicEmail")
            or item.get("externalEmail")
            or (contacts["emails"][0] if contacts["emails"] else "")
        )

        posts_count = int(item.get("postsCount") or item.get("mediaCount") or 0)
        website = item.get("externalUrl") or item.get("websiteUrl") or item.get("website") or ""

        raw_posts = item.get("latestPosts") or item.get("posts") or item.get("edge_owner_to_timeline_media", {}).get("edges", []) or []
        recent_posts = []
        for p_item in (raw_posts if isinstance(raw_posts, list) else []):
            node = p_item.get("node", p_item) if isinstance(p_item, dict) else {}
            if isinstance(node, dict):
                recent_posts.append({
                    "title": node.get("caption") or "",
                    "thumbnail": node.get("displayUrl") or node.get("thumbnailUrl") or node.get("display_url") or "",
                    "url": node.get("url") or "",
                    "likes": node.get("likesCount") or node.get("like_count") or 0,
                    "comments": node.get("commentsCount") or node.get("comment_count") or 0,
                    "views": node.get("videoViewCount") or node.get("video_view_count") or 0,
                })

        results.append({
            "handle": clean_h,
            "platform": "instagram",
            "display_name": display_name,
            "follower_count": followers,
            "followerStr": f"{followers/1_000_000:.1f}M" if followers >= 1_000_000 else f"{followers/1_000:.0f}K" if followers >= 1000 else str(followers),
            "bio": bio,
            "avatar_url": avatar or f"https://ui-avatars.com/api/?name={clean_h}&background=ec4899&color=fff",
            "email_public": email,
            "email": email,
            "website": website,
            "website_url": website,
            "profile_url": f"https://www.instagram.com/{clean_h}",
            "video_count": posts_count,
            "recent_posts": recent_posts,
            "recentPosts": recent_posts,
            "raw_apify_data": item,
        })

    return results


def apify_scrape_youtube_channels(channels: list, apify_token: str = None, timeout_secs: int = 90) -> list:
    """
    Run Apify YouTube Channel Scraper (streamers/youtube-channel-scraper / 67Q6fmd8iedTVcCwY).
    """
    from app.config import settings
    token = apify_token or settings.APIFY_API_KEY
    if not token or not channels:
        return []

    actor_id = getattr(settings, "APIFY_YOUTUBE_ACTOR", "67Q6fmd8iedTVcCwY")
    start_urls = []
    for ch in channels:
        if not ch:
            continue
        c_str = str(ch).strip()
        if c_str.startswith("http"):
            start_urls.append({"url": c_str})
        else:
            clean_h = c_str.lstrip("@").strip()
            start_urls.append({"url": f"https://www.youtube.com/@{clean_h}"})

    if not start_urls:
        return []

    run_input = {
        "startUrls": start_urls,
        "maxResults": 10,
        "maxResultsShorts": 5,
        "maxResultStreams": 0,
        "sortVideosBy": "NEWEST",
    }

    raw_items = _apify_run(actor_id, run_input, token, timeout_secs=timeout_secs)
    results = []
    seen = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        raw_url = item.get("channelUrl") or item.get("url") or ""
        clean_h = ""
        if "/@" in raw_url:
            clean_h = raw_url.split("/@")[-1].split("/")[0].split("?")[0].strip()
        elif raw_url:
            clean_h = raw_url.split("/")[-1].split("?")[0].lstrip("@").strip()

        if not clean_h:
            clean_h = str(item.get("channelName") or item.get("name") or "").replace(" ", "").lower()

        if not clean_h or clean_h in seen:
            continue
        seen.add(clean_h)

        display_name = item.get("channelName") or item.get("name") or clean_h
        bio = item.get("channelDescription") or item.get("description") or ""
        subs = _follower_count(item.get("numberOfSubscribers") or item.get("subscribers") or item.get("subscriberCount") or 0)
        avatar = item.get("channelAvatarUrl") or item.get("avatarUrl") or ""
        total_views = int(item.get("channelTotalViews") or 0)
        total_videos = int(item.get("channelTotalVideos") or 0)

        contacts = _extract_contacts_from_text(bio)
        email = item.get("email") or (contacts["emails"][0] if contacts["emails"] else "")

        results.append({
            "handle": clean_h,
            "platform": "youtube",
            "display_name": display_name,
            "follower_count": subs,
            "followerStr": f"{subs/1_000_000:.1f}M" if subs >= 1_000_000 else f"{subs/1_000:.0f}K" if subs >= 1000 else str(subs),
            "bio": bio,
            "avatar_url": avatar or f"https://ui-avatars.com/api/?name={clean_h}&background=ef4444&color=fff",
            "email_public": email,
            "email": email,
            "profile_url": f"https://www.youtube.com/@{clean_h}",
            "total_views": total_views,
            "video_count": total_videos,
            "raw_apify_data": item,
        })

    return results


def innertube_fetch_channel(handle_or_query: str, deep: bool = False) -> dict:
    """Fetch live YouTube channel profile data directly using YouTube's Innertube Search and Browse APIs."""
    import urllib.parse
    clean = handle_or_query.lstrip("@").strip()
    if clean.startswith("channel/"):
        clean = clean.split("channel/")[1].strip()
    channel_id = clean if clean.startswith("UC") and len(clean) == 24 else None

    sub_count = 0
    display_name = clean
    avatar_url = ""
    bio = ""
    email_public = ""
    website = ""
    external_urls = []

    # Step 1: Resolve channel_id directly via navigation/resolve_url (exact, zero ambiguity)
    if not channel_id:
        try:
            target_url = f"https://www.youtube.com/@{clean}" if not clean.startswith("http") else clean
            nav_payload = {
                "context": {
                    "client": {
                        "hl": "en",
                        "gl": "US",
                        "clientName": "WEB",
                        "clientVersion": "2.20240401.01.00",
                    }
                },
                "url": target_url,
            }
            r_nav = _http_post("https://www.youtube.com/youtubei/v1/navigation/resolve_url", json=nav_payload, timeout=4.0)
            if r_nav.status_code == 200:
                nav_data = r_nav.json()
                resolved_id = nav_data.get("endpoint", {}).get("browseEndpoint", {}).get("browseId")
                if resolved_id and resolved_id.startswith("UC") and len(resolved_id) == 24:
                    channel_id = resolved_id
        except Exception as e:
            logger.debug(f"[YouTube Innertube resolve_url] Error: {e}")

    # Fallback search if resolve_url did not find a direct channel ID
    if not channel_id:
        search_url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
        payload = {
            "context": {
                "client": {
                    "hl": "en",
                    "gl": "US",
                    "clientName": "WEB",
                    "clientVersion": "2.20240401.01.00",
                }
            },
            "query": clean,
            "params": "EgIQAg%3D%3D",
        }
        try:
            r = _http_post(search_url, json=payload, timeout=4.0)
            if r.status_code == 200:
                data = r.json()
                items = (
                    data.get("contents", {})
                    .get("twoColumnSearchResultsRenderer", {})
                    .get("primaryContents", {})
                    .get("sectionListRenderer", {})
                    .get("contents", [])
                )
                candidate_channels = []
                for section in items:
                    renderers = section.get("itemSectionRenderer", {}).get("contents", [])
                    for item in renderers:
                        ch = item.get("channelRenderer")
                        if ch:
                            candidate_channels.append(ch)

                # Prioritize channel that exactly matches handle or title
                matched_ch = None
                clean_lower = clean.lower()
                for ch in candidate_channels:
                    c_handle = (ch.get("subscriberCountText", {}).get("simpleText") or "").lower().replace("@", "")
                    c_title = (ch.get("title", {}).get("simpleText") or "").lower()
                    c_canon = (ch.get("canonicalBaseUrl") or "").lower().replace("@", "").strip("/")
                    if clean_lower in (c_handle, c_canon) or c_title == clean_lower:
                        matched_ch = ch
                        break

                selected_ch = matched_ch or (candidate_channels[0] if candidate_channels else None)
                if selected_ch:
                    channel_id = selected_ch.get("channelId")
                    canonical = selected_ch.get("canonicalBaseUrl", "").lstrip("/").lstrip("@")
                    if canonical:
                        clean = canonical
                    title = selected_ch.get("title", {}).get("simpleText") or "".join(r.get("text", "") for r in selected_ch.get("title", {}).get("runs", []))
                    if title:
                        display_name = title

                    sub_text = selected_ch.get("videoCountText", {}).get("simpleText") or ""
                    if "subscriber" not in sub_text.lower():
                        sub_text = selected_ch.get("subscriberCountText", {}).get("simpleText") or ""
                    sub_count = _num(sub_text)

                    thumbs = selected_ch.get("thumbnail", {}).get("thumbnails", [])
                    if thumbs:
                        avatar_url = thumbs[-1].get("url", "")
                    desc = "".join(r.get("text", "") for r in selected_ch.get("descriptionSnippet", {}).get("runs", []))
                    bio = desc
        except Exception as e:
            logger.debug(f"[YouTube Innertube Search] Error: {e}")

    # Step 2: Browse channel directly via Innertube Browse API (gets full description & external website)
    if channel_id:
        browse_url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
        b_payload = {
            "context": {
                "client": {
                    "hl": "en",
                    "gl": "US",
                    "clientName": "WEB",
                    "clientVersion": "2.20240401.01.00",
                }
            },
            "browseId": channel_id,
        }
        try:
            r2 = _http_post(browse_url, json=b_payload, timeout=4.0)
            if r2.status_code == 200:
                b_data = r2.json()
                meta = b_data.get("metadata", {}).get("channelMetadataRenderer", {})
                if meta.get("title"):
                    display_name = meta.get("title")
                full_desc = meta.get("description", "")
                if full_desc:
                    bio = full_desc
                if not avatar_url and meta.get("avatar", {}).get("thumbnails"):
                    avatar_url = meta["avatar"]["thumbnails"][-1].get("url", "")

                ph = b_data.get("header", {}).get("pageHeaderRenderer", {}).get("content", {}).get("pageHeaderViewModel", {})
                if ph:
                    # Extract handle & subscriber count from metadataRows
                    rows = ph.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                    for row in rows:
                        for part in row.get("metadataParts", []):
                            txt = part.get("text", {}).get("content", "")
                            if txt.startswith("@"):
                                clean = txt.lstrip("@")
                            if "subscriber" in txt.lower() and sub_count == 0:
                                sub_count = _num(txt)

                    attr = ph.get("attribution", {}).get("attributionViewModel", {})
                    text_content = attr.get("text", {}).get("content", "").strip()
                    if text_content:
                        external_urls.append(text_content)
                    for cmd in attr.get("text", {}).get("commandRuns", []):
                        nav_url = cmd.get("onTap", {}).get("innertubeCommand", {}).get("commandMetadata", {}).get("webCommandMetadata", {}).get("url", "")
                        if "q=" in nav_url:
                            parsed_q = urllib.parse.parse_qs(urllib.parse.urlparse(nav_url).query).get("q", [])
                            if parsed_q:
                                external_urls.append(parsed_q[0])
                        elif nav_url.startswith("http"):
                            external_urls.append(nav_url)

                c4 = b_data.get("header", {}).get("c4TabbedHeaderRenderer", {})
                if c4:
                    if not display_name and c4.get("title"):
                        display_name = c4.get("title")
                    sub_text = c4.get("subscriberCountText", {}).get("simpleText", "")
                    if "subscriber" in sub_text.lower() and sub_count == 0:
                        sub_count = _num(sub_text)
        except Exception as e:
            logger.debug(f"[YouTube Innertube Browse] Error: {e}")

    if avatar_url.startswith("//"):
        avatar_url = "https:" + avatar_url

    # Find candidate websites (not social platforms)
    for u in external_urls:
        u_clean = u.strip().lower()
        if not any(soc in u_clean for soc in ["instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com", "youtube.com", "youtu.be", "discord.gg", "twitch.tv"]):
            website = u.strip()
            break

    # Extract email from full bio
    emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", bio)
    skip_domains = {"example.com", "email.com", "youremail.com", "domain.com", "test.com"}
    for em in emails:
        clean_em = em.strip().lower()
        dom = clean_em.split("@")[-1] if "@" in clean_em else ""
        if dom not in skip_domains:
            email_public = clean_em
            break

    # If deep=True and no email in channel bio, check recent video description sequentially (max 2)
    if deep and not email_public and channel_id:
        try:
            b_vids_payload = {
                "context": {
                    "client": {
                        "hl": "en",
                        "gl": "US",
                        "clientName": "WEB",
                        "clientVersion": "2.20240401.01.00",
                    }
                },
                "browseId": channel_id,
                "params": "EgZ2aWRlb3PyBgQKAjoA",
            }
            rv = _http_post("https://www.youtube.com/youtubei/v1/browse?prettyPrint=false", json=b_vids_payload, timeout=3.5)
            if rv.status_code == 200:
                vids = list(dict.fromkeys(re.findall(r'"videoId":\s*"([a-zA-Z0-9_\-]{11})"', rv.text)))[:2]
                for vid in vids:
                    try:
                        p_res = _http_post(
                            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                            json={"context": {"client": {"hl": "en", "gl": "US", "clientName": "WEB", "clientVersion": "2.20240401.01.00"}}, "videoId": vid},
                            timeout=2.5,
                        )
                        if p_res.status_code == 200:
                            desc = p_res.json().get("videoDetails", {}).get("shortDescription", "")
                            v_emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", desc)
                            for em in v_emails:
                                clean_em = em.strip().lower()
                                dom = clean_em.split("@")[-1] if "@" in clean_em else ""
                                if dom not in skip_domains:
                                    email_public = clean_em
                                    break
                            if email_public:
                                break
                    except Exception:
                        pass
        except Exception as vid_err:
            logger.debug(f"[YouTube Video Email Check] Error: {vid_err}")

    if not channel_id and not bio:
        return {}

    return {
        "handle": clean,
        "channel_id": channel_id or "",
        "platform": "youtube",
        "name": display_name or clean,
        "display_name": display_name or clean,
        "bio": bio,
        "follower_count": sub_count,
        "avatar_url": avatar_url,
        "email_public": email_public,
        "profile_url": f"https://www.youtube.com/@{clean}",
        "niche": [],
        "website": website,
        "external_urls": external_urls,
        "social_links": [],
    }


def scrape_youtube(handle: str, deep: bool = False) -> dict:
    """
    Scrape a YouTube channel by @handle or URL.
    Uses robust Innertube Search + Browse APIs for complete metadata, full bio, and links.
    """
    handle = handle.strip()
    clean_h = handle.lstrip("@").strip()
    if clean_h.startswith("UC") and len(clean_h) == 24:
        url = f"https://www.youtube.com/channel/{clean_h}"
    elif clean_h.startswith(("channel/", "c/", "user/", "@")):
        url = f"https://www.youtube.com/{clean_h}"
    else:
        url = f"https://www.youtube.com/@{clean_h}"

    result = {
        "handle": clean_h,
        "platform": "youtube",
        "profile_url": url,
        "display_name": clean_h,
        "bio": "",
        "avatar_url": "",
        "follower_count": 0,
        "banner_url": "",
        "niche": [],
        "email_public": "",
        "website": "",
        "social_links": [],
    }

    # Step 1: Live Profile & Full Metadata via Innertube Search + Browse
    tube_res = innertube_fetch_channel(clean_h, deep=deep)
    if tube_res:
        result.update(tube_res)

    # Step 2: Fetch real video uploads via YouTube RSS feed & Innertube only if deep=True
    if deep:
        cid = result.get("channel_id") or ""
        vids = fetch_youtube_channel_videos(cid or clean_h, limit=6)
        result["recent_posts"] = vids
        result["recentPosts"] = vids
        result["videos"] = vids
    else:
        result["recent_posts"] = []
        result["recentPosts"] = []
        result["videos"] = []

    # Guess niche from channel title, handle, bio, and video titles
    vid_titles = " ".join((v.get("title") or "") for v in result.get("recent_posts", []))
    combined_text = f"{result.get('display_name', '')} {result.get('handle', '')} {result.get('bio', '')} {vid_titles}".lower()

    niche_patterns = {
        "entertainment": [r"\bentertainment\b", r"\bchallenge\b", r"\bchallenges\b", r"\bcomedy\b", r"\bfunny\b", r"\bbeast\b", r"\bmrbeast\b", r"\bstunt\b", r"\bprank\b", r"\bviral\b", r"\bgiveaway\b"],
        "gaming": [r"\bgaming\b", r"\bgamer\b", r"\btwitch\b", r"\besport\b", r"\bplaythrough\b", r"\bgameplay\b", r"\bminecraft\b", r"\broblox\b"],
        "fitness": [r"\bfitness\b", r"\bworkout\b", r"\bgym\b", r"\bhealth\b", r"\bexercise\b", r"\bcrossfit\b"],
        "cooking": [r"\bcooking\b", r"\brecipe\b", r"\brecipes\b", r"\bkitchen\b", r"\bchef\b", r"\bbaking\b"],
        "tech": [r"\btechnology\b", r"\bsoftware\b", r"\bcoding\b", r"\bdeveloper\b", r"\bprogramming\b", r"\bautomation\b", r"\bgadgets?\b"],
        "finance": [r"\bfinance\b", r"\binvesting\b", r"\bwealth\b", r"\bcrypto\b", r"\bstocks?\b", r"\breal estate\b"],
        "beauty": [r"\bbeauty\b", r"\bmakeup\b", r"\bskincare\b", r"\bcosmetics?\b", r"\bfashion\b"],
        "travel": [r"\btravel\b", r"\bnomad\b", r"\badventure\b", r"\bexploring\b"],
        "education": [r"\beducation\b", r"\btutorials?\b", r"\bcourse\b", r"\bdocumentary\b", r"\bscience\b"],
        "business": [r"\bentrepreneur\b", r"\bbusiness\b", r"\bstartup\b", r"\bfounder\b"],
    }
    detected_niches = []
    for tag, patterns in niche_patterns.items():
        if any(re.search(pat, combined_text) for pat in patterns):
            detected_niches.append(tag)

    if detected_niches:
        result["niche"] = detected_niches
    elif not result.get("niche"):
        result["niche"] = ["entertainment"] if result.get("follower_count", 0) > 100_000 else ["creator"]

    return result


def fetch_youtube_channel_videos(handle_or_channel_id: str, limit: int = 10) -> list[dict]:
    """
    Fetch the real uploaded videos for a YouTube channel using RSS feed with Innertube Browse fallback.
    Returns list of verified video items:
    [
        {
            "id": video_id,
            "videoId": video_id,
            "title": video_title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "views": formatted_views,
            "description": description_text,
            "thumbnail": thumbnail_url,
            "publishedAt": published_iso
        }
    ]
    """
    if not handle_or_channel_id:
        return []

    clean = str(handle_or_channel_id).strip()
    if "youtube.com/" in clean:
        clean = clean.split("youtube.com/")[-1].split("?")[0].strip("/")
    
    clean_h = clean.lstrip("@").strip()
    channel_id = clean_h if clean_h.startswith("UC") and len(clean_h) == 24 else None

    # Step 1: If channel_id is not already known, resolve it via resolve_url or channel page
    if not channel_id:
        try:
            nav_payload = {
                "context": {"client": {"hl": "en", "gl": "US", "clientName": "WEB", "clientVersion": "2.20240401.01.00"}},
                "url": f"https://www.youtube.com/@{clean_h}",
            }
            r_nav = _http_post("https://www.youtube.com/youtubei/v1/navigation/resolve_url", json=nav_payload, timeout=4.0)
            if r_nav.status_code == 200:
                cid = r_nav.json().get("endpoint", {}).get("browseEndpoint", {}).get("browseId")
                if cid and cid.startswith("UC") and len(cid) == 24:
                    channel_id = cid
        except Exception:
            pass

    if not channel_id:
        try:
            r_page = _http_get(f"https://www.youtube.com/@{clean_h}", timeout=4.0)
            if r_page.status_code == 200:
                m_cid = re.search(r'itemprop="channelId"\s+content="(UC[a-zA-Z0-9_\-]{22})"', r_page.text)
                if not m_cid:
                    m_cid = re.search(r'"channelId":\s*"(UC[a-zA-Z0-9_\-]{22})"', r_page.text)
                if not m_cid:
                    m_cid = re.search(r'youtube\.com/channel/(UC[a-zA-Z0-9_\-]{22})', r_page.text)
                if m_cid:
                    channel_id = m_cid.group(1)
        except Exception as e:
            logger.debug(f"[YouTube Video Fetch] Page check error for {clean_h}: {e}")

    # Fallback to innertube search to resolve channel_id
    if not channel_id:
        try:
            info = innertube_fetch_channel(clean_h, deep=False)
            if info and info.get("channel_id"):
                channel_id = info["channel_id"]
        except Exception as e:
            logger.debug(f"[YouTube Video Fetch] Innertube search error for {clean_h}: {e}")

    videos = []

    # Step 2: Try public YouTube RSS Feed (Fastest, zero-token, 100% accurate real video titles)
    if channel_id:
        try:
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            r_rss = _http_get(rss_url, timeout=4.0)
            if r_rss.status_code == 200 and "<entry>" in r_rss.text:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r_rss.text)
                ns = {
                    "atom": "http://www.w3.org/2005/Atom",
                    "yt": "http://www.youtube.com/xml/schemas/2015",
                    "media": "http://search.yahoo.com/mrss/"
                }
                for entry in root.findall("atom:entry", ns)[:limit]:
                    vid_elem = entry.find("yt:videoId", ns)
                    title_elem = entry.find("atom:title", ns)
                    if vid_elem is None or title_elem is None:
                        continue
                    vid_id = vid_elem.text.strip()
                    v_title = title_elem.text.strip()
                    
                    link_elem = entry.find("atom:link", ns)
                    v_url = link_elem.attrib.get("href") if link_elem is not None else f"https://www.youtube.com/watch?v={vid_id}"
                    
                    desc_elem = entry.find("media:group/media:description", ns)
                    v_desc = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""
                    
                    thumb_elem = entry.find("media:group/media:thumbnail", ns)
                    v_thumb = thumb_elem.attrib.get("url") if thumb_elem is not None else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                    
                    stats_elem = entry.find("media:group/media:community/media:statistics", ns)
                    raw_views = stats_elem.attrib.get("views") if stats_elem is not None else "0"
                    
                    pub_elem = entry.find("atom:published", ns)
                    pub_date = pub_elem.text.strip() if pub_elem is not None else ""
                    
                    try:
                        iv = int(raw_views)
                        views_fmt = f"{iv:,} views" if iv < 1000 else (f"{iv/1000:.1f}K views" if iv < 1000000 else f"{iv/1000000:.1f}M views")
                    except Exception:
                        views_fmt = "Verified upload"

                    videos.append({
                        "id": vid_id,
                        "videoId": vid_id,
                        "title": v_title,
                        "url": v_url,
                        "views": views_fmt,
                        "description": v_desc[:250],
                        "thumbnail": v_thumb,
                        "duration": "Video",
                        "publishedAt": pub_date
                    })
        except Exception as rss_err:
            logger.debug(f"[YouTube Video RSS] Error: {rss_err}")

    # Step 3: If RSS didn't return videos, try Innertube browse API (handles modern lockupViewModel and legacy videoRenderer)
    if not videos and channel_id:
        try:
            b_payload = {
                "context": {
                    "client": {
                        "hl": "en",
                        "gl": "US",
                        "clientName": "WEB",
                        "clientVersion": "2.20240401.01.00",
                    }
                },
                "browseId": channel_id,
                "params": "EgZ2aWRlb3PyBgQKAjoA",
            }
            rv = _http_post("https://www.youtube.com/youtubei/v1/browse?prettyPrint=false", json=b_payload, timeout=4.0)
            if rv.status_code == 200:
                d = rv.json()
                tabs = d.get("contents", {}).get("twoColumnBrowseResultsRenderer", {}).get("tabs", [])
                for tab in tabs:
                    grid = tab.get("tabRenderer", {}).get("content", {}).get("richGridRenderer", {})
                    items = grid.get("contents", [])
                    for item in items:
                        rich_item = item.get("richItemRenderer", {}).get("content", {})
                        
                        # Modern lockupViewModel (2024-2026 YouTube Desktop)
                        lk = rich_item.get("lockupViewModel")
                        if lk and lk.get("contentId"):
                            vid_id = lk["contentId"]
                            meta = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
                            v_title = meta.get("title", {}).get("content") or "Video Upload"
                            meta_rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                            row_texts = [p.get("text", {}).get("content", "") for r in meta_rows for p in r.get("metadataParts", [])]
                            v_views = row_texts[0] if row_texts else "Verified upload"
                            
                            sources = lk.get("contentImage", {}).get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
                            v_thumb = sources[-1].get("url", "") if sources else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                            
                            videos.append({
                                "id": vid_id,
                                "videoId": vid_id,
                                "title": v_title,
                                "url": f"https://www.youtube.com/watch?v={vid_id}",
                                "views": v_views,
                                "description": "",
                                "thumbnail": v_thumb,
                                "duration": "Video"
                            })
                            if len(videos) >= limit:
                                break

                        # Legacy videoRenderer fallback
                        vr = rich_item.get("videoRenderer", {})
                        if vr and vr.get("videoId"):
                            vid_id = vr["videoId"]
                            v_title = vr.get("title", {}).get("runs", [{}])[0].get("text") or vr.get("title", {}).get("simpleText") or "Video Upload"
                            v_views = vr.get("viewCountText", {}).get("simpleText") or "Verified upload"
                            v_desc = "".join(r.get("text", "") for r in vr.get("descriptionSnippet", {}).get("runs", []))
                            thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
                            v_thumb = thumbs[-1].get("url", "") if thumbs else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                            
                            videos.append({
                                "id": vid_id,
                                "videoId": vid_id,
                                "title": v_title,
                                "url": f"https://www.youtube.com/watch?v={vid_id}",
                                "views": v_views,
                                "description": v_desc[:250],
                                "thumbnail": v_thumb,
                                "duration": vr.get("lengthText", {}).get("simpleText", "Video"),
                                "publishedAt": vr.get("publishedTimeText", {}).get("simpleText", "")
                            })
                            if len(videos) >= limit:
                                break
                    if videos:
                        break
        except Exception as tube_err:
            logger.debug(f"[YouTube Innertube Videos] Error: {tube_err}")

    return videos[:limit]


def fetch_youtube_video_comments(video_id_or_url: str, limit: int = 10) -> list[dict]:
    """
    Fetch real public audience comments for a YouTube video using Innertube next endpoint.
    Returns list of real comment objects with author handle, text, upvotes, and timestamp.
    """
    if not video_id_or_url:
        return []
    vid = str(video_id_or_url).strip()
    if 'v=' in vid:
        vid = vid.split('v=')[-1].split('&')[0].split('#')[0].strip()
    elif 'youtu.be/' in vid:
        vid = vid.split('youtu.be/')[-1].split('?')[0].split('#')[0].strip()
    elif '/watch/' in vid:
        vid = vid.split('/watch/')[-1].split('?')[0].strip()

    if not vid:
        return []

    url = 'https://www.youtube.com/youtubei/v1/next?prettyPrint=false'
    payload = {
        'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20240101.01.00'}},
        'videoId': vid
    }
    
    comments = []
    seen_texts = set()
    try:
        r1 = _http_post(url, json=payload, timeout=4.0)
        if r1.status_code == 200:
            d1 = r1.json()
            # 1. Microformat check for featured comment
            try:
                mf_comments = d1.get('microformat', {}).get('microformatDataRenderer', {}).get('videoDetails', {}).get('comments', [])
                for mc in mf_comments:
                    author_name = mc.get('author', {}).get('name') or mc.get('author', {}).get('alternateName') or ''
                    text = (mc.get('text') or '').strip()
                    upvotes = mc.get('upvoteCount') or 0
                    if text and text not in seen_texts:
                        seen_texts.add(text)
                        clean_author = author_name if author_name.startswith('@') else f'@{author_name}' if author_name else '@viewer'
                        comments.append({
                            'id': f'mf_{len(comments)}',
                            'author': clean_author,
                            'text': text,
                            'likes': str(upvotes),
                            'published': 'Recent'
                        })
            except Exception:
                pass

            # 2. Extract comment-item-section continuation token
            token = None
            try:
                sections = d1.get('contents', {}).get('twoColumnWatchNextResults', {}).get('results', {}).get('results', {}).get('contents', [])
                for sec in sections:
                    if 'itemSectionRenderer' in sec and sec['itemSectionRenderer'].get('sectionIdentifier') == 'comment-item-section':
                        for c in sec['itemSectionRenderer'].get('contents', []):
                            if 'continuationItemRenderer' in c:
                                token = c['continuationItemRenderer']['continuationEndpoint']['continuationCommand']['token']
                                break
            except Exception:
                pass

            if token:
                payload2 = {
                    'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20240101.01.00'}},
                    'continuation': token
                }
                r2 = _http_post(url, json=payload2, timeout=4.0)
                if r2.status_code == 200:
                    d2 = r2.json()
                    mutations = d2.get('frameworkUpdates', {}).get('entityBatchUpdate', {}).get('mutations', [])
                    for m in mutations:
                        p = m.get('payload', {}).get('commentEntityPayload', {})
                        if p:
                            author = p.get('author', {}).get('displayName') or p.get('author', {}).get('channelTitle') or ''
                            content = (p.get('properties', {}).get('content', {}).get('content') or '').strip()
                            likes_raw = p.get('toolbar', {}).get('likeCountNotliked') or '0'
                            time_text = p.get('properties', {}).get('publishedTime') or ''
                            clean_author = author if author.startswith('@') else f'@{author}' if author else '@viewer'
                            if content and content not in seen_texts:
                                seen_texts.add(content)
                                comments.append({
                                    'id': f'comm_{len(comments)}',
                                    'author': clean_author,
                                    'text': content,
                                    'likes': likes_raw.strip() if str(likes_raw).strip() else '0',
                                    'published': time_text
                                })
                                if len(comments) >= limit:
                                    break
    except Exception as e:
        logger.debug(f"[YouTube Comments Fetch] Error for video {vid}: {e}")

    return comments[:limit]


def fetch_creator_channel_comments(handle_or_id: str, platform: str = "youtube", limit: int = 10) -> list[dict]:
    """
    Fetch genuine audience comments from a creator's channel uploads across platforms.
    If the target is a video ID or URL, fetches comments for that video.
    If it is a creator handle or channel, grabs their latest uploads first and pulls comments.
    If no real comments exist, returns an empty list without fabrication.
    """
    if not handle_or_id:
        return []

    plat = (platform or "youtube").lower()
    target = str(handle_or_id).strip()

    if plat == "youtube":
        # Check if target is directly a video ID (11 chars) or video URL
        is_direct_video = (
            "v=" in target
            or "youtu.be/" in target
            or "/watch/" in target
            or (len(target) == 11 and not target.startswith("@") and "/" not in target and " " not in target)
        )
        if is_direct_video:
            return fetch_youtube_video_comments(target, limit=limit)

        clean_handle = target.split("youtube.com/")[-1].split("?")[0].strip("/").lstrip("@")
        videos = fetch_youtube_channel_videos(clean_handle, limit=3)
        if not videos:
            return []

        all_comments = []
        seen_texts = set()
        for vid in videos:
            vid_id = vid.get("videoId") or vid.get("id")
            vid_title = vid.get("title") or "Channel Upload"
            vid_url = vid.get("url") or f"https://www.youtube.com/watch?v={vid_id}"
            if not vid_id:
                continue

            raw_comments = fetch_youtube_video_comments(vid_id, limit=limit)
            for c in raw_comments:
                txt = c.get("text") or ""
                if txt and txt not in seen_texts:
                    seen_texts.add(txt)
                    all_comments.append({
                        "id": c.get("id"),
                        "author": c.get("author") or "@viewer",
                        "text": txt,
                        "quote": txt,
                        "likes": c.get("likes") or "0",
                        "published": c.get("published") or "",
                        "videoId": vid_id,
                        "videoTitle": vid_title,
                        "videoUrl": vid_url,
                        "source": f"YouTube (@{clean_handle})",
                    })
                    if len(all_comments) >= limit:
                        break
            if len(all_comments) >= limit:
                break

        return all_comments[:limit]

    # For TikTok or Instagram: if no scraped comments available, return empty list
    return []


def _clean_url(url: str) -> str:
    """Decode JSON/unicode escapes in URLs (e.g. \\u002F → /)."""
    if not url:
        return url
    try:
        import json as _json
        return _json.loads(f'"{url}"')
    except Exception:
        return url.replace("\\u002F", "/").replace("\\u0026", "&").replace("\\/", "/")


def scrape_instagram(handle: str) -> dict:
    """Instagram scrape — uses Apify when key is configured, falls back to direct."""
    from app.config import settings
    handle = handle.lstrip("@").strip()
    url = f"https://www.instagram.com/{handle}/"
    result = {
        "handle": handle, "platform": "instagram", "profile_url": url,
        "display_name": handle, "bio": "", "avatar_url": "",
        "follower_count": 0, "niche": [], "email_public": "",
        "website": "", "social_links": [],
    }

    if settings.APIFY_API_KEY:
        try:
            actor_id = getattr(
                settings,
                "APIFY_INSTAGRAM_EMAIL_ACTOR",
                "scrapers-hub~instagram-profile-email-scraper",
            )
            items = _apify_run(
                actor_id,
                {"usernames": [handle]},
                settings.APIFY_API_KEY,
            )
            if items:
                d = items[0]
                result["display_name"]  = d.get("fullName") or d.get("full_name") or d.get("username") or handle
                result["bio"]           = d.get("biography") or d.get("bio") or ""
                result["follower_count"] = _follower_count(
                    d.get("followersCount") or d.get("followers_count") or d.get("followedBy")
                )
                result["avatar_url"]    = d.get("profilePicUrlHD") or d.get("profilePicUrl") or d.get("profile_pic_url") or ""
                result["website"]       = d.get("externalUrl") or d.get("websiteUrl") or d.get("website") or ""
                result["email_public"] = (
                    d.get("email")
                    or d.get("publicEmail")
                    or d.get("businessEmail")
                    or d.get("contactEmail")
                    or d.get("externalEmail")
                    or d.get("email_public")
                    or ""
                )
            return result
        except Exception as e:
            result["error"] = f"Apify: {e}"

    # Direct fallback (limited — Instagram blocks most requests)
    try:
        r = _http_get(url, timeout=5.0)
        html = r.text
        for pat, key in [
            (r'"edge_followed_by":\{"count":(\d+)\}', "follower_count"),
            (r'"biography":"([^"]*)"', "bio"),
            (r'"full_name":"([^"]*)"', "display_name"),
            (r'"profile_pic_url":"([^"]*)"', "avatar_url"),
            (r'"external_url":"([^"]*)"', "website"),
        ]:
            m = re.search(pat, html)
            if m:
                val = m.group(1).replace("\\/", "/")
                if key == "follower_count":
                    result[key] = int(val)
                else:
                    result[key] = val
    except Exception as e:
        result["error"] = str(e)
    return result


def scrape_tiktok(handle: str) -> dict:
    """TikTok scrape — uses Apify when key is configured, falls back to direct."""
    from app.config import settings
    handle = handle.lstrip("@").strip()
    url = f"https://www.tiktok.com/@{handle}"
    result = {
        "handle": handle, "platform": "tiktok", "profile_url": url,
        "display_name": handle, "bio": "", "avatar_url": "",
        "follower_count": 0, "niche": [], "email_public": "",
        "website": "", "social_links": [],
    }

    if settings.APIFY_API_KEY:
        try:
            items = _apify_run(
                "clockworks/tiktok-profile-scraper",
                {"profiles": [f"https://www.tiktok.com/@{handle}"], "resultsPerPage": 1},
                settings.APIFY_API_KEY,
            )
            if items:
                d = items[0]
                result["display_name"]  = d.get("nickname") or d.get("authorMeta", {}).get("name") or handle
                result["bio"]           = d.get("signature") or d.get("authorMeta", {}).get("signature") or ""
                result["follower_count"] = _follower_count(
                    d.get("followerCount") or d.get("authorMeta", {}).get("fans")
                )
                raw_av = d.get("avatarLarger") or d.get("avatarMedium") or d.get("authorMeta", {}).get("avatar") or ""
                result["avatar_url"]    = _clean_url(raw_av)
                result["email_public"]  = (
                    d.get("email")
                    or d.get("publicEmail")
                    or d.get("authorMeta", {}).get("email")
                    or d.get("authorMeta", {}).get("publicEmail")
                    or ""
                )
            return result
        except Exception as e:
            result["error"] = f"Apify: {e}"

    # Direct fallback
    try:
        r = _http_get(url, timeout=5.0)
        html = r.text
        for pat, key in [
            (r'"followerCount":(\d+)', "follower_count"),
            (r'"desc":"([^"]*)"', "bio"),
            (r'"nickname":"([^"]*)"', "display_name"),
            (r'"avatarLarger":"([^"]*)"', "avatar_url"),
        ]:
            m = re.search(pat, html)
            if m:
                val = m.group(1).replace("\\/", "/")
                if key == "follower_count":
                    result[key] = int(val)
                elif key == "avatar_url":
                    result[key] = _clean_url(val)
                else:
                    result[key] = val
    except Exception as e:
        result["error"] = str(e)
    return result


def scrape_twitter(handle: str) -> dict:
    """Twitter/X scrape — uses Apify when key is configured."""
    from app.config import settings
    handle = handle.lstrip("@").strip()
    url = f"https://x.com/{handle}"
    result = {
        "handle": handle, "platform": "twitter", "profile_url": url,
        "display_name": handle, "bio": "", "avatar_url": "",
        "follower_count": 0, "niche": [], "email_public": "",
        "website": "", "social_links": [],
    }

    if settings.APIFY_API_KEY:
        try:
            items = _apify_run(
                "apify/twitter-scraper",
                {
                    "twitterHandles": [handle],
                    "maxItems": 20,
                    "addUserInfo": True,
                    "includeUserData": True
                },
                settings.APIFY_API_KEY,
            )
            if items:
                item = items[0]
                user = item.get("author") or item.get("user") or (item if item.get("userName") else None)
                if user:
                    username = user.get("userName") or user.get("screen_name") or handle
                    result["display_name"] = user.get("name") or user.get("displayName") or username
                    result["bio"] = user.get("description") or user.get("bio") or ""
                    result["follower_count"] = _follower_count(
                        user.get("followers") or user.get("followersCount")
                    )
                    avatar_raw = user.get("profilePicture") or user.get("profile_image_url_https") or user.get("avatarUrl") or ""
                    result["avatar_url"] = avatar_raw.replace("_normal", "_400x400") if avatar_raw else ""
            return result
        except Exception as e:
            result["error"] = f"Apify: {e}"
    return result


def _extract_contacts_from_text(text: str) -> dict:
    """
    Extract email addresses and Instagram handles from text (bio, description, etc.).
    """
    contacts = {"emails": [], "instagram": None}
    if not text:
        return contacts

    # Extract email addresses from text
    raw_emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    # Filter out obviously invalid / platform notification addresses
    skip_domains = {"example.com", "email.com", "youremail.com", "domain.com", "test.com"}
    for em in raw_emails:
        em_clean = em.strip().lower()
        domain = em_clean.split("@")[-1] if "@" in em_clean else ""
        if domain not in skip_domains and em_clean not in contacts["emails"]:
            contacts["emails"].append(em_clean)

    # Extract Instagram handles
    ig_patterns = [
        r"instagram\.com/([a-zA-Z0-9_.]+)",
        r"(?:^|[\s,|])(?:ig|insta|instagram)\s*[:\-]?\s*@?([a-zA-Z0-9_.]{2,30})(?=[\s,|]|$)",
    ]
    for pat in ig_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            contacts["instagram"] = m.group(1).strip().rstrip("/")
            break

    return contacts


def _direct_youtube_search(query: str, limit: int = 15) -> list[dict]:
    """
    Search YouTube directly via Innertube Search API with fallback (no API key needed).
    Returns real channel titles, handles, actual subscriber counts, avatars, bios, and public emails.
    """
    try:
        url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
        }
        payload = {
            "context": {
                "client": {
                    "hl": "en",
                    "gl": "US",
                    "clientName": "WEB",
                    "clientVersion": "2.20240401.01.00",
                }
            },
            "query": query,
            "params": "EgIQAg%3D%3D",
        }
        r = _http_post(url, json=payload, headers=headers, timeout=3.5)
        if r.status_code == 200:
            data = r.json()
            items = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            tube_results = []
            seen = set()
            for section in items:
                renderers = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in renderers:
                    ch = item.get("channelRenderer")
                    if not ch:
                        continue
                    channel_id = ch.get("channelId", "")
                    title = ch.get("title", {}).get("simpleText") or "".join(r_item.get("text", "") for r_item in ch.get("title", {}).get("runs", []))
                    canonical = ch.get("canonicalBaseUrl", "").lstrip("/").lstrip("@")
                    handle_tag = ch.get("subscriberCountText", {}).get("simpleText") or ""

                    sub_text = ch.get("videoCountText", {}).get("simpleText") or ""
                    if "subscriber" not in sub_text.lower():
                        sub_text = ch.get("subscriberCountText", {}).get("simpleText") or ""
                    sub_count = _num(sub_text)

                    clean_handle = canonical
                    if not clean_handle and handle_tag.startswith("@"):
                        clean_handle = handle_tag.lstrip("@")
                    if not clean_handle and title:
                        clean_handle = re.sub(r"[^a-zA-Z0-9_]", "", title.lower())
                    if not clean_handle:
                        clean_handle = channel_id

                    if not clean_handle or clean_handle.lower() in seen:
                        continue
                    seen.add(clean_handle.lower())

                    thumbs = ch.get("thumbnail", {}).get("thumbnails", [])
                    avatar = thumbs[-1].get("url") if thumbs else ""
                    if avatar.startswith("//"):
                        avatar = "https:" + avatar

                    desc = "".join(r_item.get("text", "") for r_item in ch.get("descriptionSnippet", {}).get("runs", []))
                    contacts = _extract_contacts_from_text(desc)

                    tube_results.append({
                        "handle": clean_handle,
                        "channel_id": channel_id,
                        "platform": "youtube",
                        "display_name": title or clean_handle,
                        "bio": desc,
                        "follower_count": sub_count,
                        "avatar_url": avatar,
                        "email_public": contacts["emails"][0] if contacts["emails"] else "",
                        "instagram": contacts["instagram"] or "",
                        "profile_url": f"https://www.youtube.com/@{clean_handle}",
                    })
                    if len(tube_results) >= limit:
                        break
                if len(tube_results) >= limit:
                    break
            if tube_results:
                return tube_results
    except Exception as e:
        logger.debug(f"[YouTube Search] Innertube search error: {e}")

    # Fallback to direct HTML search if Innertube didn't return
    import urllib.parse
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp=EgIQAg%3D%3D"
    try:
        r = _http_get(search_url, timeout=3.5)
        if r.status_code == 200:
            m = re.search(r"var ytInitialData\s*=\s*(\{.+?\});\s*(?:var|</script)", r.text, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                contents = (
                    data.get("contents", {})
                    .get("twoColumnSearchResultsRenderer", {})
                    .get("primaryContents", {})
                    .get("sectionListRenderer", {})
                    .get("contents", [])
                )
                html_results = []
                seen_html = set()
                for section in contents:
                    items = section.get("itemSectionRenderer", {}).get("contents", [])
                    for item in items:
                        renderer = item.get("channelRenderer")
                        if not renderer:
                            continue
                        channel_id = renderer.get("channelId", "")
                        canonical = renderer.get("canonicalBaseUrl", "").lstrip("/").lstrip("@")
                        handle = canonical or channel_id
                        if not handle or handle.lower() in seen_html:
                            continue
                        seen_html.add(handle.lower())

                        title_obj = renderer.get("title", {})
                        display_name = title_obj.get("simpleText", "") or "".join(r_item.get("text", "") for r_item in title_obj.get("runs", [])) or handle

                        sub_obj = renderer.get("subscriberCountText", {})
                        sub_text = sub_obj.get("simpleText", "") or "".join(r_item.get("text", "") for r_item in sub_obj.get("runs", []))
                        subs = _num(sub_text)

                        desc_obj = renderer.get("descriptionSnippet", {})
                        desc_text = "".join(r_item.get("text", "") for r_item in desc_obj.get("runs", []))
                        contacts = _extract_contacts_from_text(desc_text)

                        thumbs = renderer.get("thumbnail", {}).get("thumbnails", [])
                        avatar_url = thumbs[-1].get("url", "") if thumbs else ""
                        if avatar_url.startswith("//"):
                            avatar_url = "https:" + avatar_url

                        html_results.append({
                            "handle": handle,
                            "channel_id": channel_id,
                            "platform": "youtube",
                            "display_name": display_name,
                            "bio": desc_text,
                            "follower_count": subs,
                            "avatar_url": avatar_url,
                            "email_public": contacts["emails"][0] if contacts["emails"] else "",
                            "instagram": contacts["instagram"] or "",
                            "profile_url": f"https://www.youtube.com/@{handle}",
                        })
                        if len(html_results) >= limit:
                            break
                    if len(html_results) >= limit:
                        break
                return html_results
    except Exception as e:
        logger.debug(f"[YouTube Search] HTML fallback error: {e}")

    return []


NICHE_SEARCH_EXPANSIONS = {
    "tech": ["Software Engineering", "Full Stack Developer", "Python AI Apps", "Web Development", "Tech Setup"],
    "fitness": ["Fitness Coach Workout", "Bodybuilding Science", "Nutrition Diet", "Strength Training"],
    "finance": ["Personal Finance Investing", "SaaS Business", "Stock Market Trading", "Financial Freedom"],
    "business": ["Startup Founder Journey", "Solo Founder SaaS", "Marketing Growth Hacks", "Digital Agency"],
    "creator": ["Content Creation Workflow", "Video Editing", "Podcast Production", "YouTube Strategy"],
    "gaming": ["Game Development", "Indie Game Studio", "Gaming Hardware Setup"],
    "design": ["UI UX Design Figma", "Webflow Website Design", "Product Design Systems"],
}


def search_youtube_channels(query: str, limit: int = 5, min_followers: int = 0, max_followers: int = 0, exclude_handles: set = None) -> list[dict]:
    """
    Search YouTube for creators matching niche keywords.
    Ultra-fast, lightweight single-hop query without nested scraping or CPU load.
    Filters out any creators in exclude_handles so previously discovered creators are not scouted again.
    """
    exclude_set = {str(h).lstrip("@").strip().lower() for h in (exclude_handles or []) if h}
    results = []
    seen = set()

    clean_q = query.strip()
    search_queries = [f"{clean_q} channel", clean_q]

    for sq in search_queries:
        found = _direct_youtube_search(sq, limit=max(limit * 2, 12))
        for ch in found:
            h = str(ch.get("handle", "")).lstrip("@").strip()
            h_lower = h.lower()
            if not h or h_lower in seen or h_lower in exclude_set:
                continue
            seen.add(h_lower)

            subs = int(ch.get("follower_count", 0) or 0)
            if min_followers and subs > 0 and subs < min_followers:
                continue
            if max_followers and subs > max_followers:
                continue

            results.append({
                "handle": h,
                "platform": "youtube",
                "display_name": str(ch.get("display_name") or h).lstrip("@").strip(),
                "bio": ch.get("bio", ""),
                "follower_count": subs,
                "avatar_url": ch.get("avatar_url", ""),
                "email_public": ch.get("email_public", ""),
                "instagram": str(ch.get("instagram", "")).lstrip("@").strip(),
                "website": ch.get("website", ""),
                "website_url": ch.get("website_url", "") or ch.get("website", ""),
                "profile_url": ch.get("profile_url") or f"https://www.youtube.com/@{h}",
                "niche": [clean_q],
            })
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    return results[:limit]


def scrape_profile(platform: str, handle: str, api_key: str = None, apify_token: str = None) -> dict:
    """
    Dispatch to the configured Apify scraper, with direct public-page fallback.
    """
    from app.config import settings
    token = apify_token or settings.APIFY_API_KEY
    clean_h = handle.lstrip("@").strip().strip("/")
    p = platform.lower().strip()

    # If full URL passed, extract handle & platform
    if "youtube.com/" in handle:
        p = "youtube"
        m = re.search(r"youtube\.com/(channel/[^/?&\s]+|c/[^/?&\s]+|user/[^/?&\s]+|@[^/?&\s]+|[^/?&\s]+)", handle)
        if m:
            matched = m.group(1).lstrip("@")
            clean_h = matched.split("channel/")[1] if "channel/" in matched else matched
    elif "instagram.com/" in handle:
        p = "instagram"
        m = re.search(r"instagram\.com/([^/?&\s]+)", handle)
        if m:
            clean_h = m.group(1)
    elif "tiktok.com/" in handle:
        p = "tiktok"
        m = re.search(r"tiktok\.com/(@?[^/?&\s]+)", handle)
        if m:
            clean_h = m.group(1).lstrip("@")
    elif "twitter.com/" in handle or "x.com/" in handle:
        p = "twitter"
        m = re.search(r"(?:twitter|x)\.com/([^/?&\s]+)", handle)
        if m:
            clean_h = m.group(1)

    result = None

    # 1. YouTube: Fast Direct Innertube Scraper (0.8s vs 60s Apify)
    if p == "youtube":
        try:
            yt_res = scrape_youtube(clean_h, deep=True)
            if yt_res and not yt_res.get("error") and (yt_res.get("follower_count", 0) > 0 or yt_res.get("avatar_url")):
                result = yt_res
        except Exception as yt_err:
            logger.debug(f"[Direct YouTube Scrape] Fallback to Apify: {yt_err}")

    # 2. Apify Actors for Platforms (TikTok, Instagram, Twitter, and YouTube fallback)
    if not result and token:
        try:
            if p == "youtube":
                res = apify_scrape_youtube_channels([clean_h], token, timeout_secs=45)
                if res:
                    result = res[0]
            elif p == "instagram":
                res = apify_scrape_instagram_profiles([clean_h], token, timeout_secs=60)
                if res:
                    result = res[0]
            elif p == "tiktok":
                res = apify_scrape_tiktok_profiles([clean_h], token, timeout_secs=60)
                if res:
                    result = res[0]
            elif p == "twitter":
                res = _apify_run("apify~twitter-scraper", {"twitterHandles": [clean_h], "maxItems": 1}, token, timeout_secs=45)
                if res and len(res) > 0:
                    item = res[0]
                    user = item.get("author") or item.get("user") or item
                    bio = user.get("description") or user.get("bio") or ""
                    email_pub = (
                        user.get("email")
                        or user.get("publicEmail")
                        or item.get("email")
                        or ""
                    )
                    result = {
                        "handle": clean_h,
                        "platform": "twitter",
                        "display_name": user.get("name") or clean_h,
                        "bio": bio,
                        "follower_count": _follower_count(_first_value(
                            user, "followers", "followersCount", "followerCount", "followers_count"
                        )),
                        "avatar_url": user.get("profilePicture") or user.get("profile_image_url_https") or "",
                        "email_public": email_pub,
                        "profile_url": f"https://x.com/{clean_h}",
                        "raw_apify_data": item,
                    }
        except Exception as e:
            logger.warning(f"[Apify Scraper] Fallback to direct scrapers for {p} @{clean_h}: {e}")

    # 3. Direct Fallback Scrapers if Apify didn't yield a result
    if not result:
        if p == "youtube":
            result = scrape_youtube(clean_h, deep=True)
        elif p == "instagram":
            result = scrape_instagram(clean_h)
        elif p == "tiktok":
            result = scrape_tiktok(clean_h)
        elif p == "twitter":
            result = scrape_twitter(clean_h)
        else:
            return {"error": f"No scraper for platform: {p}", "handle": clean_h, "platform": p}

    # 4. Smart Email Enrichment: If email is missing, query Hunter.io
    if result and not result.get("error") and not result.get("email_public"):
        try:
            from app.integrations.hunter import hunter
            h_res = hunter.smart_find_for_creator(
                creator_name=result.get("display_name") or clean_h,
                handle=clean_h,
                website_url=result.get("website"),
                bio=result.get("bio"),
            )
            if h_res.get("success") and h_res.get("email"):
                result["email_public"] = h_res["email"]
                result["email_score"] = h_res.get("score")
                result["email_status"] = h_res.get("verification_status", "valid")
        except Exception as h_err:
            logger.debug(f"[Scrape Profile Hunter Fallback] Error: {h_err}")

    return result or {"error": f"Scrape failed for @{clean_h}", "handle": clean_h, "platform": p}


