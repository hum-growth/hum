#!/usr/bin/env python3
"""
scrapecreators.py — ScrapeCreators API client for X profiles and LinkedIn profiles.

Endpoints used:
  - GET /v1/twitter/user-tweets?handle=X       — user's tweets (~100 most popular)
  - GET /v1/twitter/tweet?url=X&trim=false      — single tweet detail (for threads)
  - GET /v1/linkedin/profile?url=X              — profile + recentPosts + activity
  - GET /v1/linkedin/post?url=X                 — single post/article detail

All endpoints cost 1 credit per request.
Auth: x-api-key header.

Usage:
    python3 -m feed.source.scrapecreators x-profile <handle> [--limit 20] [--since ISO]
    python3 -m feed.source.scrapecreators x-thread <tweet_url>
    python3 -m feed.source.scrapecreators linkedin-profile <url> [--since ISO]
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config

BASE_URL = "https://api.scrapecreators.com"


def _api_get(path: str, params: dict, api_key: str) -> dict:
    """Make a GET request to ScrapeCreators API with retry/backoff on 429 and 5xx."""
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE_URL}{path}?{qs}" if qs else f"{BASE_URL}{path}"
    for attempt in range(3):
        req = Request(url, headers={"x-api-key": api_key})
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            body = e.read().decode() if e.fp else ""
            return {"error": f"HTTP {e.code}: {body[:200]}", "status": e.code}


def _parse_x_timestamp(raw: str) -> str | None:
    """Parse Twitter's created_at format to ISO 8601."""
    # Twitter format: "Wed Oct 10 20:19:24 +0000 2018"
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        return dt.isoformat()
    except (ValueError, TypeError):
        return raw if raw else None


def _normalize_x_tweet(tweet: dict) -> dict:
    """Normalize a ScrapeCreators tweet object to match the hum feed schema."""
    legacy = tweet.get("legacy", {})
    user = (tweet.get("core", {}).get("user_results", {})
            .get("result", {}).get("legacy", {}))
    views = tweet.get("views", {})
    rest_id = tweet.get("rest_id", "")
    handle = user.get("screen_name", "")

    view_count = views.get("count")
    if view_count is not None:
        try:
            view_count = int(view_count)
        except (ValueError, TypeError):
            view_count = None

    return {
        "author": f"@{handle}" if handle else "",
        "display_name": user.get("name", ""),
        "text": legacy.get("full_text", ""),
        "likes": legacy.get("favorite_count") or 0,
        "retweets": legacy.get("retweet_count") or 0,
        "replies": legacy.get("reply_count") or 0,
        "views": view_count,
        "url": tweet.get("url") or f"https://x.com/{handle}/status/{rest_id}",
        "timestamp": _parse_x_timestamp(legacy.get("created_at", "")),
        "source": "x_profile",
        "media": _extract_x_media(legacy),
    }


def _extract_x_media(legacy: dict) -> list:
    """Extract media items from tweet legacy data."""
    media_items = []
    for m in legacy.get("entities", {}).get("media", []):
        media_type = m.get("type", "photo")
        if media_type == "photo":
            media_items.append({
                "type": "image",
                "url": m.get("media_url_https", ""),
                "alt_text": m.get("ext_alt_text"),
                "thumbnail_url": None,
            })
        elif media_type in ("video", "animated_gif"):
            media_items.append({
                "type": "video" if media_type == "video" else "gif",
                "url": m.get("media_url_https", ""),
                "alt_text": None,
                "thumbnail_url": m.get("media_url_https"),
            })
    # Also check extended_entities for better media data
    for m in legacy.get("extended_entities", {}).get("media", []):
        if m.get("type") in ("video", "animated_gif"):
            variants = m.get("video_info", {}).get("variants", [])
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                # Update the matching media item
                for item in media_items:
                    if item["thumbnail_url"] == m.get("media_url_https"):
                        item["url"] = best["url"]
                        break
    return media_items


# ── X Profile Tweets ──────────────────────────────────────────────────────

def fetch_x_profile_tweets(
    handle: str,
    api_key: str,
    since: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Fetch tweets from an X profile via ScrapeCreators API.

    Args:
        handle: X handle (without @)
        api_key: ScrapeCreators API key
        since: ISO timestamp — only return tweets newer than this
        limit: Max tweets to return (default 20 for first crawl)

    Returns:
        List of normalized feed items
    """
    data = _api_get("/v1/twitter/user-tweets", {"handle": handle}, api_key)
    if "error" in data:
        print(f"  Error fetching @{handle}: {data['error']}", file=sys.stderr)
        return []

    tweets = data.get("tweets", [])
    items = [_normalize_x_tweet(t) for t in tweets]

    # Filter by since timestamp if provided
    if since:
        since_dt = datetime.fromisoformat(since)
        filtered = []
        for item in items:
            ts = item.get("timestamp")
            if ts:
                try:
                    item_dt = datetime.fromisoformat(ts)
                    # Make both offset-aware or offset-naive for comparison
                    if since_dt.tzinfo and not item_dt.tzinfo:
                        continue
                    if not since_dt.tzinfo and item_dt.tzinfo:
                        item_dt = item_dt.replace(tzinfo=None)
                    if item_dt > since_dt:
                        filtered.append(item)
                except ValueError:
                    filtered.append(item)
            else:
                filtered.append(item)
        items = filtered

    # Sort by timestamp descending, take top N
    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return items[:limit]


# ── X Thread ──────────────────────────────────────────────────────────────

def fetch_x_thread(tweet_url: str, api_key: str) -> dict:
    """Fetch a single tweet (thread entry point) via ScrapeCreators API.

    Returns the full tweet detail. For thread traversal, caller should
    follow conversation_id and in_reply_to chains.
    """
    data = _api_get("/v1/twitter/tweet", {"url": tweet_url, "trim": "false"}, api_key)
    if "error" in data:
        return {"error": data["error"]}
    return _normalize_x_tweet(data)


# ── LinkedIn Profile ──────────────────────────────────────────────────────

def _normalize_linkedin_post(post: dict, profile_name: str = "") -> dict:
    """Normalize a LinkedIn recentPost/activity item to hum feed schema."""
    return {
        "author": profile_name,
        "display_name": profile_name,
        "text": post.get("title") or post.get("text") or "",
        "likes": post.get("reactionCount") or post.get("likeCount") or 0,
        "retweets": 0,
        "replies": post.get("commentCount") or 0,
        "views": 0,
        "url": post.get("link") or post.get("url") or "",
        "timestamp": post.get("datePublished") or post.get("date") or "",
        "source": "linkedin_profile",
        "media": [],
        "activity_type": post.get("activityType", ""),
    }


def fetch_linkedin_profile_posts(
    profile_url: str,
    api_key: str,
    since: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Fetch recent posts from a LinkedIn profile via ScrapeCreators API.

    Args:
        profile_url: LinkedIn profile URL (e.g. https://www.linkedin.com/in/someone/)
        api_key: ScrapeCreators API key
        since: ISO date string — only return posts newer than this
        limit: Max posts to return

    Returns:
        List of normalized feed items
    """
    data = _api_get("/v1/linkedin/profile", {"url": profile_url}, api_key)
    if "error" in data:
        print(f"  Error fetching {profile_url}: {data['error']}", file=sys.stderr)
        return []

    profile_name = data.get("name", "")
    items = []

    # Collect from recentPosts
    for post in data.get("recentPosts", []):
        items.append(_normalize_linkedin_post(post, profile_name))

    # Collect from activity
    for post in data.get("activity", []):
        items.append(_normalize_linkedin_post(post, profile_name))

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for item in items:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)
        elif not url:
            unique.append(item)
    items = unique

    # Filter by since date if provided
    if since:
        filtered = []
        for item in items:
            ts = item.get("timestamp", "")
            if ts and ts > since:
                filtered.append(item)
            elif not ts:
                filtered.append(item)
        items = filtered

    return items[:limit]


# ── LinkedIn Post Detail ──────────────────────────────────────────────────

def fetch_linkedin_post(post_url: str, api_key: str) -> dict:
    """Fetch a single LinkedIn post/article detail."""
    data = _api_get("/v1/linkedin/post", {"url": post_url}, api_key)
    if "error" in data:
        return {"error": data["error"]}

    return {
        "author": data.get("name") or (data.get("author", {}).get("name", "")),
        "display_name": data.get("name", ""),
        "text": data.get("description") or data.get("headline") or "",
        "likes": data.get("likeCount") or 0,
        "replies": data.get("commentCount") or 0,
        "retweets": 0,
        "views": 0,
        "url": data.get("url", post_url),
        "timestamp": data.get("datePublished", ""),
        "source": "linkedin_profile",
        "media": [],
        "comments": data.get("comments", []),
    }


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ScrapeCreators API client for hum feed")
    sub = parser.add_subparsers(dest="command")

    xp = sub.add_parser("x-profile", help="Fetch tweets from an X profile")
    xp.add_argument("handle", help="X handle (without @)")
    xp.add_argument("--limit", type=int, default=20)
    xp.add_argument("--since", default=None, help="ISO timestamp — only newer tweets")

    xt = sub.add_parser("x-thread", help="Fetch a single tweet/thread")
    xt.add_argument("url", help="Tweet URL")

    lp = sub.add_parser("linkedin-profile", help="Fetch posts from a LinkedIn profile")
    lp.add_argument("url", help="LinkedIn profile URL")
    lp.add_argument("--limit", type=int, default=20)
    lp.add_argument("--since", default=None, help="ISO date — only newer posts")

    lpost = sub.add_parser("linkedin-post", help="Fetch a single LinkedIn post")
    lpost.add_argument("url", help="LinkedIn post URL")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cfg = load_config()
    api_key = cfg.get("scrapecreators_api_key")
    if not api_key:
        print("Error: SCRAPECREATORS_API_KEY not set in env or openclaw.json", file=sys.stderr)
        sys.exit(1)

    if args.command == "x-profile":
        result = fetch_x_profile_tweets(args.handle, api_key, args.since, args.limit)
    elif args.command == "x-thread":
        result = fetch_x_thread(args.url, api_key)
    elif args.command == "linkedin-profile":
        result = fetch_linkedin_profile_posts(args.url, api_key, args.since, args.limit)
    elif args.command == "linkedin-post":
        result = fetch_linkedin_post(args.url, api_key)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
