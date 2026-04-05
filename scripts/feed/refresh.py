#!/usr/bin/env python3
"""
refresh.py — Scroll Twitter/X home feed and extract posts on target topics.

Usage:
    python3 refresh.py [--scrolls N] [--output feed/raw/feed_posts.json]

Output: JSON array of posts:
  [{"author": "@handle", "text": "...", "likes": 123, "url": "...", "topics": ["AI", "startup"]}]
"""
import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config
from feed.source.x import classify, get_topics, home_feed_instructions  # noqa: F401

_CFG = load_config()
DEFAULT_OUTPUT = str(_CFG["feeds_file"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scrolls", type=int, default=5, help="Number of feed scrolls (default 5)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    args = parser.parse_args()

    instructions = home_feed_instructions(args.scrolls, args.output)
    print(json.dumps(instructions, indent=2))


if __name__ == "__main__":
    main()
