#!/usr/bin/env python3
"""
linkedin.py — LinkedIn profile post scraping via browser automation.

Generates browser automation instructions for extracting posts from LinkedIn
profiles. The actual scraping is performed by the agent using the browser tool.

Usage:
    python3 -m feed.source.linkedin profile <handle> [--output PATH] [--scrolls N]
    python3 -m feed.source.linkedin profiles --all [--scrolls N]

Architecture:
    - Profiles are stored in sources.json under type "linkedin_profile"
    - refresh.py calls this module to emit browser instructions per profile
    - The agent executes the instructions using the browser tool
    - Deduplication is handled by the caller (refresh.py) using checkpoints
"""
import argparse
import json
import sys
import re
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config, load_topics

_CFG = load_config()


def get_topics() -> dict[str, list[str]]:
    if not hasattr(get_topics, "_cache"):
        get_topics._cache = load_topics(_CFG["data_dir"])
    return get_topics._cache


def classify(text: str) -> list[str]:
    topics = get_topics()
    lower = text.lower()
    return [t for t, kws in topics.items() if any(kw in lower for kw in kws)]


def extract_handle_from_url(url: str) -> str:
    """Extract the username/vanity from a LinkedIn profile URL."""
    # https://www.linkedin.com/in/andersliulindberg/ → andersliulindberg
    m = re.search(r"/in/([^/]+)", url)
    return m.group(1) if m else url.rstrip("/").split("/")[-1]


LINKEDIN_POST_SCHEMA = {
    "author": "@handle (vanity name from profile URL)",
    "display_name": "full name",
    "text": "full post text — click 'see more' on every post that is truncated before reading",
    "likes": "integer or null",
    "replies": "integer or null",
    "views": "integer or null",
    "url": "full LinkedIn post URL (https://www.linkedin.com/posts/...)",
    "timestamp": "ISO 8601 date string or relative time (e.g. '3 days ago', '1w ago')",
    "media": [
        {
            "type": "image | video | document",
            "url": "direct URL to the media",
            "alt_text": "alt text if present, else null",
        }
    ],
    "activity_type": "post | article | comment | share",
    "topics": "list of matched topic categories from CONTENT.md",
    "source": "linkedin",
}


def home_feed_instructions(scrolls: int = 5, output: str | None = None) -> dict:
    """Generate browser automation instructions for scraping the LinkedIn home feed."""
    out = output or str(_CFG["feeds_file"])
    feed_schema = {**LINKEDIN_POST_SCHEMA, "source": "linkedin"}
    return {
        "action": "scrape_linkedin_home_feed",
        "steps": [
            "Navigate to https://www.linkedin.com/feed/",
            "Wait for the page to fully load",
            f"Scroll {scrolls} times, waiting 2s between each scroll to load content",
            "For each visible post in the feed:",
            "  - Extract author display name and profile handle (vanity name from the post's profile link)",
            "  - Post text — MUST click 'see more' / '...more' on every post that is truncated before reading; never use the preview text",
            "  - Engagement: likes, comments, views (views may not always be visible)",
            "  - Post URL — click the timestamp on each post to get the full LinkedIn post URL (https://www.linkedin.com/posts/...)",
            "  - Any attached media: images (get direct URL), videos (thumbnail + video URL), documents",
            "  - Activity type: 'post' for text posts, 'article' for article shares, 'video' for video posts, 'share' for reshares",
            "Classify each post's topics using these topic categories and keywords:",
            json.dumps(get_topics()),
            "Deduplicate by URL — do not include the same post twice",
            f"Output JSON to: {out}",
            "Format: a JSON object with an 'items' key containing a list of post objects, following this schema:",
            json.dumps(feed_schema, indent=2),
            "IMPORTANT:",
            "  - You MUST expand truncated posts before extracting text",
            "  - You MUST get the full post URL for each item — click the timestamp",
            "  - Set source='linkedin' on every item",
        ],
        "topics": get_topics(),
        "output_schema": {
            "items": [feed_schema],
        },
    }


def profile_instructions(
    profile_url: str,
    output: str | None = None,
    scrolls: int = 3,
    since: str | None = None,
) -> dict:
    """Generate browser automation instructions for scraping a single LinkedIn profile's posts.

    Args:
        profile_url: Full LinkedIn profile URL
        output: Path to write JSON results
        scrolls: Number of scroll cycles to perform (default 3 ≈ 20-30 posts)
        since: ISO 8601 timestamp — stop when a post is older than this (incremental crawling)
    """
    handle = extract_handle_from_url(profile_url)
    out = output or str(_CFG["feed_raw"] / f"linkedin_{handle}.json")

    steps = [
        f"Navigate to {profile_url}",
        "Wait for the page to fully load",
        'Click the "Activity" tab (or "Posts" tab if Activity is not visible)',
        "Wait 2s for the activity feed to load",
    ]
    if since:
        steps.append(f"Stop as soon as you encounter a post published before {since} — do not extract it or any older posts")
    steps += [
        f"Scroll up to {scrolls} times, waiting 2s between each scroll to load more posts",
        "For each post in the feed, extract:",
        "  - Author display name (from profile header)",
        "  - Post text — MUST click 'see more' / '...more' on every post that is truncated before reading; never use the preview text",
        "  - Engagement: likes, comments, views (note: views may not always be visible)",
        "  - Post URL — click the timestamp on each post to get the full LinkedIn post URL (https://www.linkedin.com/posts/...)",
        "  - Any attached media: images (get direct URL), videos (get thumbnail + video URL), documents",
        "  - Activity type: 'post' for text posts, 'article' for article shares, 'video' for video posts, 'share' for reshares",
    ]
    if since:
        steps.append(f"  - Check each post's timestamp: if it was published BEFORE {since}, STOP — do not extract it or continue scrolling")
    steps += [
        "Classify each post's topics using these topic categories and keywords:",
        json.dumps(get_topics()),
        f"Output JSON to: {out}",
        "Format: a JSON object with a 'posts' key containing a list of post objects, following this schema:",
        json.dumps(LINKEDIN_POST_SCHEMA, indent=2),
        "IMPORTANT:",
        "  - You MUST get the full post URL for each item — click the timestamp; the browser URL bar alone is not sufficient",
        "  - You MUST expand truncated posts before extracting text",
        "  - Deduplicate by URL — do not include the same post twice",
        "  - Set source='linkedin' on every item",
        "  - Set topics=[...] on every item using the topic classification above",
    ]

    return {
        "action": "scrape_linkedin_profile",
        "profile": {
            "handle": handle,
            "url": profile_url,
        },
        "steps": steps,
        "output_schema": {
            "profile_url": profile_url,
            "handle": handle,
            "post_count": "number of posts extracted",
            "posts": [LINKEDIN_POST_SCHEMA],
        },
    }


def all_profiles_instructions(scrolls: int = 3) -> list[dict]:
    """Generate browser instructions for all linkedin_profile sources in sources.json.

    Returns a list of instruction dicts, one per profile.
    """
    sys.path.insert(0, str(_SCRIPTS_ROOT))
    from feed.sources import load_sources, get_by_type

    sources_file = _CFG["sources_file"]
    sources = load_sources(sources_file)
    profiles = get_by_type(sources, "linkedin_profile")

    instructions = []
    for p in profiles:
        profile_url = p.get("url", "")
        if not profile_url:
            continue
        handle = extract_handle_from_url(profile_url)
        output = str(_CFG["feed_raw"] / f"linkedin_{handle}.json")
        instructions.append(profile_instructions(profile_url, output, scrolls))

    return instructions


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn profile scraping via browser — generate automation instructions"
    )
    sub = parser.add_subparsers(dest="command")

    prof_p = sub.add_parser("profile", help="Scrape a single LinkedIn profile")
    prof_p.add_argument("handle", help="LinkedIn profile URL or vanity name")
    prof_p.add_argument("--output", default=None, help="Output JSON path")
    prof_p.add_argument(
        "--scrolls", type=int, default=3, help="Number of scroll cycles (default 3)"
    )

    all_p = sub.add_parser("profiles", help="Scrape all linkedin_profile sources")
    all_p.add_argument(
        "--scrolls", type=int, default=3, help="Number of scroll cycles per profile (default 3)"
    )

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "profile":
        url = args.handle
        if not url.startswith("http"):
            url = f"https://www.linkedin.com/in/{url}/"
        result = profile_instructions(url, args.output, args.scrolls)
        print(json.dumps(result, indent=2))

    elif args.command == "profiles":
        results = all_profiles_instructions(args.scrolls)
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
