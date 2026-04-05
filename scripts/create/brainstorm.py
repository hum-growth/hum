#!/usr/bin/env python3
"""
brainstorm.py — Filter and format feed posts for topic brainstorming.

Scores posts against content pillars defined in CONTENT.md. Posts spanning
multiple pillars get a cross-pillar bonus — these intersection ideas are
the most interesting for content.

Scoring weights live in <data_dir>/ideas/brainstorm.json.

Usage:
    python3 scripts/create/brainstorm.py [--input feeds.json] [--max 10]
"""
import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config, load_topics

_CFG = load_config()

DEFAULT_WEIGHTS = {
    "keyword_weight": 50,
    "cross_pillar_bonus": 100,
    "min_pillars_for_bonus": 2,
    "likes_divisor": 100,
}


def load_weights() -> dict:
    """Load scoring weights, creating or updating brainstorm.json if needed."""
    path = _CFG["ideas_dir"] / "brainstorm.json"
    if path.exists():
        with open(path) as f:
            saved = json.load(f)
        merged = {**DEFAULT_WEIGHTS, **saved}
        # Backfill any new default keys
        if merged != saved:
            path.write_text(json.dumps(merged, indent=2) + "\n")
        return merged
    # First run — create with defaults
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_WEIGHTS, indent=2) + "\n")
    return dict(DEFAULT_WEIGHTS)


def score_post(post: dict, pillars: dict[str, list[str]], weights: dict) -> tuple[int, list[str]]:
    """Score a post against all pillars. Returns (score, matched_pillar_names)."""
    text = (post.get("text") or post.get("title") or "").lower()
    kw_weight = weights["keyword_weight"]
    matched_pillars = []
    total_hits = 0

    for pillar_name, keywords in pillars.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            matched_pillars.append(pillar_name)
            total_hits += hits

    score = total_hits * kw_weight

    if len(matched_pillars) >= weights["min_pillars_for_bonus"]:
        score += weights["cross_pillar_bonus"]

    likes = min(post.get("likes", 0), 50000)
    divisor = weights["likes_divisor"]
    if divisor > 0:
        score += likes // divisor

    return score, matched_pillars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(_CFG["feeds_file"]))
    parser.add_argument("--max", type=int, default=10)
    args = parser.parse_args()

    pillars = load_topics()
    if not pillars:
        print("No content pillars found. Set up CONTENT.md first.", file=sys.stderr)
        sys.exit(1)

    weights = load_weights()

    if not os.path.exists(args.input):
        print(f"No feed data at {args.input}. Run scrape first.")
        sys.exit(1)

    with open(args.input) as f:
        posts = json.load(f)

    scored = []
    for p in posts:
        s, matched = score_post(p, pillars, weights)
        if s > 0:
            scored.append((s, matched, p))

    scored.sort(key=lambda x: -x[0])
    scored = scored[:args.max]

    if not scored:
        print("No relevant posts found.")
        sys.exit(0)

    pillar_names = ", ".join(pillars.keys())
    print(f"FEED POSTS FOR BRAINSTORMING ({len(scored)} of {len(posts)} total)")
    print(f"Pillars: {pillar_names}\n")
    print("=" * 60)

    for i, (score, matched, p) in enumerate(scored, 1):
        author = p.get("author", "?").lstrip("@")
        likes = p.get("likes", 0)
        pillars_str = " + ".join(matched)
        print(f"\n[{i}] @{author} | {likes:,} likes | {pillars_str} (score: {score})")
        print(f"    {(p.get('text') or p.get('title') or '').strip()[:300]}")
        if p.get("url"):
            print(f"    {p['url']}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
