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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config

_CFG = load_config()


def run_step(label: str, cmd: list[str], *, allow_fail: bool = False) -> int:
    """Run a subprocess step, printing status."""
    print(f"\n{'─' * 50}")
    print(f"▶ {label}")
    print(f"  {' '.join(cmd)}")
    print(f"{'─' * 50}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0 and not allow_fail:
        print(f"✗ {label} failed (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def _write_run_summary(data_dir: Path, summary: dict) -> None:
    """Write run summary to run_log.json (latest) and append to runs.jsonl (history)."""
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


# ── Step 1: Feed Digest ────────────────────────────────────────────────────


def run_digest(max_posts: int = 12, days: int = 7, skip_youtube: bool = False):
    """Fetch feeds, rank, format digest.

    Browser-based sources (X, HN, PH) emit JSON instructions to stdout.
    The agent running this script should read those instructions and execute
    them via its browser tool, saving results to feeds_file before the
    ranker/digest steps run.
    """
    feed_raw = _CFG["feed_raw"]
    feeds_file = str(_CFG["feeds_file"])
    youtube_feed = str(feed_raw / "youtube_feed.json")
    hn_feed = str(feed_raw / "hn_feed.json")
    ranked_feed = str(feed_raw / "feed_ranked.json")
    sources_file = str(_CFG["sources_file"])
    feed_dir = _SCRIPTS_ROOT / "feed"

    # Step 1a: Fetch Hacker News stories directly (Algolia API — no browser needed).
    # HN posts are merged into feeds_file so they appear in digest alongside X/PH.
    run_step(
        "Fetch Hacker News stories (Algolia API)",
        [sys.executable, str(feed_dir / "source" / "hn.py"),
         "--days", str(days), "--output", hn_feed],
        allow_fail=True,
    )
    hn_path = Path(hn_feed)
    if hn_path.exists():
        try:
            hn_items = json.loads(hn_path.read_text())
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
            print(f"[loop] Merged {len(hn_items)} HN posts → feeds_file ({len(merged)} total)", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[loop] Could not merge HN feed: {exc}", file=sys.stderr)

    # Step 1b: Emit browser scraping instructions for X/Twitter feed.
    # The agent must execute these via browser tool and save to feeds_file.
    run_step(
        "X/Twitter feed — browser instructions (agent must execute)",
        [sys.executable, str(feed_dir / "refresh.py"),
         "--output", feeds_file],
    )

    # Step 1d: Fetch YouTube creator updates (direct via yt-dlp).
    if not skip_youtube:
        run_step(
            "Fetch YouTube creator updates",
            [sys.executable, str(feed_dir / "source" / "youtube.py"),
             "--file", sources_file, "--days", str(days),
             "--output", youtube_feed],
            allow_fail=True,
        )

    # Steps below depend on feeds_file being fully populated by the agent (X + PH).
    run_step(
        "Rank and score posts",
        [sys.executable, str(feed_dir / "ranker.py"),
         "--input", feeds_file, "--output", ranked_feed],
        allow_fail=True,
    )

    run_step(
        "Format digest",
        [sys.executable, str(feed_dir / "digest.py"),
         "--input", feeds_file, "--youtube-input", youtube_feed,
         "--max-posts", str(max_posts)],
    )


# ── Step 2: Engage ─────────────────────────────────────────────────────────


def run_engage():
    """Suggest accounts to follow and draft replies for approval.

    Outputs structured suggestions for the agent to present to the user.
    """
    print("\n" + "═" * 50)
    print("💬 ENGAGEMENT SUGGESTIONS")
    print("═" * 50)
    print()
    print("Review your recent posts on X and LinkedIn for new comments/replies.")
    print("Check feed sources for high-value accounts to follow.")
    print()
    print("Actions for the agent:")
    print("  1. Open X and LinkedIn in browser")
    print("  2. Check recent posts for unanswered comments")
    print("  3. Draft reply suggestions for user approval")
    print("  4. Suggest 3-5 new accounts to follow based on feed sources")
    print()
    print("Present all suggestions and wait for user approval before acting.")


# ── Step 3: Brainstorm ─────────────────────────────────────────────────────


def run_brainstorm():
    """Surface top ideas from feed and ask about topics/posts.

    Outputs prompts for the agent to present to the user.
    """
    create_dir = _SCRIPTS_ROOT / "create"

    run_step(
        "Filter feed for brainstorm ideas",
        [sys.executable, str(create_dir / "brainstorm.py"), "--max", "8"],
        allow_fail=True,
    )

    print("\n" + "═" * 50)
    print("💡 CONTENT BRAINSTORM")
    print("═" * 50)
    print()
    print("Actions for the agent:")
    print("  1. Present the top feed items above as inspiration")
    print("  2. Ask: 'Any topics you want to add to the pipeline?'")
    print("  3. Ask: 'Want to work on any posts today?'")
    print("  4. If yes, run /hum create for the chosen idea")


# ── Step 4: Learn (Sundays only) ──────────────────────────────────────────


def run_learn():
    """Weekly strategy refresh — feed trends, algorithm research, context updates.

    Outputs instructions for the agent to execute the /learn command.
    """
    print("\n" + "═" * 50)
    print("📚 WEEKLY LEARN (Sunday)")
    print("═" * 50)
    print()
    print("Actions for the agent:")
    print("  1. Run the /hum learn command as defined in COMMANDS.md")
    print("  2. Analyze feed trends and top-performing content")
    print("  3. Research what X and LinkedIn algorithms currently favor")
    print("  4. Update context files based on findings")
    print("  5. Share key findings and recommended actions with the user")


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
        if args.step == "digest":
            run_digest(args.max_posts, args.days, args.skip_youtube)
        elif args.step == "engage":
            run_engage()
        elif args.step == "brainstorm":
            run_brainstorm()
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

    # Step 1: Digest
    t0 = time.time()
    try:
        run_digest(args.max_posts, args.days, args.skip_youtube)
        steps["digest"] = {"status": "ok", "duration_s": round(time.time() - t0, 1)}
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
