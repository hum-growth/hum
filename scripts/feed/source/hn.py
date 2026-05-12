#!/usr/bin/env python3
"""
hn.py — Hacker News feed source via Algolia HN Search API.

Two-stage filter:
  1. Cheap pre-filter — points >= HN_MIN_POINTS AND topic matches CONTENT.md
  2. Cap survivors at HN_MAX_ITEMS, then fetch underlying article body via trafilatura

Comment enrichment runs on the first ENRICH_LIMIT survivors.

No API key needed — Algolia HN search is publicly accessible.

Usage:
    python3 -m feed.source.hn                        # front page + Show HN
    python3 -m feed.source.hn --type front_page     # front page only
    python3 -m feed.source.hn --type show_hn        # Show HN only
    python3 -m feed.source.hn --output /tmp/hn_feed.json
"""

import argparse
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from typing import Any
import urllib.request
import urllib.error

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from lib.atomic_io import atomic_write_json

from config import load_config
from feed.source.x import classify
from feed.source.handlers.common import extract_article

_CFG = load_config()

ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
ALGOLIA_SEARCH_URL = f"{ALGOLIA_BASE}/search"
ALGOLIA_ITEM_URL = f"{ALGOLIA_BASE}/items"

# Enrich top N stories with comments
ENRICH_LIMIT = 5

# Defaults for the viral+relevant pre-filter and article fetch.
# Overridable via preferences.json → "hn" block.
DEFAULT_MIN_POINTS = 30
DEFAULT_MAX_ITEMS = 20
DEFAULT_REQUIRE_TOPIC_MATCH = True
DEFAULT_FETCH_ARTICLE_BODY = True
ARTICLE_TEXT_MAX_CHARS = 5000
ARTICLE_EXCERPT_CHARS = 280


def _load_hn_prefs() -> dict:
    """Load the 'hn' block from preferences.json with defaults."""
    prefs_file = Path(_CFG["feed_assets"]) / "preferences.json"
    hn_prefs: dict = {}
    if prefs_file.exists():
        try:
            with open(prefs_file) as f:
                hn_prefs = (json.load(f) or {}).get("hn", {}) or {}
        except (json.JSONDecodeError, OSError):
            hn_prefs = {}
    return {
        "min_points": int(hn_prefs.get("min_points", DEFAULT_MIN_POINTS)),
        "max_items_per_run": int(hn_prefs.get("max_items_per_run", DEFAULT_MAX_ITEMS)),
        "require_topic_match": bool(hn_prefs.get("require_topic_match", DEFAULT_REQUIRE_TOPIC_MATCH)),
        "fetch_article_body": bool(hn_prefs.get("fetch_article_body", DEFAULT_FETCH_ARTICLE_BODY)),
    }


def _is_hn_self_url(url: str) -> bool:
    """True if the URL points to HN itself (Show HN / Ask HN with no external link)."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return host.endswith("ycombinator.com")


def _fetch_article_body(item: dict) -> tuple[str | None, str | None]:
    """Return (article_text, article_excerpt) for an item, or (None, None) on failure."""
    url = item.get("url", "")
    if not url or _is_hn_self_url(url):
        return None, None
    try:
        text = extract_article(url, source_key="hn", download_images=False)
    except Exception:
        return None, None
    if not text:
        return None, None
    truncated = text[:ARTICLE_TEXT_MAX_CHARS]
    excerpt_src = re.sub(r"\s+", " ", text).strip()
    excerpt = excerpt_src[:ARTICLE_EXCERPT_CHARS].rstrip()
    if len(excerpt_src) > ARTICLE_EXCERPT_CHARS:
        excerpt += "…"
    return truncated, excerpt


def enrich_with_articles(items: list[dict], max_workers: int = 5) -> list[dict]:
    """Fetch underlying article body for each item in parallel."""
    if not items:
        return items
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_article_body, item): idx for idx, item in enumerate(items)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                text, excerpt = future.result(timeout=30)
            except Exception:
                text, excerpt = None, None
            items[idx]["article_text"] = text
            items[idx]["article_excerpt"] = excerpt
    return items


def _get(url: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _strip_html(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r'<p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def fetch_algolia(tag: str, hits_per_page: int = 30, days_back: int = 7, min_points: int = DEFAULT_MIN_POINTS) -> list[dict]:
    """Fetch stories from Algolia, filtered by date and minimum engagement (points >= min_points)."""
    since = int(time.time()) - (days_back * 86400)
    params = {
        "tags": tag,
        "hitsPerPage": hits_per_page,
        "numericFilters": f"created_at_i>{since},points>={min_points}",
    }
    url = f"{ALGOLIA_SEARCH_URL}?{urlencode(params)}"
    try:
        return _get(url).get("hits", [])
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        # Fallback without numeric filter, client-side date + points filter
        fallback_url = f"{ALGOLIA_SEARCH_URL}?tags={tag}&hitsPerPage={hits_per_page}"
        try:
            hits = _get(fallback_url).get("hits", [])
            cutoff = int(time.time()) - (days_back * 86400)
            return [h for h in hits if h.get("created_at_i", 0) > cutoff and (h.get("points") or 0) >= min_points]
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            print(f"[hn] Error fetching {tag}: {exc}", file=sys.stderr)
            return []


def _fetch_comments(object_id: str, max_comments: int = 5) -> dict[str, Any]:
    """Fetch top-level comments for a story, sorted by points."""
    try:
        data = _get(f"{ALGOLIA_ITEM_URL}/{object_id}", timeout=15)
    except Exception:
        return {"comments": [], "comment_insights": []}

    children = [c for c in data.get("children", []) if c.get("text") and c.get("author")]
    children.sort(key=lambda c: c.get("points") or 0, reverse=True)

    comments = []
    insights = []
    for c in children[:max_comments]:
        text = _strip_html(c.get("text", ""))
        excerpt = text[:300] + "..." if len(text) > 300 else text
        comments.append({
            "author": c.get("author", ""),
            "text": excerpt,
            "points": c.get("points") or 0,
        })
        first = text.split(". ")[0].split("\n")[0][:200]
        if first:
            insights.append(first)

    return {"comments": comments, "comment_insights": insights}


def enrich_top_stories(items: list[dict], limit: int = ENRICH_LIMIT) -> list[dict]:
    """Fetch comments for top N stories by points, in parallel."""
    if not items:
        return items

    by_points = sorted(range(len(items)), key=lambda i: items[i].get("likes", 0), reverse=True)
    to_enrich = by_points[:limit]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_comments, items[i]["object_id"]): i for i in to_enrich}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result(timeout=15)
                items[idx]["top_comments"] = result["comments"]
                items[idx]["comment_insights"] = result["comment_insights"]
            except Exception:
                items[idx]["top_comments"] = []
                items[idx]["comment_insights"] = []

    return items


def parse_story(hit: dict, story_type: str) -> dict | None:
    url = hit.get("url", "")
    object_id = hit.get("objectID", "")
    if not url:
        url = f"https://news.ycombinator.com/item?id={object_id}"

    title = hit.get("title", "") or hit.get("story_text", "")[:100]
    if not title:
        return None

    text = hit.get("comment_text", "") or ""
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

    topics = classify(f"{title}\n{text[:500]}")

    return {
        "source": "hn",
        "author": f"@{author}" if author else "@hn",
        "display_name": author or "Hacker News",
        "content": (text or title)[:500],
        "post_type": "story",
        "title": title,
        "url": url,
        "discussion_url": f"https://news.ycombinator.com/item?id={object_id}",
        "topics": topics,
        "timestamp": created,
        "likes": points,
        "replies": num_comments,
        "views": points * 100,
        "object_id": object_id,
        "top_comments": [],
        "comment_insights": [],
        "article_text": None,
        "article_excerpt": None,
    }


def fetch_hn(story_type: str = "both", hits_per_page: int = 30, days_back: int = 7) -> list[dict]:
    """Fetch HN stories, apply viral+relevant pre-filter, then enrich survivors with article body and comments."""
    hn_prefs = _load_hn_prefs()
    min_points = hn_prefs["min_points"]
    max_items = hn_prefs["max_items_per_run"]
    require_topic_match = hn_prefs["require_topic_match"]
    fetch_articles = hn_prefs["fetch_article_body"]

    items: list[dict] = []

    if story_type in ("front_page", "both"):
        for hit in fetch_algolia("front_page", hits_per_page, days_back, min_points):
            item = parse_story(hit, "front_page")
            if item:
                items.append(item)

    if story_type in ("show_hn", "both"):
        for hit in fetch_algolia("show_hn", hits_per_page, days_back, min_points):
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

    pre_filter_count = len(unique)

    # Stage 1b: relevance gate — drop items with no topic match against CONTENT.md.
    # Show HN posts may have empty title classifications; keep them if their story body
    # (already classified into post["topics"]) hits a pillar.
    if require_topic_match:
        unique = [item for item in unique if item.get("topics")]

    # Sort by points and cap
    unique.sort(key=lambda x: x.get("likes", 0) or 0, reverse=True)
    unique = unique[:max_items]

    print(
        f"[hn] Pre-filter: {pre_filter_count} → {len(unique)} survivors "
        f"(min_points={min_points}, require_topic_match={require_topic_match}, cap={max_items})",
        file=sys.stderr,
    )

    # Stage 2: fetch underlying article body for each survivor
    if fetch_articles:
        unique = enrich_with_articles(unique)

    # Enrich top stories with comments
    unique = enrich_top_stories(unique)

    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Hacker News stories via Algolia API")
    parser.add_argument("--type", choices=["front_page", "show_hn", "both"], default="both")
    parser.add_argument("--hits-per-page", type=int, default=30)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--output", default=str(_CFG["feed_raw"] / "hn_feed.json"))
    args = parser.parse_args()

    print(f"[HN] Fetching — {args.type}, last {args.days} days...", file=sys.stderr)
    items = fetch_hn(args.type, args.hits_per_page, args.days)
    print(f"[HN] Got {len(items)} stories (top {ENRICH_LIMIT} enriched with comments)", file=sys.stderr)

    output_path = Path(args.output)
    atomic_write_json(output_path, items)
    print(json.dumps(items, indent=2))


if __name__ == "__main__":
    main()
