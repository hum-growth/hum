#!/usr/bin/env python3
"""
digest.py — Format scraped feed posts into a Telegram digest.

Usage:
    python3 digest.py --input /tmp/feed_posts.json [--youtube-input /tmp/youtube_feed.json] [--max-posts 10]

Output: Formatted Telegram message (plain text, no markdown tables)
"""
import argparse, json, os, re, sys
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config
from feed.utils import STOPWORDS, parse_likes
from feed.blocklist import load_blocklist, is_blocked
from lib.atomic_io import atomic_write_json
_CFG = load_config()
SEEN_HISTORY_FILE = str(_CFG["feed_assets"] / "seen_history.json")

MAX_TEXT_LEN = 200
SEEN_EXPIRY_DAYS = 7  # Forget seen items after this many days
FINGERPRINT_OVERLAP_MIN = 4  # Min shared keywords to treat two stories as duplicates
MAX_POSTS_PER_AUTHOR = 2  # Cap per-author posts within a single digest

TOPIC_KEYWORDS = {
    "ai": [
        "ai", "agent", "agents", "llm", "llms", "openai", "claude", "codex",
        "copilot", "model", "models", "onnx", "inference", "transformer", "gpt",
    ],
    "startups": [
        "startup", "startups", "founder", "founders", "yc", "venture", "seed",
        "product-market", "pmf", "launch", "growth",
    ],
    "fintech": [
        "fintech", "banking", "bank", "payments", "partner bank", "neobank",
        "wallet", "card", "cards", "stripe", "stablecoin",
    ],
    "finance": [
        "finance", "cfo", "fp&a", "revenue", "pricing", "margin", "budget",
        "forecast", "cash", "earnings", "accounting", "finanser",
    ],
    "crypto": [
        "crypto", "ethereum", "bitcoin", "web3", "defi", "dao", "token",
        "chain", "blockchain", "rollup", "erc-",
    ],
}


def truncate(text, n=MAX_TEXT_LEN):
    return text[:n].rstrip() + "…" if len(text) > n else text


def post_date(p: dict):
    ts = p.get("timestamp", "")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return None


def load_items(path):
    if not path:
        return []
    try:
        with open(path) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []


def make_story_fingerprint(text: str) -> str:
    """
    Create a rough 'story fingerprint' from tweet text for cross-source deduplication.
    Strips stopwords, lowercases, extracts key words, and joins the top 5.
    Two tweets about the same story should share several keywords.
    """
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    filtered = [w for w in words if w not in STOPWORDS]
    # Use the first 8 meaningful words as fingerprint
    return " ".join(filtered[:8])


def content_depth_multiplier(post: dict) -> float:
    """Prefer deeper article-style content over shallow feed items."""
    source = post.get("source")
    post_type = post.get("post_type") or ""
    text_len = len(post.get("content", "") or "")
    title_len = len(post.get("title", "") or "")
    multiplier = 1.0

    if source == "knowledge":
        multiplier *= 1.5
    if source == "youtube":
        multiplier *= 1.2
    if post_type in {"article", "story"} and source != "x":
        multiplier *= 1.2
    if text_len >= 800:
        multiplier *= 1.25
    elif text_len >= 300:
        multiplier *= 1.1
    if source == "x" and text_len < 160 and title_len == 0:
        multiplier *= 0.75
    return multiplier


def post_sort_key(post: dict) -> tuple:
    depth = content_depth_multiplier(post)
    if "_score" in post:
        return (3, float(post.get("_score", 0)) * depth)
    if post.get("source") == "youtube":
        return (2, int(post.get("views", 0) or 0) * depth)
    return (1, parse_likes(post.get("likes", 0)) * depth)


def infer_topics(post: dict) -> set[str]:
    """Return explicit topics plus lightweight keyword-inferred topics."""
    topics = {(t or "").lower() for t in post.get("topics", []) if t}
    text = " ".join(
        str(post.get(k, "") or "")
        for k in ("title", "content", "author", "display_name", "url")
    ).lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            topics.add(topic)
    return topics


def _keyword_matches(text: str, keyword: str) -> bool:
    # Avoid false positives like `ai` in `main` or `yc` in `physical`.
    if re.fullmatch(r"[a-z0-9]+", keyword):
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def load_seen_history() -> dict:
    """Load the seen history file. Returns {url: iso_date, fingerprint: iso_date}."""
    if os.path.exists(SEEN_HISTORY_FILE):
        try:
            with open(SEEN_HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[digest] Error: {e}", file=sys.stderr)
    return {}


def save_seen_history(history: dict):
    """Save seen history, pruning entries older than SEEN_EXPIRY_DAYS."""
    cutoff = (datetime.now() - timedelta(days=SEEN_EXPIRY_DAYS)).isoformat()
    pruned = {k: v for k, v in history.items() if v >= cutoff}
    atomic_write_json(Path(SEEN_HISTORY_FILE), pruned)


def is_seen(post: dict, history: dict) -> bool:
    """Return True if this post (or a very similar one) has been shown before."""
    url = post.get("url", "")
    if url and url in history:
        return True
    fp = make_story_fingerprint(post.get("content", "") or post.get("title", ""))
    fp_words = set(fp.split())
    if len(fp_words) >= FINGERPRINT_OVERLAP_MIN:
        threshold = min(FINGERPRINT_OVERLAP_MIN, len(fp_words) - 1)
        for key in history:
            if key.startswith("fp:"):
                seen_words = set(key[3:].split())
                if len(fp_words & seen_words) >= threshold:
                    return True
    return False


def mark_seen(post: dict, history: dict):
    """Add post URL and fingerprint to the seen history."""
    now = datetime.now().isoformat()
    url = post.get("url", "")
    if url:
        history[url] = now
    fp = make_story_fingerprint(post.get("content", "") or post.get("title", ""))
    if fp:
        history[f"fp:{fp}"] = now


def _format_post_lines(counter: int, p: dict) -> list[str]:
    """Render one digest entry for any source. Returns the lines to append."""
    author = p.get("author", "") or p.get("channel_name", "")
    url = p.get("url", "")
    lines: list[str] = []

    if p.get("source") == "youtube":
        title = truncate(p.get("title", ""), 110)
        summary = truncate(p.get("content", ""))
        published = p.get("timestamp", "")
        date_suffix = f" ({published[:10]})" if published else ""
        lines.append(f"{counter}. ▶ {author}: {title}{date_suffix}")
        if summary:
            lines.append(f"   {summary}")
    elif p.get("source") == "knowledge":
        post_type = p.get("post_type") or "article"
        icon = {"podcast": "🎧", "video": "▶", "article": "📄"}.get(post_type, "📄")
        title = truncate(p.get("title", "") or "Untitled", 110)
        snippet = truncate(p.get("content", ""))
        source_name = p.get("knowledge_source", "") or author or "knowledge"
        lines.append(f"{counter}. {icon} {source_name}: {title}")
        if snippet:
            lines.append(f"   {snippet}")
    elif p.get("source") == "hn":
        title = truncate(p.get("title", "") or p.get("content", ""), 110)
        excerpt = p.get("article_excerpt") or ""
        lines.append(f"{counter}. {author}: {title}")
        if excerpt:
            lines.append(f"   {truncate(excerpt)}")
    else:
        text = truncate(p.get("content", "") or p.get("title", ""))
        lines.append(f"{counter}. {author}: {text}")

    if url:
        lines.append(f"   {url}")
    return lines


def format_digest(posts: list[dict], max_posts: int, include_seen: bool = False) -> str:
    if not posts:
        return None

    history = load_seen_history()
    blocklist = load_blocklist()

    # Drop blocked authors. For normal automated runs, avoid repeats from the
    # recent seen history; for manual resends, include same-day items even if
    # they were already sent in an earlier attempt.
    fresh_posts = [
        p for p in posts
        if not is_blocked(p.get("author", ""), blocklist)
        and (include_seen or not is_seen(p, history))
    ]

    fresh_posts.sort(key=post_sort_key, reverse=True)

    # Prefer yesterday's articles; fall back to past week if not enough
    yesterday = (datetime.now() - timedelta(days=1)).date()
    week_ago = (datetime.now() - timedelta(days=7)).date()
    yesterday_posts = [p for p in fresh_posts if post_date(p) == yesterday]
    if len(yesterday_posts) >= max_posts:
        fresh_posts = yesterday_posts
    elif yesterday_posts:
        older = [p for p in fresh_posts if (d := post_date(p)) and week_ago <= d < yesterday]
        fresh_posts = yesterday_posts + older

    # Deduplicate within this batch: each post appears in at most one section (highest-priority topic wins)
    # Buckets are keyed by lowercased pillar name so matching is case-insensitive
    # and robust to legacy topic variants (e.g. "startup" vs "Startups").
    topic_priority = ["ai", "startups", "fintech", "finance", "crypto"]
    by_topic: dict[str, list[dict]] = {t: [] for t in topic_priority}
    untagged: list[dict] = []
    seen_urls = set()
    author_counts: dict[str, int] = {}

    # Hard cap: stop once we've selected max_posts items across all sections.
    # Show a small curated set per topic, then put overflow into General so a
    # single dominant topic (e.g. AI-heavy days) does not collapse the digest.
    topic_cap = 3
    untagged_cap = max_posts

    total_placed = 0  # track items selected so far
    for p in fresh_posts:
        if total_placed >= max_posts:
            break
        url = p.get("url", "")
        if url in seen_urls:
            continue
        author = p.get("author", "") or ""
        if author_counts.get(author, 0) >= MAX_POSTS_PER_AUTHOR:
            continue
        post_topics_lower = infer_topics(p)
        assigned = False
        for t in topic_priority:
            if t in post_topics_lower and len(by_topic[t]) < topic_cap:
                by_topic[t].append(p)
                seen_urls.add(url)
                author_counts[author] = author_counts.get(author, 0) + 1
                total_placed += 1
                assigned = True
                break
        # Fallback to General if the post is untagged or its topic bucket is full.
        if not assigned and len(untagged) < untagged_cap:
            untagged.append(p)
            seen_urls.add(url)
            author_counts[author] = author_counts.get(author, 0) + 1
            total_placed += 1

    today = datetime.now().strftime("%a %d %b %Y")
    lines = [f"**🗞 Hum Digest — {today}**", ""]

    counter = 1
    digest_map = {}
    labels = {
        "ai": "🤖 AI",
        "startups": "🚀 Startups",
        "fintech": "🏦 Fintech",
        "finance": "💰 Finance",
        "crypto": "🪙 Crypto",
    }

    # First pass: topic-sections
    for topic, label in labels.items():
        items = by_topic.get(topic, [])
        if not items:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"**{label}**")
        for p in items:
            lines.extend(_format_post_lines(counter, p))
            digest_map[str(counter)] = p
            counter += 1
            mark_seen(p, history)

    # Fallback: show General posts until max_posts is reached.
    if untagged:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("**📌 General**")
        for p in untagged:
            if counter > max_posts:
                break
            lines.extend(_format_post_lines(counter, p))
            digest_map[str(counter)] = p
            counter += 1
            mark_seen(p, history)
            seen_urls.add(p.get("url", ""))

    # Save updated seen history
    save_seen_history(history)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(_CFG["feeds_file"]))
    parser.add_argument("--youtube-input", default=str(_CFG["feed_raw"] / "youtube_feed.json"))
    parser.add_argument("--max-posts", type=int, default=12)
    parser.add_argument("--include-seen", action="store_true", help="Include items already sent recently; useful for manual same-day resends")
    args = parser.parse_args()

    posts = load_items(args.input)
    youtube_posts = load_items(args.youtube_input)
    posts.extend(youtube_posts)

    if not posts:
        print("ERROR: No input items found. Run refresh.py and/or source/youtube.py first.", file=sys.stderr)
        sys.exit(1)

    digest = format_digest(posts, args.max_posts, include_seen=args.include_seen)
    if digest:
        print(digest)
    else:
        print("No relevant posts found today.")


if __name__ == "__main__":
    main()
