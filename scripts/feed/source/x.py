#!/usr/bin/env python3
"""
x.py — X/Twitter feed source: scrape home feed, threads, and tweets with media.

This module outputs structured JSON instructions for browser automation.
The actual scraping is performed by the agent using the browser tool.

Usage:
    python3 -m feed.source.x home [--scrolls N] [--output PATH]
    python3 -m feed.source.x thread <url> [--output PATH]
    python3 -m feed.source.x tweet <url> [--output PATH]
"""
import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config, load_topics

_CFG = load_config()

# ── Topic classification ───────────────────────────────────────────────────


def get_topics() -> dict[str, list[str]]:
    """Load topics from CONTENT.md (data_dir). Cached after first call."""
    if not hasattr(get_topics, "_cache"):
        get_topics._cache = load_topics(_CFG["data_dir"])
    return get_topics._cache


def classify(text: str, topics: dict[str, list[str]] | None = None) -> list[str]:
    """Classify text into topic categories based on keyword matching."""
    if topics is None:
        topics = get_topics()
    lower = text.lower()
    return [topic for topic, kws in topics.items() if any(re.search(r'\b' + re.escape(kw) + r'\b', lower) for kw in kws)]


# ── Shared schemas ─────────────────────────────────────────────────────────

MEDIA_SCHEMA = {
    "type": "image | video | gif",
    "url": "direct media URL (highest resolution available)",
    "alt_text": "alt text if present, else null",
    "thumbnail_url": "video thumbnail URL if type is video, else null",
}

TWEET_SCHEMA = {
    "author": "@handle",
    "display_name": "display name",
    "text": "full tweet text (expand 'Show more' if truncated)",
    "likes": "integer or null",
    "retweets": "integer or null",
    "replies": "integer or null",
    "views": "integer or null",
    "url": "https://x.com/<handle>/status/<id>",
    "timestamp": "ISO 8601 or relative time string",
    "media": [MEDIA_SCHEMA],
    "is_quote_tweet": "boolean",
    "quoted_tweet": "nested tweet object if quote tweet, else null",
}


# ── Home feed scraping ─────────────────────────────────────────────────────

def home_feed_instructions(scrolls: int = 5, output: str | None = None) -> dict:
    """Generate browser automation instructions for scraping the X home feed."""
    out = output or str(_CFG["feeds_file"])
    return {
        "action": "scrape_x_home_feed",
        "steps": [
            "Navigate to https://x.com/home",
            f"Scroll {scrolls} times, waiting 2s between each scroll to load content",
            "For each visible tweet:",
            "  - Extract author handle, display name, full text — you MUST click 'Show more' on every tweet that has it before reading the text; never use the truncated inline preview",
            "  - Extract engagement counts: likes, retweets, replies, views",
            "  - Extract tweet URL",
            "  - Extract all attached media:",
            "    - Images: right-click or inspect to get full-res URL (format=jpg&name=large)",
            "    - Videos: note the thumbnail URL and video URL if accessible",
            "    - GIFs: extract the video/mp4 URL",
            "  - If tweet is a quote tweet, also extract the quoted tweet content and media",
            "Filter to tweets matching at least one topic keyword (see topics below)",
            "Deduplicate by URL",
            "Set source='x' on every item",
            f"Output JSON to: {out}",
        ],
        "topics": get_topics(),
        "output_schema": {
            "items": [{**TWEET_SCHEMA, "source": "x"}],
            "note": "topics field is added per-item based on keyword classification",
        },
    }


# ── Profile scraping ──────────────────────────────────────────────────────

def profile_instructions(
    handle: str,
    output: str | None = None,
    limit: int = 20,
    since: str | None = None,
) -> dict:
    """Generate browser automation instructions for scraping an X profile's posts.

    Args:
        handle: X handle (without @)
        output: Path to write JSON results
        limit: Max number of tweets to extract (default 20)
        since: ISO 8601 timestamp — stop when a tweet is older than this
    """
    out = output or str(_CFG["feed_raw"] / f"x_{handle}.json")
    since_step = (
        f"  - Check the tweet's timestamp: if it was posted BEFORE {since}, STOP immediately — do not extract this or any further tweets"
        if since else None
    )
    steps = [
        f"Navigate to https://x.com/{handle}",
        "Wait for the page to fully load",
        f"Extract up to {limit} tweets from this profile's timeline, scrolling as needed",
    ]
    if since:
        steps.append(f"Stop as soon as you encounter a tweet posted before {since} — do not extract it")
    steps += [
        "For each tweet in chronological reverse order (newest first):",
        "  - Extract author handle, display name, full text — you MUST click 'Show more' on every tweet that has it before reading the text; never use the truncated inline preview",
        "  - Extract engagement counts: likes, retweets, replies, views",
        "  - Extract tweet URL",
        "  - Extract all attached media:",
        "    - Images: get full-res URL (format=jpg&name=large)",
        "    - Videos: note the thumbnail URL and video URL if accessible",
        "    - GIFs: extract the video/mp4 URL",
        "  - If tweet is a quote tweet, also extract the quoted tweet content and media",
    ]
    if since_step:
        steps.append(since_step)
    steps += [
        "Deduplicate by URL",
        "Set source='x' on every item",
        f"Output JSON to: {out}",
    ]
    return {
        "action": "scrape_x_profile",
        "profile": {
            "handle": handle,
            "url": f"https://x.com/{handle}",
        },
        "steps": steps,
        "output_schema": {
            "handle": handle,
            "tweet_count": "number of tweets extracted",
            "items": [{**TWEET_SCHEMA, "source": "x"}],
        },
    }


# ── Thread scraping ────────────────────────────────────────────────────────

def thread_instructions(thread_url: str, output: str | None = None) -> dict:
    """Generate browser automation instructions for scraping an X thread."""
    out = output or str(_CFG["feed_raw"] / "thread.json")
    return {
        "action": "scrape_x_thread",
        "steps": [
            f"Navigate to {thread_url}",
            "Identify the thread author from the original tweet",
            "Scroll down to load the full thread — keep scrolling until all replies by the "
            "original author in the thread chain are visible",
            "Collect each tweet in the thread (tweets by the same author in the reply chain):",
            "  - Extract author handle, display name, full text (click 'Show more' if truncated)",
            "  - Extract engagement counts: likes, retweets, replies, views",
            "  - Extract tweet URL (each tweet in a thread has its own URL)",
            "  - Extract all attached media per tweet:",
            "    - Images: get full-res URL (format=jpg&name=large)",
            "    - Videos: note the thumbnail URL and video URL if accessible",
            "    - GIFs: extract the video/mp4 URL",
            "  - If any tweet is a quote tweet, also extract the quoted tweet content",
            "Order tweets chronologically (first tweet first)",
            "Stop collecting when the thread ends (next reply is by a different author "
            "and not a continuation)",
            f"Output JSON to: {out}",
        ],
        "output_schema": {
            "thread_url": thread_url,
            "author": "@handle of thread author",
            "tweet_count": "number of tweets in thread",
            "tweets": [
                {
                    **TWEET_SCHEMA,
                    "position": "1-indexed position in thread",
                }
            ],
            "total_likes": "sum of likes across all thread tweets",
            "total_retweets": "sum of retweets across all thread tweets",
            "topics": ["classified from combined thread text"],
        },
    }


# ── Single tweet scraping ──────────────────────────────────────────────────

def tweet_instructions(tweet_url: str, output: str | None = None) -> dict:
    """Generate browser automation instructions for scraping a single tweet with media."""
    out = output or str(_CFG["feed_raw"] / "tweet.json")
    return {
        "action": "scrape_x_tweet",
        "steps": [
            f"Navigate to {tweet_url}",
            "Extract the full tweet content:",
            "  - Author handle and display name",
            "  - Full text (click 'Show more' if truncated)",
            "  - Engagement counts: likes, retweets, replies, views, bookmarks",
            "  - Timestamp",
            "Extract all attached media:",
            "  - Images: click to open full size, get URL with format=jpg&name=large",
            "  - Videos: extract video URL and thumbnail",
            "  - GIFs: extract the video/mp4 source URL",
            "If this is a quote tweet, also extract the quoted tweet and its media",
            "Check if this tweet is part of a thread (look for 'Show this thread' or "
            "replies by the same author). If so, note is_thread=true and thread_url",
            f"Output JSON to: {out}",
        ],
        "output_schema": {
            **TWEET_SCHEMA,
            "bookmarks": "integer or null",
            "is_thread": "boolean — true if tweet is part of a thread by same author",
            "thread_url": "URL of first tweet in thread if is_thread, else null",
            "topics": ["classified from tweet text"],
        },
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="X/Twitter feed source — generate scraping instructions"
    )
    sub = parser.add_subparsers(dest="command")

    home_p = sub.add_parser("home", help="Scrape home feed")
    home_p.add_argument("--scrolls", type=int, default=5, help="Feed scrolls (default 5)")
    home_p.add_argument("--output", default=None, help="Output JSON path")

    thread_p = sub.add_parser("thread", help="Scrape a thread")
    thread_p.add_argument("url", help="URL of any tweet in the thread")
    thread_p.add_argument("--output", default=None, help="Output JSON path")

    tweet_p = sub.add_parser("tweet", help="Scrape a single tweet with media")
    tweet_p.add_argument("url", help="Tweet URL")
    tweet_p.add_argument("--output", default=None, help="Output JSON path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "home":
        result = home_feed_instructions(args.scrolls, args.output)
    elif args.command == "thread":
        result = thread_instructions(args.url, args.output)
    elif args.command == "tweet":
        result = tweet_instructions(args.url, args.output)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
