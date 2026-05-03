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
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_channel_config, load_channel_handle, load_config, load_topics, load_x_credentials
from lib import bird_x
from lib.atomic_io import atomic_merge_json, atomic_write_json, compute_dedupe_key

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
            err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            if channel == "telegram" and _send_telegram_direct(account, recipient, chunk):
                print(f"[loop] sent ok via Telegram Bot API fallback", file=sys.stderr)
                continue
            print(f"[loop] send failed: {err}", file=sys.stderr)
            raise RuntimeError(f"send failed for {target_str} chunk {i}/{len(chunks)}: {err}")
        print(f"[loop] sent ok", file=sys.stderr)


def _send_telegram_direct(account: str | None, recipient: str, text: str) -> bool:
    """Fallback Telegram sender for when the OpenClaw CLI plugin loader is broken.

    The daily loop runs outside the agent process, so its normal delivery path is
    `openclaw message send`. If OpenClaw's staged plugin runtime is temporarily
    broken, we can still deliver the digest through Telegram's Bot API using the
    bot token already configured in openclaw.json.
    """
    token = _load_openclaw_telegram_bot_token(account)
    if not token:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": recipient,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("ok"))
    except Exception as exc:
        print(f"[loop] Telegram Bot API fallback failed: {exc}", file=sys.stderr)
        return False


def _load_openclaw_telegram_bot_token(account: str | None) -> str | None:
    oc_path = Path.home() / ".openclaw" / "openclaw.json"
    try:
        with oc_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    telegram = cfg.get("channels", {}).get("telegram", {})
    accounts = telegram.get("accounts", {})
    account_cfg = None
    if account:
        account_cfg = accounts.get(account)
    elif len(accounts) == 1:
        account_cfg = next(iter(accounts.values()))
    if not isinstance(account_cfg, dict):
        return None
    token = account_cfg.get("botToken")
    return token if isinstance(token, str) and token else None


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
        atomic_write_json(run_log, summary)
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
        atomic_write_json(loop_summary, summary)
    except OSError as exc:
        print(f"[loop] Could not write loop summary: {exc}", file=sys.stderr)


# ── Step 1: Feed Digest ────────────────────────────────────────────────────


def run_digest(max_posts: int = 12, days: int = 7, skip_youtube: bool = False, include_seen: bool = False) -> dict:
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
            added, total = atomic_merge_json(Path(feeds_file), hn_items, compute_dedupe_key)
            print(f"[loop] Merged {added}/{hn_count} new HN posts → feeds_file ({total} total)", file=sys.stderr)
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

    digest_cmd = [sys.executable, str(feed_dir / "digest.py"),
                  "--input", ranked_feed, "--youtube-input", youtube_feed,
                  "--max-posts", str(max_posts)]
    if include_seen:
        digest_cmd.append("--include-seen")

    _, digest_output = run_step(
        "Format digest",
        digest_cmd,
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


def _save_following_cache(loop_dir: Path, handles: set[str]) -> None:
    cache_file = loop_dir / "following_cache.json"
    try:
        atomic_write_json(cache_file, {"handles": sorted(handles)})
    except OSError:
        pass


def _load_following_cache(loop_dir: Path) -> set[str]:
    cache_file = loop_dir / "following_cache.json"
    if cache_file.exists():
        try:
            return set(json.loads(cache_file.read_text()).get("handles", []))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _load_following(handle: str | None, loop_dir: Path | None = None) -> tuple[set[str], str]:
    """Return (followed_handles, status) for the configured X user.

    status is one of: "ok", "no-handle", "no-creds", "fetch-failed".
    Falls back to a disk cache (following_cache.json) when the live fetch
    returns empty, so already-followed accounts are excluded even on API glitches.
    """
    if not handle:
        return set(), "no-handle"

    creds = load_x_credentials()
    if not creds.get("auth_token") or not creds.get("ct0"):
        return set(), "no-creds"
    bird_x.set_credentials(creds["auth_token"], creds["ct0"])

    followed = bird_x.fetch_following(handle)
    if followed:
        if loop_dir:
            _save_following_cache(loop_dir, followed)
        return followed, "ok"

    # Live fetch failed — fall back to cached list
    if loop_dir:
        cached = _load_following_cache(loop_dir)
        if cached:
            print(f"[loop] fetch_following failed — using cached following list ({len(cached)} handles)", file=sys.stderr)
            return cached, "ok"

    return set(), "fetch-failed"


def _load_follow_history(loop_dir: Path) -> dict[str, str]:
    """Return {handle: iso_date_last_suggested} from follow_history.json."""
    hist_file = loop_dir / "follow_history.json"
    if hist_file.exists():
        try:
            return json.loads(hist_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_follow_history(loop_dir: Path, history: dict[str, str]) -> None:
    hist_file = loop_dir / "follow_history.json"
    try:
        atomic_write_json(hist_file, history)
    except OSError:
        pass


def _load_audience() -> str:
    """Return AUDIENCE.md contents (truncated) for LLM context, or ''."""
    audience_file = _CFG["data_dir"] / "AUDIENCE.md"
    if not audience_file.exists():
        return ""
    try:
        return audience_file.read_text(encoding="utf-8")[:1500]
    except OSError:
        return ""


def _load_voice() -> str:
    """Return VOICE.md contents (truncated) for LLM context, or ''."""
    voice_file = _CFG["data_dir"] / "VOICE.md"
    if not voice_file.exists():
        return ""
    try:
        return voice_file.read_text(encoding="utf-8")[:1200]
    except OSError:
        return ""


def _llm_chat(system: str, user: str) -> str | None:
    """Run a one-shot LLM turn via openclaw's configured default model.

    Shells out to `openclaw capability model run --prompt ... --json`, which uses
    whatever provider/model openclaw is configured with (no API key needed in
    this script's environment). Returns the assistant's text response, or None
    if the call fails.
    """
    prompt = f"{system}\n\n---\n\n{user}"
    cmd = ["openclaw", "capability", "model", "run", "--prompt", prompt, "--json"]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"[loop] openclaw model run failed ({exc})", file=sys.stderr)
        return None

    if proc.returncode != 0:
        stderr = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "no stderr"
        print(f"[loop] openclaw model run exit {proc.returncode}: {stderr}", file=sys.stderr)
        return None

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[loop] openclaw model run returned invalid JSON", file=sys.stderr)
        return None

    if not data.get("ok"):
        return None

    outputs = data.get("outputs") or []
    parts = [o.get("text", "") for o in outputs if isinstance(o, dict) and o.get("text")]
    text = "".join(parts).strip()
    return text or None


def _draft_replies(posts: list[dict], voice_text: str, audience_text: str, outbound_target: str) -> dict[str, str]:
    """Draft short replies for outbound posts via openclaw's default model.

    Returns a dict mapping post URL to suggested reply text. Returns {} if the
    LLM call fails.
    """
    if not posts:
        return {}

    post_blocks = []
    for i, p in enumerate(posts, 1):
        author = p.get("author", "")
        content = p.get("content", "")[:300]
        url = p.get("url", "")
        post_blocks.append(f"POST {i} by {author}:\n{content}\nURL: {url}")

    system_prompt = (
        "You are drafting outbound replies on X for a finance operator. Each reply must:\n"
        "- Anchor to something specific in the post (a stat, claim, or phrase)\n"
        "- Add value: a data point, contrarian take with reason, or concrete example\n"
        "- Sound human — no filler openers like 'Great point!', 'Love this', 'So true'\n"
        "- Be concise (under 280 characters)\n"
        "- Match this voice:\n\n"
        f"{voice_text[:800]}\n\n"
        f"Audience the user is building for:\n{audience_text[:800]}\n\n"
        f"Outbound target: {outbound_target}\n\n"
        "Output format — one reply per line, numbered to match:\n"
        "1. [reply text]\n"
        "2. [reply text]\n"
        "...\n"
    )

    raw = _llm_chat(system_prompt, "\n\n".join(post_blocks))
    if not raw:
        return {}

    replies: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"(\d+)[.)]\s*(.*)", line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(posts):
                post_url = posts[idx].get("url", "")
                if post_url:
                    replies[post_url] = m.group(2).strip()

    print(f"[loop] drafted {len(replies)} outbound replies", file=sys.stderr)
    return replies


def _score_follow_candidates(
    candidates: list[dict],
    follow_target: str,
    audience_text: str,
    cap: int,
    fresh_handles: set[str] | None = None,
) -> list[dict]:
    """Rank follow candidates against follow_target + audience via openclaw's default model.

    Returns up to `cap` {handle, reason} dicts. Falls back to the first `cap`
    candidates (no reason) if the LLM call fails.

    fresh_handles: accounts never previously suggested — the LLM is asked to
    include at least 1/3 of these to ensure variety across runs.
    """
    if not candidates:
        return []

    fresh_handles = fresh_handles or set()
    fresh_floor = max(1, cap // 3)
    has_fresh = any(c["handle"] in fresh_handles for c in candidates)

    cand_lines = []
    for i, c in enumerate(candidates, 1):
        followers = c.get("followers", 0)
        sample = c.get("sample", "")[:120]
        tag = " [FRESH]" if c["handle"] in fresh_handles else ""
        cand_lines.append(f"{i}. @{c['handle']} ({followers} followers){tag}: {sample}")

    fresh_instruction = (
        f"At least {fresh_floor} of your picks must be tagged [FRESH] (accounts never "
        "suggested before) to introduce variety — unless fewer than that many qualify. "
    ) if has_fresh else ""

    system_prompt = (
        "You are evaluating X/Twitter accounts as potential follows for a finance operator.\n\n"
        f"Audience the user is building for:\n{audience_text[:800]}\n\n"
        f"Follow target: {follow_target}\n\n"
        f"Select the best {cap} accounts from the numbered list below. You MUST only pick "
        "handles that appear in that list — do not invent or suggest any handle not present. "
        "Reject spam, news bots, engagement farmers, and unrelated noise. Prefer real "
        f"practitioners who match the target above. {fresh_instruction}"
        f"If fewer than {cap} accounts genuinely qualify, return fewer. If none qualify, "
        "return nothing.\n\n"
        "For each pick, output one line:\n"
        "handle | one-line reason tied to the target/audience\n\n"
        "Output ONLY the selected accounts, nothing else. No numbering."
    )

    raw = _llm_chat(system_prompt, "\n".join(cand_lines))
    if not raw:
        return [{"handle": c["handle"], "reason": ""} for c in candidates[:cap]]

    results: list[dict] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("0123456789.) ")
        if "|" in line:
            handle_part, reason = line.split("|", 1)
            handle = handle_part.strip().lstrip("@").lower()
            results.append({"handle": handle, "reason": reason.strip()})
        elif line:
            handle = line.strip().lstrip("@").split()[0].lower()
            results.append({"handle": handle, "reason": ""})

    print(f"[loop] scored {len(results)} follow candidates", file=sys.stderr)
    return results[:cap]


def _score_outbound_posts(
    posts: list[dict],
    outbound_target: str,
    audience_text: str,
    cap: int,
) -> list[dict]:
    """Rank outbound posts against outbound_target + audience via openclaw's default model.

    Returns up to `cap` posts in ranked order. Falls back to the first `cap`
    posts unchanged if the LLM call fails.
    """
    if not posts:
        return []

    by_url = {p.get("url", ""): p for p in posts if p.get("url")}
    cand_lines = []
    for i, p in enumerate(posts, 1):
        author = p.get("author", "")
        content = (p.get("content") or "").replace("\n", " ")[:200]
        likes = p.get("likes") or 0
        replies = p.get("replies") or 0
        purl = p.get("url", "")
        cand_lines.append(f"{i}. {author} ({likes}♥ {replies}↩) {purl}\n   {content}")

    system_prompt = (
        "You are picking outbound reply candidates on X for a finance operator.\n\n"
        f"Audience the user is building for:\n{audience_text[:800]}\n\n"
        f"Outbound target: {outbound_target}\n\n"
        f"Select the best {cap} posts from the list below. Reject motivational fluff, "
        "pure news reposts, engagement bait, and posts unrelated to the target/audience. "
        "Prefer posts making a specific, debatable claim where a thoughtful reply adds value. "
        f"If fewer than {cap} posts genuinely qualify, return fewer.\n\n"
        "Output one URL per line, ranked best first. URLs only, no commentary."
    )

    raw = _llm_chat(system_prompt, "\n\n".join(cand_lines))
    if not raw:
        return posts[:cap]

    picked: list[dict] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip().lstrip("0123456789.) -")
        m = re.search(r"https?://\S+", line)
        if not m:
            continue
        picked_url = m.group(0).rstrip(".,);")
        if picked_url in seen or picked_url not in by_url:
            continue
        seen.add(picked_url)
        picked.append(by_url[picked_url])
        if len(picked) >= cap:
            break

    print(f"[loop] scored {len(picked)} outbound candidates", file=sys.stderr)
    return picked or posts[:cap]


def _draft_inbound_replies(inbound: list[dict], voice_text: str, audience_text: str) -> dict[str, str]:
    """Draft replies to inbound replies via openclaw's default model.

    Returns a dict mapping reply URL to suggested response text. Returns {} if
    the LLM call fails.
    """
    if not inbound:
        return {}

    post_blocks = []
    for i, r in enumerate(inbound, 1):
        post_blocks.append(
            f"REPLY {i} by {r['reply_author']}:\n"
            f"Original tweet: {r['original_tweet'][:200]}\n"
            f"Their reply: {r['reply_text'][:300]}\n"
            f"URL: {r['reply_url']}"
        )

    system_prompt = (
        "You are drafting responses to replies on a finance operator's tweets. For each reply, "
        "draft a short response (1-2 sentences) that:\n"
        "- Engages with the commenter's specific point\n"
        "- Adds value — extends the point, shares an insight, or asks a follow-up\n"
        "- Sounds human — no filler ('Thanks!', 'Great point!')\n"
        "- Is concise (under 280 characters)\n"
        "- Matches this voice:\n\n"
        f"{voice_text[:800]}\n\n"
        f"Audience the user is building for:\n{audience_text[:800]}\n\n"
        "Output format — one reply per line, numbered to match:\n"
        "1. [reply text]\n"
        "2. [reply text]\n"
    )

    raw = _llm_chat(system_prompt, "\n\n".join(post_blocks))
    if not raw:
        return {}

    replies: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"(\d+)[.)]\s*(.*)", line)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(inbound):
                reply_url = inbound[idx].get("reply_url", "")
                if reply_url:
                    replies[reply_url] = m.group(2).strip()

    print(f"[loop] drafted {len(replies)} inbound replies", file=sys.stderr)
    return replies


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
    inbound_target: str = x_cfg.get("inbound_target", "")

    feeds_file = Path(_CFG["feeds_file"])
    sources_file = Path(_CFG["sources_file"])
    loop_dir = Path(_CFG["loop_dir"])

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
    followed, filter_status = _load_following(my_handle, loop_dir)
    if filter_status != "ok":
        print(f"[loop] follower filter unavailable: {filter_status}", file=sys.stderr)
    else:
        print(f"[loop] @{my_handle} follows {len(followed)} accounts on X", file=sys.stderr)

    # Follow suggestion history — exclude recently-suggested accounts for 14 days
    follow_history = _load_follow_history(loop_dir)
    today_str = datetime.now().strftime("%Y-%m-%d")
    cutoff_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    recently_suggested: set[str] = {h for h, d in follow_history.items() if d >= cutoff_date}

    self_exclude = {my_handle.lower()} if my_handle else set()
    exclude = tracked_handles | followed | self_exclude

    audience_text = _load_audience()
    voice_text = _load_voice()

    # Load blocked authors (weight = 0 in preferences.json)
    prefs_file = Path(_CFG["feed_assets"]) / "preferences.json"
    blocked_authors: set[str] = set()
    if prefs_file.exists():
        try:
            prefs = json.loads(prefs_file.read_text())
            for author, weight in prefs.get("authors", {}).items():
                if weight == 0:
                    blocked_authors.add(author.lstrip("@").lower())
        except (json.JSONDecodeError, OSError):
            pass

    def _truncate(text: str, n: int) -> str:
        text = " ".join((text or "").split())
        return text if len(text) <= n else text[: n - 1].rstrip() + "…"

    today = datetime.now().strftime("%a %d %b %Y")
    lines: list[str] = []

    # ── Part 1: Follow candidates ──────────────────────────────────────────
    lines.append(f"**👥 Hum Follow — {today}**")
    if follow_target:
        lines.append(f"Target: {_truncate(follow_target, 180)}")
    lines.append(f"Top {follows_cap}")
    lines.append("")

    if follows_cap == 0:
        lines.append("Skipped (follows_per_run is 0).")
    elif filter_status != "ok":
        follow_error_map = {
            "no-handle": "no X handle is configured",
            "no-creds": "X credentials are missing or incomplete",
            "fetch-failed": "the live X following-list refresh failed",
        }
        follow_error = follow_error_map.get(filter_status, f"unknown error: {filter_status}")
        lines.append(
            "Skipped follow suggestions because "
            f"{follow_error}. This avoids recommending accounts you may already follow."
        )
        lines.append(f"Error: {filter_status}")
        lines.append("")
    else:
        # Passive pool: from today's feeds.json
        feed_pool: dict[str, dict] = {}
        if feeds_file.exists():
            try:
                for p in json.loads(feeds_file.read_text()):
                    if p.get("source") != "x" or not p.get("author"):
                        continue
                    h = p["author"].lstrip("@").lower()
                    if not h or h in exclude or h in blocked_authors:
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
                    topic_pool = [c for c in topic_pool if c["handle"] not in exclude and c["handle"] not in blocked_authors]
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

        # Filter out recently-suggested accounts (14-day cooldown)
        before_filter = len(all_candidates)
        all_candidates = [c for c in all_candidates if c["handle"] not in recently_suggested]
        if before_filter != len(all_candidates):
            print(f"[loop] filtered {before_filter - len(all_candidates)} recently-suggested accounts", file=sys.stderr)

        # Tag candidates as fresh (never suggested) for variety enforcement
        fresh_handles = {c["handle"] for c in all_candidates if c["handle"] not in follow_history}
        print(f"[loop] {len(fresh_handles)} fresh / {len(all_candidates) - len(fresh_handles)} seen candidates", file=sys.stderr)

        if all_candidates:
            scored = _score_follow_candidates(
                all_candidates, follow_target, audience_text, follows_cap, fresh_handles
            )
            by_handle = {c["handle"]: c for c in all_candidates}
            # Discard any handles the LLM hallucinated that weren't in the candidate pool
            scored = [s for s in scored if s["handle"] in by_handle]
            for i, s in enumerate(scored, 1):
                base = by_handle.get(s["handle"], {})
                followers = base.get("followers", 0)
                if followers >= 1000:
                    fol = f"{followers / 1000:.1f}k followers"
                elif followers:
                    fol = f"{followers} followers"
                else:
                    fol = "? followers"
                reason = s.get("reason") or _truncate(base.get("sample", ""), 120) or "—"
                lines.append(f"{i}. @{s['handle']} ({fol})")
                lines.append(f"   {reason}")
                lines.append(f"   https://x.com/{s['handle']}")
                lines.append("")
            # Update suggestion history so these accounts enter the 14-day cooldown
            for s in scored:
                follow_history[s["handle"]] = today_str
            _save_follow_history(loop_dir, follow_history)
        else:
            lines.append("No new follow candidates.")
            lines.append("")

    # ── Part 2: Outbound reply candidates ─────────────────────────────────
    lines.append(f"**💬 Hum Outbound — {today}**")
    if outbound_target:
        lines.append(f"Target: {_truncate(outbound_target, 180)}")
    lines.append(f"Top {outbound_cap}")
    lines.append("")

    if outbound_cap == 0:
        lines.append("Skipped (outbound_suggestions_per_run is 0).")
        lines.append("")
    elif not bird_x.is_available():
        lines.append("Bird API unavailable — set credentials in ~/.hum/credentials/x.json.")
        lines.append("")
    else:
        try:
            since_2d = (datetime.now(_tz.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
            print("[loop] fetching home feed for outbound candidates...", file=sys.stderr)
            home_posts = bird_x.fetch_home_feed(since=since_2d, count=80)

            candidates = [
                p for p in home_posts
                if len(p.get("content", "")) >= 60 and not p.get("content", "").startswith("RT @")
            ]

            if candidates:
                top_posts = _score_outbound_posts(
                    candidates, outbound_target, audience_text, outbound_cap
                )
                reply_drafts = _draft_replies(
                    top_posts, voice_text, audience_text, outbound_target
                )
                for i, p in enumerate(top_posts, 1):
                    author = p.get("author", "")
                    content = _truncate(p.get("content", ""), 180)
                    url = p.get("url", "")
                    likes = p.get("likes") or 0
                    rcount = p.get("replies") or 0
                    lines.append(f"{i}. {author} — {likes}♥ {rcount}↩")
                    lines.append(f"   \"{content}\"")
                    lines.append(f"   {url}")
                    draft = reply_drafts.get(url, "")
                    if draft:
                        lines.append(f"   Reply: {draft}")
                    lines.append("")
            else:
                lines.append("No suitable posts in home feed (last 48h).")
                lines.append("")
        except Exception as e:
            lines.append(f"Outbound fetch failed: {e}")
            lines.append("")
            print(f"[loop] outbound error: {e}", file=sys.stderr)

    # ── Part 3: Inbound replies ────────────────────────────────────────────
    lines.append(f"**📥 Hum Inbound — {today}**")
    if inbound_target:
        lines.append(f"Target: {_truncate(inbound_target, 180)}")
    lines.append("")

    if inbound_cap == 0 and not inbound_no_cap:
        lines.append("Skipped (inbound_suggestions_per_run is 0).")
    elif not my_handle:
        lines.append("No X handle configured in CHANNELS.md.")
    elif not bird_x.is_available():
        lines.append("Bird API unavailable — set credentials in ~/.hum/credentials/x.json.")
    else:
        try:
            print(f"[loop] fetching replies to @{my_handle}...", file=sys.stderr)
            inbound = bird_x.fetch_replies_to_user(my_handle, since_days=3)
            if inbound_cap is not None:
                inbound = inbound[:inbound_cap]

            if inbound:
                inbound_drafts = _draft_inbound_replies(inbound, voice_text, audience_text)
                for i, r in enumerate(inbound, 1):
                    original = _truncate(r.get("original_tweet", ""), 80)
                    reply = _truncate(r.get("reply_text", ""), 200)
                    lines.append(f"{i}. {r['reply_author']} on \"{original}\":")
                    lines.append(f"   \"{reply}\"")
                    lines.append(f"   {r['reply_url']}")
                    draft = inbound_drafts.get(r["reply_url"], "")
                    if draft:
                        lines.append(f"   Reply: {draft}")
                    lines.append("")
            else:
                lines.append("No unanswered replies in the last 3 days.")
        except Exception as e:
            lines.append(f"Inbound fetch failed: {e}")
            print(f"[loop] inbound error: {e}", file=sys.stderr)

    full_text = "\n".join(lines).rstrip() + "\n"
    print(full_text)
    _save_step_output("engage", full_text)


# ── Step 3: Brainstorm ─────────────────────────────────────────────────────


def run_brainstorm():
    """Surface top topic ideas from feed + knowledge.

    The brainstorm.py script already produces the desired Telegram-shaped output
    (grouped by content pillar, scored by cross-pillar resonance + engagement).
    Save it verbatim as brainstorm.md.
    """
    create_dir = _SCRIPTS_ROOT / "create"

    _, brainstorm_output = run_step(
        "Filter feed for brainstorm ideas",
        [sys.executable, str(create_dir / "brainstorm.py"), "--max", "5"],
        allow_fail=True,
    )

    _save_step_output("brainstorm", brainstorm_output.strip() + "\n")


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


# ── Supervisor mode (cron entry point) ─────────────────────────────────────
#
# When invoked with --supervised, loop.py forks itself: the parent enforces a
# wall-clock timeout, holds a per-step lock, verifies expected output files
# after the child exits, and emits a single-line `HUM_RESULT` summary on
# stdout. The child runs the actual work via the same main() path without
# --supervised. This is the cron entry point — no bash wrapper required.
#
# Exit codes:
#   0    success (child exited 0 AND expected output files exist)
#   1    child returned non-zero (general step failure)
#   2    child returned 0 but expected output files are missing or empty
#   11   another instance holds the lock (concurrent run rejected)
#  124   wall-clock timeout (child SIGTERMed, then SIGKILLed after grace)

_EXPECTED_FILES = {
    "digest": ("digest.md",),
    "engage": ("engage.md",),
    "brainstorm": ("brainstorm.md",),
    "full": ("digest.md", "engage.md", "brainstorm.md"),
}


def _acquire_step_lock(lock_dir: Path) -> bool:
    """Atomic mkdir-based lock with stale-pid recovery.

    Portable across macOS and Linux (flock isn't on macOS by default).
    Returns True if the lock is now held by this process, False if a live
    holder owns it. A holder whose pid is no longer alive is reclaimed.
    """
    try:
        lock_dir.mkdir()
        (lock_dir / "pid").write_text(str(os.getpid()))
        return True
    except FileExistsError:
        pass

    pid_file = lock_dir / "pid"
    holder_alive = False
    try:
        old_pid = int(pid_file.read_text().strip())
        os.kill(old_pid, 0)  # raises if dead
        holder_alive = True
    except (FileNotFoundError, ValueError, OSError, ProcessLookupError):
        pass

    if holder_alive:
        return False

    # Holder is dead — reclaim.
    shutil.rmtree(lock_dir, ignore_errors=True)
    try:
        lock_dir.mkdir()
        (lock_dir / "pid").write_text(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def _read_lock_holder(lock_dir: Path) -> str:
    try:
        return (lock_dir / "pid").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def _child_argv(args) -> list[str]:
    """Reconstruct loop.py args for the child, dropping supervisor-only flags."""
    out: list[str] = []
    if args.step:
        out += ["--step", args.step]
    if args.dry_run:
        out.append("--dry-run")
    out += ["--max-posts", str(args.max_posts)]
    out += ["--days", str(args.days)]
    if args.skip_youtube:
        out.append("--skip-youtube")
    return out


def _supervise(args) -> int:
    """Lock + spawn child + enforce timeout + verify outputs + emit HUM_RESULT.

    Never invokes loop logic itself — the child does that. Always prints
    exactly one HUM_RESULT line on stdout and returns the wrapper exit code.
    """
    step = args.step or "full"
    lock_dir = Path(f"/tmp/hum-loop-{step}.lock.d")

    if not _acquire_step_lock(lock_dir):
        holder = _read_lock_holder(lock_dir)
        print(f"HUM_RESULT step={step} exit=11 file=na duration_s=0 "
              f"reason=lock_busy holder={holder}", flush=True)
        return 11

    try:
        cmd = [sys.executable, str(Path(__file__).resolve())] + _child_argv(args)
        # New session/process group so we can SIGTERM the whole subprocess tree
        # (loop.py spawns children for HN, X, YouTube, ranker, digest).
        preexec = os.setsid if hasattr(os, "setsid") else None

        start = time.time()
        proc = subprocess.Popen(cmd, preexec_fn=preexec)

        def _signal_group(sig: int) -> None:
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                pass

        try:
            child_exit = proc.wait(timeout=args.hard_timeout)
        except subprocess.TimeoutExpired:
            _signal_group(signal.SIGTERM)
            try:
                proc.wait(timeout=args.kill_grace)
            except subprocess.TimeoutExpired:
                _signal_group(signal.SIGKILL)
                proc.wait()
            child_exit = 124
        except KeyboardInterrupt:
            _signal_group(signal.SIGTERM)
            proc.wait()
            child_exit = 130

        duration = round(time.time() - start)

        loop_dir = Path(_CFG["loop_dir"]) / datetime.now().strftime("%Y-%m-%d")
        expected = _EXPECTED_FILES.get(step, ())
        missing = [
            name for name in expected
            if not (loop_dir / name).exists()
            or (loop_dir / name).stat().st_size == 0
        ]
        file_status = "ok" if not missing else "missing"

        # If the child reported success but produced nothing, that's a distinct
        # class of failure (silent cred rot, empty digest, etc.) — surface it
        # as exit=2 so cron alerts on it differently from a script crash.
        wrapper_exit = child_exit
        if child_exit == 0 and missing:
            wrapper_exit = 2

        line = (f"HUM_RESULT step={step} exit={wrapper_exit} file={file_status} "
                f"duration_s={duration}")
        if missing:
            line += f" missing={','.join(missing)}"
        print(line, flush=True)
        return wrapper_exit

    finally:
        shutil.rmtree(lock_dir, ignore_errors=True)


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
    parser.add_argument("--include-seen", action="store_true",
                        help="Include recently-sent items in digest formatting; useful for same-day manual resends")
    parser.add_argument("--supervised", action="store_true",
                        help="Cron entry point: fork self, enforce timeout + lock, "
                             "verify outputs, emit one HUM_RESULT line on stdout.")
    parser.add_argument("--hard-timeout", type=int, default=1100,
                        help="(--supervised) wall-clock seconds before SIGTERM (default: 1100)")
    parser.add_argument("--kill-grace", type=int, default=60,
                        help="(--supervised) seconds between SIGTERM and SIGKILL (default: 60)")
    args = parser.parse_args()

    if args.supervised:
        sys.exit(_supervise(args))

    is_sunday = datetime.now().weekday() == 6

    if args.step:
        # Run a single step
        digest_target = _CFG.get("digest_target")
        brainstorm_target = _CFG.get("brainstorm_target")
        engage_target = _CFG.get("engage_target")
        if args.step == "digest":
            run_digest(args.max_posts, args.days, args.skip_youtube, args.include_seen)
            digest_file = _loop_run_dir() / "digest.md"
            if digest_target and digest_file.exists():
                _send_to_target(digest_target, digest_file.read_text(), dry_run=args.dry_run)
        elif args.step == "engage":
            run_engage()
            engage_file = _loop_run_dir() / "engage.md"
            if engage_target and engage_file.exists():
                _send_to_target(engage_target, engage_file.read_text(), dry_run=args.dry_run)
        elif args.step == "brainstorm":
            run_brainstorm()
            brainstorm_file = _loop_run_dir() / "brainstorm.md"
            if brainstorm_target and brainstorm_file.exists():
                _send_to_target(brainstorm_target, brainstorm_file.read_text(), dry_run=args.dry_run)
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
        counts = run_digest(args.max_posts, args.days, args.skip_youtube, args.include_seen)
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
        engage_file = _loop_run_dir() / "engage.md"
        if engage_target and engage_file.exists():
            _send_to_target(engage_target, engage_file.read_text(), dry_run=args.dry_run)
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
        brainstorm_file = _loop_run_dir() / "brainstorm.md"
        if brainstorm_target and brainstorm_file.exists():
            _send_to_target(brainstorm_target, brainstorm_file.read_text(), dry_run=args.dry_run)
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
