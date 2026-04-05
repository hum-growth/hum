#!/usr/bin/env python3
"""
sources.py — Manage feed sources (X accounts, YouTube creators, websites).

Usage:
    python3 sources.py list
    python3 sources.py add x <handle> [category]
    python3 sources.py add youtube <url> [name]
    python3 sources.py add website <name> <url>
    python3 sources.py remove <handle_or_name>
"""
import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config


def load_sources(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"x_accounts": [], "youtube_creators": [], "websites": []}


def save_sources(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved → {path}")


def cmd_list(sources: dict):
    print("=== X Accounts ===")
    for a in sources.get("x_accounts", []):
        cat = f" [{a['category']}]" if a.get("category") else ""
        desc = f" — {a['description']}" if a.get("description") else ""
        print(f"  @{a['handle']}{cat}{desc}")
    print(f"  ({len(sources.get('x_accounts', []))} total)\n")

    print("=== YouTube Creators ===")
    for c in sources.get("youtube_creators", []):
        print(f"  {c.get('name', '?')} — {c['url']}")
    print(f"  ({len(sources.get('youtube_creators', []))} total)\n")

    print("=== Websites ===")
    for w in sources.get("websites", []):
        print(f"  {w['name']} — {w['url']}")
    print(f"  ({len(sources.get('websites', []))} total)")


def cmd_add(sources: dict, args):
    if args.source_type == "x":
        handle = args.value.lstrip("@")
        if any(a["handle"] == handle for a in sources["x_accounts"]):
            print(f"@{handle} already exists.")
            return False
        entry = {"handle": handle}
        if args.extra:
            entry["category"] = " ".join(args.extra)
        sources["x_accounts"].append(entry)
        print(f"Added @{handle}")
        return True

    elif args.source_type == "youtube":
        url = args.value
        if any(c["url"] == url for c in sources["youtube_creators"]):
            print(f"{url} already exists.")
            return False
        name = " ".join(args.extra) if args.extra else url.split("@")[-1] if "@" in url else url
        sources["youtube_creators"].append({"name": name, "url": url})
        print(f"Added YouTube: {name}")
        return True

    elif args.source_type == "website":
        name = args.value
        if not args.extra:
            print("Usage: add website <name> <url>")
            return False
        url = args.extra[0]
        if any(w["name"] == name for w in sources["websites"]):
            print(f"{name} already exists.")
            return False
        sources["websites"].append({"name": name, "url": url})
        print(f"Added website: {name}")
        return True

    else:
        print(f"Unknown source type: {args.source_type}")
        return False


def cmd_remove(sources: dict, target: str):
    target_lower = target.lower().lstrip("@")

    for i, a in enumerate(sources.get("x_accounts", [])):
        if a["handle"].lower() == target_lower:
            sources["x_accounts"].pop(i)
            print(f"Removed @{a['handle']}")
            return True

    for i, c in enumerate(sources.get("youtube_creators", [])):
        if c.get("name", "").lower() == target_lower or c["url"].lower() == target_lower:
            sources["youtube_creators"].pop(i)
            print(f"Removed YouTube: {c.get('name', c['url'])}")
            return True

    for i, w in enumerate(sources.get("websites", [])):
        if w["name"].lower() == target_lower:
            sources["websites"].pop(i)
            print(f"Removed website: {w['name']}")
            return True

    print(f"Not found: {target}")
    return False


def main():
    parser = argparse.ArgumentParser(description="Manage hum feed sources")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all sources")

    add_p = sub.add_parser("add", help="Add a source")
    add_p.add_argument("source_type", choices=["x", "youtube", "website"])
    add_p.add_argument("value", help="Handle, URL, or name")
    add_p.add_argument("extra", nargs="*", help="Category (x), name (youtube), or URL (website)")

    rm_p = sub.add_parser("remove", help="Remove a source")
    rm_p.add_argument("target", help="Handle or name to remove")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cfg = load_config()
    sources_file = cfg["sources_file"]
    sources = load_sources(sources_file)

    if args.command == "list":
        cmd_list(sources)
    elif args.command == "add":
        if cmd_add(sources, args):
            save_sources(sources_file, sources)
    elif args.command == "remove":
        if cmd_remove(sources, args.target):
            save_sources(sources_file, sources)


if __name__ == "__main__":
    main()
