#!/usr/bin/env python3
"""
refresh.py — Crawl feed sources and aggregate results.

Orchestrates crawling across source types:
  - x_browser: emits browser automation instructions (unchanged)
  - x_profile: crawls via ScrapeCreators API
  - linkedin_profile: crawls via ScrapeCreators API
  - youtube: delegates to youtube.py (yt-dlp)
  - website (HN): delegates to hn.py

Respects last_crawled per source for incremental updates.
First-time crawls fetch up to 20 recent posts per account.

Usage:
    python3 refresh.py [--type x_browser|x_profile|linkedin_profile|all]
    python3 refresh.py --type x_browser --scrolls 5
    python3 refresh.py --type x_profile [--handles sama,karpathy]
    python3 refresh.py --type linkedin_profile
    python3 refresh.py --thread <tweet_url>
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config
from feed.sources import load_sources, save_sources, get_by_type, update_last_crawled
from feed.source.x import home_feed_instructions
from feed.source.scrapecreators import (
    fetch_x_profile_tweets,
    fetch_x_thread,
    fetch_linkedin_profile_posts,
)
from feed.source.x import classify

_CFG = load_config()
DEFAULT_OUTPUT = str(_CFG["feeds_file"])
FIRST_CRAWL_LIMIT = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def refresh_x_browser(scrolls: int = 5, output: str | None = None) -> dict:
    """Emit browser automation instructions for X home feed."""
    return home_feed_instructions(scrolls, output)


def refresh_x_profiles(
    sources: dict,
    api_key: str,
    handles: list[str] | None = None,
    output_path: Path | None = None,
) -> list[dict]:
    """Crawl X profiles via ScrapeCreators API.

    Returns aggregated feed items. Updates last_crawled per source.
    """
    profiles = get_by_type(sources, "x_profile")
    if handles:
        handles_lower = {h.lower().lstrip("@") for h in handles}
        profiles = [p for p in profiles if p["handle"].lower() in handles_lower]

    all_items = []
    for p in profiles:
        handle = p["handle"]
        last_crawled = p.get("last_crawled")
        limit = FIRST_CRAWL_LIMIT if not last_crawled else 100

        print(f"  Crawling @{handle}...", end="", flush=True)
        items = fetch_x_profile_tweets(
            handle, api_key, since=last_crawled, limit=limit
        )

        # Classify topics
        for item in items:
            if "topics" not in item:
                item["topics"] = classify(item.get("text", ""))

        all_items.extend(items)
        update_last_crawled(sources, "x_profile", handle, _now_iso())
        print(f" {len(items)} posts")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(all_items, indent=2, default=str))
        print(f"  Saved {len(all_items)} x_profile items -> {output_path}")

    return all_items


def refresh_linkedin_profiles(
    sources: dict,
    api_key: str,
    output_path: Path | None = None,
) -> list[dict]:
    """Crawl LinkedIn profiles via ScrapeCreators API."""
    profiles = get_by_type(sources, "linkedin_profile")
    if not profiles:
        print("  No LinkedIn profile sources configured.")
        return []

    all_items = []
    for p in profiles:
        url = p["url"]
        name = p.get("name", url)
        last_crawled = p.get("last_crawled")
        limit = FIRST_CRAWL_LIMIT if not last_crawled else 100

        print(f"  Crawling {name}...", end="", flush=True)
        items = fetch_linkedin_profile_posts(
            url, api_key, since=last_crawled, limit=limit
        )

        # Classify topics
        for item in items:
            if "topics" not in item:
                item["topics"] = classify(item.get("text", ""))

        all_items.extend(items)
        update_last_crawled(sources, "linkedin_profile", url, _now_iso())
        print(f" {len(items)} posts")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(all_items, indent=2, default=str))
        print(f"  Saved {len(all_items)} linkedin_profile items -> {output_path}")

    return all_items


def refresh_thread(tweet_url: str, api_key: str, output_path: Path | None = None) -> dict:
    """Crawl a single tweet/thread via ScrapeCreators API."""
    print(f"  Crawling thread: {tweet_url}...")
    result = fetch_x_thread(tweet_url, api_key)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, default=str))
        print(f"  Saved thread -> {output_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Refresh feed sources")
    parser.add_argument(
        "--type",
        choices=["x_browser", "x_profile", "linkedin_profile", "all"],
        default="all",
        help="Source type to crawl (default: all)",
    )
    parser.add_argument("--scrolls", type=int, default=5, help="Browser scrolls for x_browser")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--handles", default=None, help="Comma-separated X handles to crawl (x_profile only)")
    parser.add_argument("--thread", default=None, help="Tweet URL to crawl as thread")

    args = parser.parse_args()

    cfg = _CFG
    sources_file = cfg["sources_file"]
    sources = load_sources(sources_file)
    api_key = cfg.get("scrapecreators_api_key")
    raw_dir = cfg["feed_raw"]

    # Thread mode
    if args.thread:
        if not api_key:
            print("Error: SCRAPECREATORS_API_KEY required for thread crawling", file=sys.stderr)
            sys.exit(1)
        result = refresh_thread(args.thread, api_key, raw_dir / "thread.json")
        print(json.dumps(result, indent=2, default=str))
        return

    results = {}
    counts: dict[str, int] = {}

    # x_browser — always emits instructions, doesn't use API
    if args.type in ("x_browser", "all"):
        instructions = refresh_x_browser(args.scrolls, args.output)
        results["x_browser"] = instructions
        update_last_crawled(sources, "x_browser", "", _now_iso())
        if args.type == "x_browser":
            print(json.dumps(instructions, indent=2))

    # x_profile — ScrapeCreators API
    if args.type in ("x_profile", "all"):
        if not api_key:
            print("Warning: SCRAPECREATORS_API_KEY not set, skipping x_profile crawl", file=sys.stderr)
        else:
            handles = args.handles.split(",") if args.handles else None
            items = refresh_x_profiles(
                sources, api_key, handles=handles,
                output_path=raw_dir / "x_profile_feed.json",
            )
            results["x_profile"] = items
            counts["x_profile"] = len(items)

    # linkedin_profile — ScrapeCreators API
    if args.type in ("linkedin_profile", "all"):
        if not api_key:
            print("Warning: SCRAPECREATORS_API_KEY not set, skipping linkedin_profile crawl", file=sys.stderr)
        else:
            items = refresh_linkedin_profiles(
                sources, api_key,
                output_path=raw_dir / "linkedin_feed.json",
            )
            results["linkedin_profile"] = items
            counts["linkedin_profile"] = len(items)

    # Save updated last_crawled timestamps
    save_sources(sources_file, sources)

    # Summary
    total = sum(counts.values())
    print(f"\nRefresh complete. {total} new items across API sources.")
    if counts:
        for source, n in counts.items():
            print(f"  {source}: {n} items")
    if "x_browser" in results:
        print("X browser instructions emitted — execute via browser tool.")

    return counts


if __name__ == "__main__":
    main()
