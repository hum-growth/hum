#!/usr/bin/env python3
"""
One-time migration: convert old sources.json format to new feed_sources format.

Old format: {x_accounts: [...], youtube_creators: [...], websites: [...]}
New format: {feed_sources: [{type, handle/url/name, category, description, last_crawled}, ...]}

Usage:
    python3 migrate_sources.py [--dry-run]
"""
import json
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))
from config import load_config


def migrate(sources: dict) -> dict:
    feed_sources = []

    # X browser feed (always present as a meta-source)
    feed_sources.append({
        "type": "x_browser",
        "enabled": True,
        "description": "X home feed via browser scrolling",
        "last_crawled": None,
    })

    # Migrate x_accounts → x_profile
    for acct in sources.get("x_accounts", []):
        feed_sources.append({
            "type": "x_profile",
            "handle": acct["handle"],
            "category": acct.get("category", ""),
            "description": acct.get("description", ""),
            "last_crawled": None,
        })

    # Migrate youtube_creators → youtube
    for yt in sources.get("youtube_creators", []):
        feed_sources.append({
            "type": "youtube",
            "name": yt.get("name", ""),
            "url": yt["url"],
            "description": yt.get("description", ""),
            "last_crawled": None,
        })

    # Migrate websites → website
    for ws in sources.get("websites", []):
        feed_sources.append({
            "type": "website",
            "name": ws["name"],
            "url": ws["url"],
            "last_crawled": None,
        })

    return {"feed_sources": feed_sources}


def main():
    dry_run = "--dry-run" in sys.argv
    cfg = load_config()
    sources_file = cfg["sources_file"]

    if not sources_file.exists():
        print(f"Sources file not found: {sources_file}")
        sys.exit(1)

    with open(sources_file) as f:
        old = json.load(f)

    # Already migrated?
    if "feed_sources" in old:
        print("Already in new format. Nothing to do.")
        return

    new = migrate(old)

    if dry_run:
        print(json.dumps(new, indent=2))
        print(f"\n--- {len(new['feed_sources'])} sources total ---")
        return

    # Backup old file
    backup = sources_file.with_suffix(".json.bak")
    backup.write_text(json.dumps(old, indent=2))
    print(f"Backed up old format → {backup}")

    # Write new format
    with open(sources_file, "w") as f:
        json.dump(new, f, indent=2, ensure_ascii=False)
    print(f"Migrated → {sources_file} ({len(new['feed_sources'])} sources)")


if __name__ == "__main__":
    main()
