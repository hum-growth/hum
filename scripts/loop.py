#!/usr/bin/env python3
"""
loop.py — Daily hum automation loop.

Runs at 6am daily. Orchestrates the full morning workflow:
  1. Feed digest — scrape, rank, format, send via Telegram
  2. Engage — suggest accounts to follow + draft replies for approval
  3. Brainstorm — surface top ideas and ask which topics to add / posts to work on
  4. Learn (Sundays only) — analyze feed trends, research algorithms, update context files

Usage:
    python3 scripts/loop.py                     # full daily loop
    python3 scripts/loop.py --step digest       # just the digest
    python3 scripts/loop.py --step engage       # just engagement suggestions
    python3 scripts/loop.py --step brainstorm   # just brainstorm
    python3 scripts/loop.py --step learn        # just strategy refresh (normally Sunday only)
    python3 scripts/loop.py --dry-run           # format output but don't send
    python3 scripts/loop.py --max-posts 15      # override digest size
"""

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_channel_config, load_channel_handle, load_config, load_topics, load_x_credentials
from lib import bird_x

_CFG = load_config()

# Telegram message character limit
_TG_LIMIT = 4000


def _send_to_target(target_str: str, text: str, dry_run: bool = False) -> None:
    """Send text to a delivery target.

    target_str formats:
      "channel:recipient"         e.g. "telegram:-1003734033302"
      "channel:account:recipient" e.g. "telegram:ghost:1196250983"

    The optional account selects which bot account to send from (passed as
    --account to the openclaw CLI). Splits into chunks if text exceeds limit.
    """
    if not target_str or not text.strip():
        return
    parts = target_str.split(":")
    if len(parts) == 3:
        channel, account, recipient = parts[0].strip(), parts[1].strip(), parts[2].strip()
    elif len(parts) == 2:
        channel, recipient = parts[0].strip(), parts[1].strip()
        account = None
    else:
        print(f"[loop] invalid target format '{target_str}' — expected channel:recipient or channel:account:recipient", file=sys.stderr)
        return

    # Split into chunks at paragraph boundaries to stay under the limit
    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip("\n") if current else para
        if len(candidate) > _TG_LIMIT:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        return

    for i, chunk in enumerate(chunks, 1):
        cmd = [
            "openclaw", "message", "send",
            "--channel", channel,
            "--target", recipient,
            "--message", chunk,
        ]
        if account:
            cmd += ["--account", account]
        if dry_run:
            print(f"[loop] [dry-run] would send chunk {i}/{len(chunks)} to {target_str} ({len(chunk)} chars)")
            continue
        print(f"[loop] sending chunk {i}/{len(chunks)} to {target_str} ({len(chunk)} chars)", file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[loop] send failed: {proc.stderr.strip()}", file=sys.stderr)
        else:
            print(f"[loop] sent ok", file=sys.stderr)


def _loop_run_dir() -> Path:
    """Return today's loop output directory: data_dir/loop/YYYY-MM-DD/."""
    d = _CFG["loop_dir"] / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_step_output(step_name: str, text: str) -> None:
    """Write a step's output to the loop run directory."""
    if not text.strip():
        return
    out_file = _loop_run_dir() / f"{step_name}.md"
    out_file.write_text(text, encoding="utf-8")
    print(f"[loop] Saved {step_name} output → {out_file}", file=sys.stderr)


def run_step(label: str, cmd: list[str], *, allow_fail: bool = False,
              env_extra: dict | None = None) -> tuple[int, str]:
    """Run a subprocess step, printing status and returning captured stdout."""
    import os
    print(f"\n{'─' * 50}")
    print(f"▶ {label}")
    print(f"  {' '.join(cmd)}")
    print(f"{'─' * 50}")
    run_env = dict(os.environ)
    if env_extra:
        run_env.update(env_extra)
    result = subprocess.run(cmd, capture_output=True, text=True, env=run_env)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0 and not allow_fail:
        print(f"✗ {label} failed (exit {result.returncode})", file=sys.stderr)
    return result.returncode, result.stdout or ""


def _write_run_summary(data_dir: Path, summary: dict) -> None:
    """Write run summary to run_log.json (latest), runs.jsonl (history), and loop dir."""
    feed_dir = data_dir / "feed"
    feed_dir.mkdir(parents=True, exist_ok=True)

    run_log = feed_dir / "run_log.json"
    runs_jsonl = feed_dir / "runs.jsonl"

    try:
        run_log.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[loop] Could not write run_log.json: {exc}", file=sys.stderr)

    try:
        with runs_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
    except OSError as exc:
        print(f"[loop] Could not append to runs.jsonl: {exc}", file=sys.stderr)

    # Also save summary to the loop run directory
    try:
        loop_summary = _loop_run_dir() / "summary.json"
        loop_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[loop] Could not write loop summary: {exc}", file=sys.stderr)


# ── Step 1: Feed Digest ────────────────────────────────────────────────────


def run_digest(max_posts: int = 12, days: int = 7, skip_youtube: bool = False) -> dict:
    """Fetch feeds, rank, format digest.

    All sources fetch directly via API/subprocess — no browser automation.
      - HN: Algolia API (step 1a)
      - X profiles: Bird API (from:handle) (step 1b)
      - X home feed: Bird API (filter:follows) (step 1c)
      - YouTube: yt-dlp (step 1d)

    Returns a dict of crawl counts per source.
    """
    feed_raw = _CFG["feed_raw"]
    feeds_file = str(_CFG["feeds_file"])
    youtube_feed = str(feed_raw / "youtube_feed.json")
    hn_feed = str(feed_raw / "hn_feed.json")
    ranked_feed = str(feed_raw / "feed_ranked.json")
    sources_file = str(_CFG["sources_file"])
    feed_dir = _SCRIPTS_ROOT / "feed"

    crawl_counts: dict = {}

    # Step 1a: Fetch Hacker News stories directly (Algolia API — no browser needed).
    # HN posts are merged into feeds_file so they appear in digest alongside X/PH.
    _, _ = run_step(
        "Fetch Hacker News stories (Algolia API)",
        [sys.executable, str(feed_dir / "source" / "hn.py"),
         "--days", str(days), "--output", hn_feed],
        allow_fail=True,
    )
    hn_path = Path(hn_feed)
    hn_count = 0
    if hn_path.exists():
        try:
            hn_items = json.loads(hn_path.read_text())
            hn_count = len(hn_items)
            existing = []
            if Path(feeds_file).exists():
                existing = json.loads(Path(feeds_file).read_text())
            seen_urls = {p.get("url") for p in existing if p.get("url")}
            merged = list(existing)
            for item in hn_items:
                if item.get("url") and item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    merged.append(item)
            Path(feeds_file).write_text(json.dumps(merged, indent=2))
            print(f"[loop] Merged {hn_count} HN posts → feeds_file ({len(merged)} total)", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[loop] Could not merge HN feed: {exc}", file=sys.stderr)
    crawl_counts["hn"] = hn_count

    # Step 1b: Crawl X profile sources via Bird API (direct, no browser needed).
    # Falls back silently to browser instructions if credentials are absent.
    _, xp_out = run_step(
        "X profiles — Bird API (incremental)",
        [sys.executable, str(feed_dir / "refresh.py"),
         "--type", "x_profile", "--output", feeds_file],
        allow_fail=True,
    )
    crawl_counts["x_profiles"] = sum(
        int(m) for m in re.findall(r"Bird: fetched (\d+) tweets from @", xp_out)
    )

    # Step 1c: Fetch X home feed via Bird (filter:follows). Direct, no browser.
    _, xf_out = run_step(
        "X home feed — Bird filter:follows",
        [sys.executable, str(feed_dir / "refresh.py"), "--type", "x_feed"],
        allow_fail=True,
    )
    m = re.search(r"Bird: fetched (\d+) tweets from home feed", xf_out)
    crawl_counts["x_feed"] = int(m.group(1)) if m else 0

    # Step 1d: Fetch YouTube creator updates (direct via yt-dlp).
    if not skip_youtube:
        _, yt_out = run_step(
            "Fetch YouTube creator updates",
            [sys.executable, str(feed_dir / "source" / "youtube.py"),
             "--file", sources_file, "--days", str(days),
             "--output", youtube_feed],
            allow_fail=True,
        )
        try:
            yt_items = json.loads(yt_out)
            crawl_counts["youtube"] = len(yt_items) if isinstance(yt_items, list) else 0
        except (json.JSONDecodeError, ValueError):
            crawl_counts["youtube"] = 0

    # Step 1e: Crawl knowledge sources (RSS, sitemaps, YouTube transcripts, podcasts).
    _, kb_out = run_step(
        "Crawl knowledge sources (RSS / sitemap / YouTube / podcast)",
        [sys.executable, str(feed_dir / "refresh.py"), "--type", "knowledge"],
        allow_fail=True,
    )
    m = re.search(r"Knowledge: crawled (\d+) new articles", kb_out)
    crawl_counts["knowledge_articles"] = int(m.group(1)) if m else 0
    m = re.search(r"Knowledge: (\d+) new feed items merged", kb_out)
    crawl_counts["knowledge_feed_items"] = int(m.group(1)) if m else 0

    # Steps below depend on feeds_file being fully populated by the agent (X + PH).
    _, _ = run_step(
        "Rank and score posts",
        [sys.executable, str(feed_dir / "ranker.py"),
         "--input", feeds_file, "--output", ranked_feed],
        allow_fail=True,
    )

    _, digest_output = run_step(
        "Format digest",
        [sys.executable, str(feed_dir / "digest.py"),
         "--input", feeds_file, "--youtube-input", youtube_feed,
         "--max-posts", str(max_posts)],
    )

    _save_step_output("digest", digest_output)

    # Print crawl stats summary
    total = sum(crawl_counts.values())
    stats_lines = ["", "─" * 50, "📊 Crawl stats"]
    label_map = [
        ("hn", "HN stories"),
        ("x_feed", "X home feed"),
        ("x_profiles", "X profiles"),
        ("youtube", "YouTube"),
        ("knowledge_feed_items", "Knowledge"),
    ]
    for key, label in label_map:
        if key in crawl_counts:
            stats_lines.append(f"  {label:<14} {crawl_counts[key]:>4} items")
    stats_lines.append(f"  {'Total':<14} {total:>4} items")
    stats_lines.append("─" * 50)
    print("\n".join(stats_lines))

    return crawl_counts


# ── Step 2: Engage ─────────────────────────────────────────────────────────


def _load_following(handle: str | None) -> tuple[set[str], str]:
    """Return (followed_handles, status) for the configured X user.

    status is one of: "ok", "no-handle", "no-creds", "fetch-failed".
    Empty set is returned in every non-ok status so the caller can decide
    how to surface that to the user.
    """
    if not handle:
        return set(), "no-handle"

    creds = load_x_credentials()
    if not creds.get("auth_token") or not creds.get("ct0"):
        return set(), "no-creds"
    bird_x.set_credentials(creds["auth_token"], creds["ct0"])

    followed = bird_x.fetch_following(handle)
    if not followed:
        return set(), "fetch-failed"
    return followed, "ok"


def run_engage():
    """X engagement: follow candidates, outbound reply candidates, inbound replies.

    Fetches broad candidate pools via Bird API and formats output for the agent
    to evaluate against the natural-language targets defined in CHANNELS.md.

    Part 1 — Follow candidates
        Passive pool from today's feeds.json + active Bird topic search.
        Agent evaluates against follow_target and selects follows_per_run best.

    Part 2 — Outbound reply candidates
        Recent posts from home feed, minimal filtering.
        Agent evaluates against outbound_target and selects outbound_suggestions_per_run best.

    Part 3 — Inbound replies
        Replies to the user's own recent tweets, ready for draft responses.
    """
    from datetime import timedelta, timezone as _tz

    x_cfg = load_channel_config("x")
    follows_cap: int = x_cfg.get("follows_per_run", 5)
    follow_target: str = x_cfg.get("follow_target", "")
    outbound_cap: int = x_cfg.get("outbound_suggestions_per_run", 5)
    outbound_target: str = x_cfg.get("outbound_target", "")
    inbound_cap: int | None = x_cfg.get("inbound_suggestions_per_run", None)
    inbound_no_cap: bool = x_cfg.get("inbound_no_cap", True)

    feeds_file = Path(_CFG["feeds_file"])
    sources_file = Path(_CFG["sources_file"])

    # Load tracked handles from sources.json
    tracked_handles: set[str] = set()
    if sources_file.exists():
        try:
            sources_data = json.loads(sources_file.read_text())
            for src in sources_data.get("x_profiles", []):
                h = src.get("handle", "").lstrip("@").lower()
                if h:
                    tracked_handles.add(h)
        except (json.JSONDecodeError, OSError):
            pass

    my_handle = load_channel_handle("x")
    followed, filter_status = _load_following(my_handle)
    if filter_status != "ok":
        print(f"[loop] follower filter unavailable: {filter_status}", file=sys.stderr)
    else:
        print(f"[loop] @{my_handle} follows {len(followed)} accounts on X", file=sys.stderr)

    self_exclude = {my_handle.lower()} if my_handle else set()
    exclude = tracked_handles | followed | self_exclude

    lines: list[str] = []       # full output — printed to stdout + saved to file
    tg_lines: list[str] = []   # compact output — sent to Telegram (no agent instructions / CLI commands)

    _SEP = "─" * 20

    def _followers_str(c: dict) -> str:
        f = c.get("followers", 0)
        if not f:
            return "? followers"
        if f >= 1000:
            return f"{f / 1000:.1f}k followers"
        return f"{f} followers"

    # ── Part 1: Follow candidates ──────────────────────────────────────────
    lines += ["", "═" * 50, "👥 ACCOUNTS TO FOLLOW", "═" * 50, ""]

    if follows_cap == 0:
        lines.append("  follows_per_run is 0 — follow step skipped.")
    else:
        if follow_target:
            lines += [f"  Target: {follow_target}", ""]

        # Passive pool: from today's feeds.json
        feed_pool: dict[str, dict] = {}
        if feeds_file.exists():
            try:
                for p in json.loads(feeds_file.read_text()):
                    if p.get("source") not in ("x", "x_feed") or not p.get("author"):
                        continue
                    h = p["author"].lstrip("@").lower()
                    if not h or h in exclude:
                        continue
                    text = p.get("content") or p.get("text") or ""
                    entry = feed_pool.setdefault(h, {"handle": h, "followers": 0, "sample": "", "count": 0})
                    entry["count"] += 1
                    if not entry["sample"] and len(text) > 20:
                        entry["sample"] = text[:120]
            except (json.JSONDecodeError, OSError):
                pass

        # Active pool: Bird topic search
        topic_pool: list[dict] = []
        if bird_x.is_available():
            try:
                topics = load_topics()
                keywords = [kw for kws in topics.values() for kw in kws[:2]][:8]
                if keywords:
                    print(f"[loop] Bird topic search for follow candidates...", file=sys.stderr)
                    topic_pool = bird_x.search_accounts_by_topic(keywords, count=80, since_days=7)
                    topic_pool = [c for c in topic_pool if c["handle"] not in exclude]
                    print(f"[loop] {len(topic_pool)} topic candidates found", file=sys.stderr)
            except Exception as e:
                print(f"[loop] Bird topic search failed: {e}", file=sys.stderr)

        # Merge: topic pool first (has follower counts), fill from feed pool
        seen: set[str] = set()
        all_candidates: list[dict] = []
        for c in topic_pool:
            if c["handle"] not in seen:
                seen.add(c["handle"])
                all_candidates.append(c)
        for h, c in feed_pool.items():
            if h not in seen:
                seen.add(h)
                all_candidates.append(c)

        if all_candidates:
            note = f"{len(all_candidates)} candidates found"
            if filter_status == "ok":
                note += f" (already-followed accounts filtered out)"
            else:
                note += f" (follower filter unavailable: {filter_status})"
            lines.append(note)
            lines.append(f"Cap: follow up to {follows_cap} per run.\n")
            for c in all_candidates:
                followers_disp = f"{c.get('followers', 0):,} followers" if c.get("followers") else "? followers"
                lines.append(f"  • @{c['handle']} — {followers_disp}")
                if c.get("sample"):
                    lines.append(f"    {c['sample'][:120]}")
                lines.append(f"    https://x.com/{c['handle']}")
                lines.append("")
            lines.append(
                f"Agent: evaluate each candidate against the follow_target above. "
                f"Select the best {follows_cap}. Then run:\n"
                f"  python3 scripts/act/engage.py --platform x --action follow "
                f"--handles '@handle1,@handle2,...' --account {my_handle or '_jyek'}"
            )

            # Compact Telegram version — cap at 20 candidates, no agent instructions
            tg_display = all_candidates[:20]
            tg_lines += [f"👥 Follow — {len(all_candidates)} candidates", _SEP, ""]
            for c in tg_display:
                tg_lines.append(f"@{c['handle']} · {_followers_str(c)}")
                if c.get("sample"):
                    tg_lines.append(c["sample"][:100])
                tg_lines.append(f"https://x.com/{c['handle']}")
                tg_lines.append("")
            if len(all_candidates) > 20:
                tg_lines.append(f"… and {len(all_candidates) - 20} more candidates")
                tg_lines.append("")
        else:
            lines.append("  No new follow candidates found.")
            tg_lines += [f"👥 Follow", _SEP, "No new follow candidates.", ""]

    # ── Part 2: Outbound reply candidates ─────────────────────────────────
    lines += ["", "═" * 50, "💬 OUTBOUND REPLY CANDIDATES", "═" * 50, ""]

    if outbound_cap == 0:
        lines.append("  outbound_suggestions_per_run is 0 — outbound step skipped.")
    elif not bird_x.is_available():
        lines.append("  Bird API unavailable — set credentials in ~/.hum/credentials/x.json.")
    else:
        if outbound_target:
            lines += [f"  Target: {outbound_target}", ""]

        try:
            since_2d = (datetime.now(_tz.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
            print("[loop] fetching home feed for outbound candidates...", file=sys.stderr)
            home_posts = bird_x.fetch_home_feed(since=since_2d, count=80)

            # Minimal filter: skip very short posts and pure retweets
            candidates = [
                p for p in home_posts
                if len(p.get("content", "")) >= 60 and not p.get("content", "").startswith("RT @")
            ]

            if candidates:
                lines.append(
                    f"{len(candidates)} posts from your home feed (last 48h). "
                    f"Cap: select up to {outbound_cap} to reply to.\n"
                )
                tg_lines += [f"💬 Outbound — {len(candidates)} posts", _SEP, ""]
                for i, p in enumerate(candidates, 1):
                    author = p.get("author", "")
                    content = p.get("content", "")
                    url = p.get("url", "")
                    likes = p.get("likes") or 0
                    replies = p.get("replies") or 0
                    lines.append(f"  {i}. {author} — {likes}♥ {replies}↩")
                    lines.append(f"     {content[:160]}")
                    lines.append(f"     {url}")
                    lines.append("")
                    tg_lines.append(f"{i}. {author} · {likes}♥ {replies}↩")
                    tg_lines.append(content[:120])
                    tg_lines.append(url)
                    tg_lines.append("")
                lines.append(
                    f"Agent: evaluate each post against the outbound_target above. "
                    f"Select the best {outbound_cap}, draft a reply, and present for approval before posting."
                )
            else:
                lines.append("  No suitable posts found in home feed (last 48h).")
                tg_lines += [f"💬 Outbound", _SEP, "No suitable posts found (last 48h).", ""]
        except Exception as e:
            lines.append(f"  Outbound fetch failed: {e}")
            tg_lines += [f"💬 Outbound", _SEP, f"Fetch failed: {e}", ""]
            print(f"[loop] outbound error: {e}", file=sys.stderr)

    # ── Part 3: Inbound replies ────────────────────────────────────────────
    lines += ["", "═" * 50, "📥 INBOUND REPLIES", "═" * 50, ""]

    if inbound_cap == 0 and not inbound_no_cap:
        lines.append("  inbound_suggestions_per_run is 0 — inbound step skipped.")
    elif not my_handle:
        lines.append("  No X handle configured in CHANNELS.md.")
    elif not bird_x.is_available():
        lines.append("  Bird API unavailable — set credentials in ~/.hum/credentials/x.json.")
    else:
        try:
            print(f"[loop] fetching replies to @{my_handle}...", file=sys.stderr)
            inbound = bird_x.fetch_replies_to_user(my_handle, since_days=3)
            if inbound_cap is not None:
                inbound = inbound[:inbound_cap]

            if inbound:
                lines.append(f"{len(inbound)} replies to your recent posts (last 3 days):\n")
                tg_lines += [f"📥 Inbound — {len(inbound)} replies", _SEP, ""]
                for i, r in enumerate(inbound, 1):
                    lines.append(f"  {i}. {r['reply_author']} replied to:")
                    lines.append(f"     \"{r['original_tweet'][:80]}\"")
                    lines.append(f"     → {r['reply_text'][:200]}")
                    lines.append(f"     {r['reply_url']}")
                    lines.append("")
                    tg_lines.append(f"{i}. {r['reply_author']} → \"{r['original_tweet'][:60]}...\"")
                    tg_lines.append(r['reply_text'][:160])
                    tg_lines.append(r['reply_url'])
                    tg_lines.append("")
                lines.append(
                    "Agent: draft a response to each reply and present for approval before posting."
                )
            else:
                lines.append("  No unanswered replies found in the last 3 days.")
                tg_lines += [f"📥 Inbound", _SEP, "No unanswered replies (last 3 days).", ""]
        except Exception as e:
            lines.append(f"  Inbound fetch failed: {e}")
            tg_lines += [f"📥 Inbound", _SEP, f"Fetch failed: {e}", ""]
            print(f"[loop] inbound error: {e}", file=sys.stderr)

    full_text = "\n".join(lines)
    tg_text = "\n".join(tg_lines)
    print(full_text)
    _save_step_output("engage", full_text)
    # Store compact version for Telegram delivery
    _loop_run_dir().joinpath("engage_tg.md").write_text(tg_text, encoding="utf-8")


# ── Step 3: Brainstorm ─────────────────────────────────────────────────────


def run_brainstorm():
    """Surface top ideas from feed and ask about topics/posts.

    Outputs prompts for the agent to present to the user.
    """
    create_dir = _SCRIPTS_ROOT / "create"

    _, brainstorm_output = run_step(
        "Filter feed for brainstorm ideas",
        [sys.executable, str(create_dir / "brainstorm.py"), "--max", "8"],
        allow_fail=True,
    )

    lines = [
        "",
        "═" * 50,
        "💡 CONTENT BRAINSTORM",
        "═" * 50,
        "",
        "Actions for the agent:",
        "  1. Present the top feed items above as inspiration",
        "  2. Ask: 'Any topics you want to add to the pipeline?'",
        "  3. Ask: 'Want to work on any posts today?'",
        "  4. If yes, run /hum create for the chosen idea",
    ]
    text = "\n".join(lines)
    print(text)
    full_text = brainstorm_output + text
    _save_step_output("brainstorm", full_text)
    # Compact version for Telegram: just the scored items, no agent instructions
    tg_text = brainstorm_output.strip()
    if tg_text:
        tg_file = _loop_run_dir() / "brainstorm_tg.md"
        tg_file.write_text(tg_text, encoding="utf-8")


# ── Step 4: Learn (Sundays only) ──────────────────────────────────────────


def run_learn():
    """Weekly strategy refresh — feed trends, algorithm research, context updates.

    Outputs instructions for the agent to execute the /learn command.
    """
    lines = [
        "",
        "═" * 50,
        "📚 WEEKLY LEARN (Sunday)",
        "═" * 50,
        "",
        "Actions for the agent:",
        "  1. Run the /hum learn command as defined in COMMANDS.md",
        "  2. Analyze feed trends and top-performing content",
        "  3. Research what X and LinkedIn algorithms currently favor",
        "  4. Update context files based on findings",
        "  5. Share key findings and recommended actions with the user",
    ]
    text = "\n".join(lines)
    print(text)
    _save_step_output("learn", text)


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Daily hum automation loop")
    parser.add_argument("--step", choices=["digest", "engage", "brainstorm", "learn"],
                        help="Run a single step instead of the full loop")
    parser.add_argument("--dry-run", action="store_true",
                        help="Format output but don't send")
    parser.add_argument("--max-posts", type=int, default=12,
                        help="Max posts in digest (default: 12)")
    parser.add_argument("--days", type=int, default=7,
                        help="YouTube lookback days (default: 7)")
    parser.add_argument("--skip-youtube", action="store_true",
                        help="Skip YouTube fetch in digest step")
    args = parser.parse_args()

    is_sunday = datetime.now().weekday() == 6

    if args.step:
        # Run a single step
        digest_target = _CFG.get("digest_target")
        brainstorm_target = _CFG.get("brainstorm_target")
        engage_target = _CFG.get("engage_target")
        if args.step == "digest":
            run_digest(args.max_posts, args.days, args.skip_youtube)
            digest_file = _loop_run_dir() / "digest.md"
            if digest_target and digest_file.exists():
                _send_to_target(digest_target, digest_file.read_text(), dry_run=args.dry_run)
        elif args.step == "engage":
            run_engage()
            engage_tg_file = _loop_run_dir() / "engage_tg.md"
            if engage_target and engage_tg_file.exists():
                _send_to_target(engage_target, engage_tg_file.read_text(), dry_run=args.dry_run)
        elif args.step == "brainstorm":
            run_brainstorm()
            brainstorm_tg_file = _loop_run_dir() / "brainstorm_tg.md"
            if brainstorm_target and brainstorm_tg_file.exists():
                _send_to_target(brainstorm_target, brainstorm_tg_file.read_text(), dry_run=args.dry_run)
        elif args.step == "learn":
            run_learn()
        return

    # Full daily loop
    print("🌅 Hum Daily Loop")
    print(f"   {datetime.now().strftime('%A, %d %B %Y %H:%M')}")
    if is_sunday:
        print("   📚 Sunday — includes weekly strategy refresh")
    print()

    run_ts = datetime.now(timezone.utc).astimezone().isoformat()
    steps: dict = {}
    errors: list[str] = []
    digest_target = _CFG.get("digest_target")
    brainstorm_target = _CFG.get("brainstorm_target")
    engage_target = _CFG.get("engage_target")

    # Step 1: Digest
    t0 = time.time()
    try:
        counts = run_digest(args.max_posts, args.days, args.skip_youtube)
        steps["digest"] = {"status": "ok", "duration_s": round(time.time() - t0, 1), "counts": counts}
        digest_file = _loop_run_dir() / "digest.md"
        if digest_target and digest_file.exists():
            _send_to_target(digest_target, digest_file.read_text(), dry_run=args.dry_run)
    except Exception as exc:
        msg = f"[loop] digest failed: {exc}"
        print(msg, file=sys.stderr)
        steps["digest"] = {"status": "error", "duration_s": round(time.time() - t0, 1)}
        errors.append(msg)

    # Step 2: Engage
    t0 = time.time()
    try:
        run_engage()
        steps["engage"] = {"status": "ok", "duration_s": round(time.time() - t0, 1)}
        engage_tg_file = _loop_run_dir() / "engage_tg.md"
        if engage_target and engage_tg_file.exists():
            _send_to_target(engage_target, engage_tg_file.read_text(), dry_run=args.dry_run)
    except Exception as exc:
        msg = f"[loop] engage failed: {exc}"
        print(msg, file=sys.stderr)
        steps["engage"] = {"status": "error", "duration_s": round(time.time() - t0, 1)}
        errors.append(msg)

    # Step 3: Brainstorm
    t0 = time.time()
    try:
        run_brainstorm()
        steps["brainstorm"] = {"status": "ok", "duration_s": round(time.time() - t0, 1)}
        brainstorm_tg_file = _loop_run_dir() / "brainstorm_tg.md"
        if brainstorm_target and brainstorm_tg_file.exists():
            _send_to_target(brainstorm_target, brainstorm_tg_file.read_text(), dry_run=args.dry_run)
    except Exception as exc:
        msg = f"[loop] brainstorm failed: {exc}"
        print(msg, file=sys.stderr)
        steps["brainstorm"] = {"status": "error", "duration_s": round(time.time() - t0, 1)}
        errors.append(msg)

    # Step 4: Learn (Sundays only)
    if is_sunday:
        t0 = time.time()
        try:
            run_learn()
            steps["learn"] = {"status": "ok", "duration_s": round(time.time() - t0, 1)}
        except Exception as exc:
            msg = f"[loop] learn failed: {exc}"
            print(msg, file=sys.stderr)
            steps["learn"] = {"status": "error", "duration_s": round(time.time() - t0, 1)}
            errors.append(msg)

    summary = {
        "timestamp": run_ts,
        "status": "error" if errors else "ok",
        "steps": steps,
        "errors": errors,
    }
    data_dir = _CFG.get("data_dir")
    if data_dir:
        _write_run_summary(Path(data_dir), summary)

    print("\n" + "═" * 50)
    print("✓ Daily loop complete.")
    if is_sunday:
        print("  Includes: digest + engage + brainstorm + learn")
    else:
        print("  Includes: digest + engage + brainstorm")
    print("═" * 50)


if __name__ == "__main__":
    main()
