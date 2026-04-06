#!/usr/bin/env python3
"""
refresh.py — Crawl feed sources and aggregate results.

Orchestrates crawling across source types:
  - x_feed: emits browser automation instructions for X home feed
  - x_profile: emits browser automation instructions for individual X profiles (incremental)
  - linkedin_feed: emits browser automation instructions for LinkedIn home feed
  - linkedin_profile: emits browser automation instructions for LinkedIn profiles (incremental)
  - hn: fetches Hacker News stories via Algolia API

Respects last_crawled per source for incremental updates (x_profile, linkedin_profile).

Usage:
    python3 refresh.py [--type x_feed|x_profile|linkedin_feed|linkedin_profile|hn|all]
    python3 refresh.py --type x_feed --scrolls 5
    python3 refresh.py --type x_profile
    python3 refresh.py --type linkedin_profile
    python3 refresh.py --type hn
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
from feed.source.x import home_feed_instructions, profile_instructions as x_profile_instructions
from feed.source.linkedin import home_feed_instructions as linkedin_home_feed_instructions, profile_instructions as linkedin_profile_instructions
from feed.source.hn import fetch_hn

_CFG = load_config()
DEFAULT_OUTPUT = str(_CFG["feeds_file"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def refresh_x_feed(scrolls: int = 5, output: str | None = None) -> dict:
    """Emit browser automation instructions for X home feed."""
    return home_feed_instructions(scrolls, output)


def refresh_x_profiles(sources: dict, output_dir: Path | None = None) -> list[dict]:
    """Emit browser automation instructions for all configured X profiles (incremental).

    Returns a list of instruction dicts, one per profile.
    """
    profiles = get_by_type(sources, "x_profile")
    if not profiles:
        print("  No X profile sources configured.")
        return []

    results = []
    for p in profiles:
        handle = p.get("handle", "")
        if not handle:
            continue
        since = p.get("last_crawled")
        out = str((output_dir or _CFG["feed_raw"]) / f"x_{handle}.json")
        instr = x_profile_instructions(handle, out, since=since)
        results.append(instr)
        since_note = f" (since {since})" if since else " (first crawl)"
        print(f"  Prepared instructions for: @{handle}{since_note}")
        update_last_crawled(sources, "x_profile", handle, _now_iso())

    return results


def refresh_linkedin_feed(scrolls: int = 5, output: str | None = None) -> dict:
    """Emit browser automation instructions for LinkedIn home feed."""
    return linkedin_home_feed_instructions(scrolls, output)


def refresh_linkedin_profiles(sources: dict, output_dir: Path | None = None) -> list[dict]:
    """Emit browser automation instructions for all configured LinkedIn profiles (incremental).

    Returns a list of instruction dicts, one per profile.
    """
    profiles = get_by_type(sources, "linkedin_profile")
    if not profiles:
        print("  No LinkedIn profile sources configured.")
        return []

    results = []
    for p in profiles:
        url = p.get("url", "")
        name = p.get("name", url)
        if not url:
            continue
        since = p.get("last_crawled")
        out = str((output_dir or _CFG["feed_raw"]) / f"linkedin_{url.split('/in/')[-1].rstrip('/')}.json")
        instr = linkedin_profile_instructions(url, out, since=since)
        results.append(instr)
        since_note = f" (since {since})" if since else " (first crawl)"
        print(f"  Prepared instructions for: {name} ({url}){since_note}")
        update_last_crawled(sources, "linkedin_profile", url, _now_iso())

    return results


def refresh_hn(
    output_path: Path,
    story_type: str = "both",
    hits_per_page: int = 30,
    days_back: int = 7,
) -> list[dict]:
    """Fetch HN stories via Algolia API and merge into feeds.json."""
    items = fetch_hn(story_type, hits_per_page, days_back)

    existing: list[dict] = []
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []

    non_hn = [p for p in existing if p.get("source") != "hn"]
    merged = non_hn + items

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"  Fetched {len(items)} HN stories → {output_path}")
    return items


def main():
    parser = argparse.ArgumentParser(description="Refresh feed sources")
    parser.add_argument(
        "--type",
        choices=["x_feed", "x_profile", "linkedin_feed", "linkedin_profile", "hn", "all"],
        default="all",
        help="Source type to crawl (default: all)",
    )
    parser.add_argument("--scrolls", type=int, default=5, help="Browser scrolls for x_feed and linkedin_feed")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")

    args = parser.parse_args()

    cfg = _CFG
    sources_file = cfg["sources_file"]
    sources = load_sources(sources_file)
    raw_dir = cfg["feed_raw"]

    results = {}

    # x_feed — browser automation for X home feed
    if args.type in ("x_feed", "all"):
        instructions = refresh_x_feed(args.scrolls, args.output)
        results["x_feed"] = instructions
        update_last_crawled(sources, "x_feed", "", _now_iso())
        if args.type == "x_feed":
            print(json.dumps(instructions, indent=2))

    # x_profile — browser automation per configured X profile (incremental)
    if args.type in ("x_profile", "all"):
        instructions = refresh_x_profiles(sources, raw_dir)
        results["x_profile"] = instructions
        if args.type == "x_profile":
            print(json.dumps(instructions, indent=2, default=str))

    # linkedin_feed — browser automation for LinkedIn home feed
    if args.type in ("linkedin_feed", "all"):
        instructions = refresh_linkedin_feed(args.scrolls, args.output)
        results["linkedin_feed"] = instructions
        update_last_crawled(sources, "linkedin_feed", "", _now_iso())
        if args.type == "linkedin_feed":
            print(json.dumps(instructions, indent=2))

    # linkedin_profile — browser automation per configured LinkedIn profile (incremental)
    if args.type in ("linkedin_profile", "all"):
        instructions = refresh_linkedin_profiles(sources, raw_dir)
        results["linkedin_profile"] = instructions
        if args.type == "linkedin_profile":
            print(json.dumps(instructions, indent=2, default=str))

    # hn — fetch Hacker News stories via Algolia API and merge into feeds.json
    if args.type in ("hn", "all"):
        feeds_path = Path(args.output)
        hn_items = refresh_hn(feeds_path)
        results["hn"] = hn_items
        update_last_crawled(sources, "website", "news.ycombinator.com", _now_iso())
        if args.type == "hn":
            print(json.dumps(hn_items, indent=2))

    # Save updated last_crawled timestamps
    save_sources(sources_file, sources)

    print(f"\nRefresh complete.")
    browser_types = []
    if "x_feed" in results:
        browser_types.append("X feed")
    if "x_profile" in results and results["x_profile"]:
        browser_types.append(f"{len(results['x_profile'])} X profile(s)")
    if "linkedin_feed" in results:
        browser_types.append("LinkedIn feed")
    if "linkedin_profile" in results and results["linkedin_profile"]:
        browser_types.append(f"{len(results['linkedin_profile'])} LinkedIn profile(s)")
    if browser_types:
        print(f"Browser instructions emitted for: {', '.join(browser_types)} — execute via browser tool.")
    if "hn" in results:
        print(f"HN: {len(results['hn'])} stories merged into {args.output}.")

    return results


if __name__ == "__main__":
    main()
