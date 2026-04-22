#!/usr/bin/env python3
"""
brainstorm.py — Filter and format feed posts for topic brainstorming.

Scores posts against content pillars defined in CONTENT.md. Posts spanning
multiple pillars get a cross-pillar bonus — these intersection ideas are
the most interesting for content.

Also loads knowledge/ articles (influencer blogs) and merges them into the
ranked list so trends reinforced across both sources surface to the top.

Scoring weights live in <data_dir>/ideas/brainstorm.json.

Usage:
    python3 scripts/create/brainstorm.py [--input feeds.json] [--max 10]
                                          [--knowledge-days 30] [--no-knowledge]
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
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
        if merged != saved:
            path.write_text(json.dumps(merged, indent=2) + "\n")
        return merged
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_WEIGHTS, indent=2) + "\n")
    return dict(DEFAULT_WEIGHTS)


def score_post(post: dict, pillars: dict[str, list[str]], weights: dict) -> tuple[int, list[str]]:
    """Score a post. Returns (score, [matchedPillarNames])."""
    text = (post.get("content") or post.get("text") or post.get("title") or "").lower()
    kw_weight = weights["keyword_weight"]
    matched_pillars: list[str] = []
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


def load_knowledge_items(knowledge_dir: Path, days_back: int = 30) -> list[dict]:
    """Load knowledge/ markdown articles published within days_back days."""
    if not knowledge_dir.exists():
        return []

    cutoff = date.today() - timedelta(days=days_back)
    items = []

    for md_file in knowledge_dir.rglob("*.md"):
        name = md_file.stem
        date_prefix = name[:10]
        try:
            file_date = date.fromisoformat(date_prefix)
        except ValueError:
            continue
        if file_date < cutoff:
            continue

        try:
            raw = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        title = ""
        url = ""
        source = md_file.parent.name
        author = source
        fm_done = False
        in_fm = False
        body_lines: list[str] = []

        for line in raw.splitlines():
            if not fm_done:
                if line.strip() == "---":
                    if not in_fm:
                        in_fm = True
                        continue
                    else:
                        fm_done = True
                        continue
                if in_fm:
                    if line.lower().startswith("title:"):
                        title = line[6:].strip().strip('"').strip("'")
                    elif line.lower().startswith("url:"):
                        url = line[4:].strip()
                    elif line.lower().startswith("source:"):
                        source = line[7:].strip()
                    elif line.lower().startswith("author:"):
                        author = line[7:].strip().strip('"').strip("'")
            else:
                body_lines.append(line)

        body = " ".join(body_lines)[:800]
        text = f"{title} {body}".strip()

        items.append({
            "source": source,
            "author": author,
            "title": title,
            "text": text,
            "url": url,
            "timestamp": file_date.isoformat(),
            "likes": 0,
            "_from": "knowledge",
        })

    return items


def build_brainstorm_items(
    all_posts: list[dict],
    pillars: dict[str, list[str]],
    weights: dict,
    max_per_pillar: int = 3,
) -> list[tuple]:
    """
    Rank and balance posts across all pillars.
    Returns sorted list of (score, topic, summary, pillar, ref, likes, matched_pillars).
    """
    scored = []
    for p in all_posts:
        s, matched = score_post(p, pillars, weights)
        if s > 0:
            scored.append((s, matched, p))

    scored.sort(key=lambda x: -x[0])

    # Balance: cap per pillar so no single pillar dominates
    by_pillar: dict[str, list[tuple]] = {}
    for entry in scored:
        _score, matched, p = entry
        primary = matched[0] if matched else "General"
        if len(by_pillar.get(primary, [])) < max_per_pillar:
            by_pillar.setdefault(primary, []).append(entry)

    merged = []
    for entries in by_pillar.values():
        merged.extend(entries)
    merged.sort(key=lambda x: -x[0])

    items = []
    for _score, matched, p in merged:
        primary = matched[0] if matched else "General"

        title = (p.get("title") or "").strip()
        text_body = (p.get("content") or p.get("text") or "").strip()
        if title and title != "None":
            topic = title
        else:
            first_sent = text_body.split(". ")[0].split(".\n")[0].split("\n\n")[0]
            topic = first_sent[:80].rstrip(".")

        summary = text_body
        if summary.startswith(topic):
            summary = summary[len(topic):].lstrip(". \n")
        summary = " ".join(summary.split())
        chunk = summary[:200]
        dot = chunk.rfind(". ")
        if dot > 60:
            chunk = chunk[:dot + 1]

        url = p.get("url", "") or ""
        ref = url if url.startswith("http") else (p.get("author", "") or "")
        if ref and not ref.startswith("@"):
            ref = "@" + ref

        items.append((_score, topic, chunk.strip(), primary, ref, p.get("likes", 0), matched))

    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(_CFG["feeds_file"]))
    parser.add_argument("--max", type=int, default=10)
    parser.add_argument("--knowledge-days", type=int, default=30,
                        help="Include knowledge articles from last N days (default: 30)")
    parser.add_argument("--no-knowledge", action="store_true",
                        help="Skip knowledge folder, show feed items only")
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
        feed_posts = json.load(f)

    knowledge_items = []
    if not args.no_knowledge:
        knowledge_items = load_knowledge_items(_CFG["knowledge_dir"], args.knowledge_days)

    all_posts = [
        {**p, "_from": "feed"} for p in feed_posts
    ] + knowledge_items

    items = build_brainstorm_items(all_posts, pillars, weights, max_per_pillar=3)

    if not items:
        print("No relevant posts found.")
        sys.exit(0)

    today = datetime.now().strftime("%a %d %b %Y")
    print(f"**💡 Hum Brainstorm — {today}**")
    print()

    item_num = 0
    for score, topic, summary, pillar, ref, likes, matched in items:
        item_num += 1
        print(f"{item_num}. {topic}")
        print(f"   Pillar: {pillar}")
        if summary:
            print(f"   {summary}")
        why_parts = []
        if len(matched) >= 2:
            why_parts.append(f"Spans {' + '.join(matched)}")
        if likes >= 100:
            why_parts.append(f"High engagement ({likes})")
        if why_parts:
            print(f"   Why: {' · '.join(why_parts)}")
        if ref:
            print(f"   Ref: {ref}")
        print()


if __name__ == "__main__":
    main()
