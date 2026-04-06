#!/usr/bin/env python3
"""
hn.py — Hacker News feed source via Algolia HN Search API.

Fetches:
  - Front page stories (tags=front_page)
  - Show HN stories (tags=show_hn)

No API key needed — Algolia HN search is publicly accessible.

Usage:
    python3 -m feed.source.hn                        # front page + Show HN
    python3 -m feed.source.hn --type front_page     # front page only
    python3 -m feed.source.hn --type show_hn        # Show HN only
    python3 -m feed.source.hn --output /tmp/hn_feed.json
"""

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config
from feed.source.x import classify

_CFG = load_config()

ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


def fetch_algolia(tag: str, hits_per_page: int = 30, days_back: int = 7) -> list[dict]:
    """Fetch stories from Algolia HN API, filtered to recent N days."""
    import time
    since = int(time.time()) - (days_back * 86400)
    # Use numeric filter on created_at_i (Unix epoch)
    url = (
        f"{ALGOLIA_BASE}/search"
        f"?tags={tag}&hitsPerPage={hits_per_page}"
        f"&numericFilters=created_at_i%3E{since}"
        f"&sortBy=points&Ranking=true"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get("hits", [])
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        # Fallback without numeric filter
        url_fallback = f"{ALGOLIA_BASE}/search?tags={tag}&hitsPerPage={hits_per_page}"
        try:
            with urllib.request.urlopen(url_fallback, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                hits = data.get("hits", [])
                # Client-side filter by date
                import time
                cutoff = int(time.time()) - (days_back * 86400)
                return [h for h in hits if h.get("created_at_i", 0) > cutoff]
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc2:
            print(f"[hn] Error fetching {tag}: {exc2}", file=sys.stderr)
            return []


def parse_story(hit: dict, story_type: str) -> dict | None:
    """Transform an Algolia hit into a feed item."""
    url = hit.get("url", "")
    if not url:
        # Self-post or Ask HN — use the discussion link
        url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

    title = hit.get("title", "") or hit.get("story_text", "")[:100]
    if not title:
        return None

    text = hit.get("comment_text", "") or ""
    # For Ask HN / Show HN, the title is the hook
    if story_type == "show_hn" and not text:
        text = title

    author = hit.get("author", "unknown")
    points = hit.get("points", 0) or 0
    num_comments = hit.get("num_comments", 0) or 0
    created = hit.get("created_at", "")
    try:
        date_str = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        date_str = created[:10] if created else ""

    # Classify topics from title + text
    combined = f"{title}\n{text[:500]}"
    topics = classify(combined)

    return {
        "source": "hn",
        "author": f"@{author}" if author else "@hn",
        "display_name": author or "Hacker News",
        "text": (text or title)[:500],
        "title": title,
        "url": url,
        "discussion_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
        "topics": topics,
        "timestamp": created,
        "date": date_str,
        "likes": points,
        "replies": num_comments,
        "views": points * 100,  # HN votes are a rough proxy; no view count in API
        "story_type": story_type,
        "object_id": hit.get("objectID", ""),
    }


def fetch_hn(story_type: str = "both", hits_per_page: int = 30, days_back: int = 7) -> list[dict]:
    """Fetch HN stories and return feed items sorted by points."""
    items: list[dict] = []

    if story_type in ("front_page", "both"):
        hits = fetch_algolia("front_page", hits_per_page, days_back)
        for hit in hits:
            item = parse_story(hit, "front_page")
            if item:
                items.append(item)

    if story_type in ("show_hn", "both"):
        hits = fetch_algolia("show_hn", hits_per_page, days_back)
        for hit in hits:
            item = parse_story(hit, "show_hn")
            if item:
                items.append(item)

    # Deduplicate by object ID
    seen: set = set()
    unique: list[dict] = []
    for item in items:
        oid = item.get("object_id", "")
        if oid and oid not in seen:
            seen.add(oid)
            unique.append(item)

    # Sort by likes (points) descending
    unique.sort(key=lambda x: x.get("likes", 0) or 0, reverse=True)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Hacker News stories via Algolia API")
    parser.add_argument(
        "--type",
        choices=["front_page", "show_hn", "both"],
        default="both",
        help="Which HN feed to fetch (default: both)",
    )
    parser.add_argument(
        "--hits-per-page", type=int, default=30,
        help="Max stories per feed (default: 30)",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Only fetch stories from the last N days (default: 7)",
    )
    parser.add_argument(
        "--output",
        default=str(_CFG["feed_dir"] / "hn_feed.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    print(f"[HN] Fetching Algolia HN API — {args.type}, last {args.days} days...", file=sys.stderr)
    items = fetch_hn(args.type, args.hits_per_page, args.days)
    print(f"[HN] Got {len(items)} stories", file=sys.stderr)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    print(json.dumps(items, indent=2))


if __name__ == "__main__":
    main()
