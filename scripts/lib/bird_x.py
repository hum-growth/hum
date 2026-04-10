"""Bird X search client for hum feed scraping.

Uses a vendored subset of @steipete/bird v0.8.0 (MIT License) to search X
via Twitter's GraphQL API. Requires AUTH_TOKEN and CT0 from an active X
browser session.

Credentials are loaded in priority order:
  1. HUM_X_AUTH_TOKEN / HUM_X_CT0 environment variables
  2. ~/.hum/credentials/x.json → "auth_token" / "ct0" keys
  3. AUTH_TOKEN / CT0 environment variables (shared with last30days)
"""

import json
import os
import shutil
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

_BIRD_SEARCH_MJS = Path(__file__).parent / "vendor" / "bird-search" / "bird-search.mjs"

_credentials: dict[str, str] = {}


def set_credentials(auth_token: str | None, ct0: str | None) -> None:
    """Inject AUTH_TOKEN/CT0 so Node subprocesses can use them."""
    if auth_token:
        _credentials["AUTH_TOKEN"] = auth_token
    if ct0:
        _credentials["CT0"] = ct0


def _has_credentials() -> bool:
    creds = _subprocess_env()
    return bool(creds.get("AUTH_TOKEN") and creds.get("CT0"))


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_credentials)
    return env


def is_available() -> bool:
    """Return True if bird-search.mjs exists, Node is in PATH, and credentials are set."""
    if not _BIRD_SEARCH_MJS.exists():
        return False
    if not shutil.which("node"):
        return False
    return _has_credentials()


def _run(query: str, count: int, timeout: int) -> dict[str, Any]:
    """Run bird-search.mjs and return parsed JSON response."""
    cmd = ["node", str(_BIRD_SEARCH_MJS), query, "--count", str(count), "--json"]
    preexec = os.setsid if hasattr(os, "setsid") else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=preexec,
            env=_subprocess_env(),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            proc.wait(timeout=5)
            return {"error": f"timed out after {timeout}s", "items": []}

        if proc.returncode != 0:
            return {"error": (stderr or "").strip() or "bird search failed", "items": []}

        output = (stdout or "").strip()
        return json.loads(output) if output else {"items": []}

    except json.JSONDecodeError as e:
        return {"error": f"invalid JSON: {e}", "items": []}
    except Exception as e:
        return {"error": str(e), "items": []}


def _normalize(raw_items: list[dict], handle: str = "") -> list[dict]:
    """Convert raw Bird tweet objects to hum feed item format."""
    items = []
    for tweet in raw_items:
        if not isinstance(tweet, dict):
            continue

        # URL
        url = tweet.get("permanent_url") or tweet.get("url", "")
        if not url and tweet.get("id"):
            author = tweet.get("author", {}) or tweet.get("user", {})
            screen_name = author.get("username") or author.get("screen_name", "")
            if screen_name:
                url = f"https://x.com/{screen_name}/status/{tweet['id']}"
        if not url:
            continue

        # Date
        date = None
        created_at = tweet.get("createdAt") or tweet.get("created_at", "")
        if created_at:
            try:
                if len(created_at) > 10 and created_at[10] == "T":
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                else:
                    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                date = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Author
        author = tweet.get("author", {}) or tweet.get("user", {})
        author_handle = (
            author.get("username") or author.get("screen_name", "") or handle
        ).lstrip("@")

        text = str(tweet.get("text") or tweet.get("full_text") or "").strip()

        items.append({
            "source": "x",
            "author": f"@{author_handle}",
            "text": text[:500],
            "url": url,
            "timestamp": date,
            "likes": _int(tweet.get("likeCount") or tweet.get("like_count") or tweet.get("favorite_count")),
            "retweets": _int(tweet.get("retweetCount") or tweet.get("retweet_count")),
            "replies": _int(tweet.get("replyCount") or tweet.get("reply_count")),
            "views": _int(tweet.get("viewCount") or tweet.get("view_count")),
            "media": [],
        })
    return items


def _int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def fetch_profile(handle: str, since: str | None = None, count: int = 20, timeout: int = 30) -> list[dict]:
    """Fetch recent posts from an X profile via Bird.

    Args:
        handle: X handle (without @)
        since: ISO 8601 date string (YYYY-MM-DD) — only fetch tweets after this date
        count: Max number of tweets to fetch
        timeout: Seconds before giving up

    Returns:
        List of normalized hum feed items, or empty list on failure.
    """
    handle = handle.lstrip("@")
    query = f"from:{handle}"
    if since:
        date = since[:10]  # trim to YYYY-MM-DD
        query += f" since:{date}"

    response = _run(query, count, timeout)

    if response.get("error"):
        return []

    raw = response if isinstance(response, list) else response.get("items", response.get("tweets", []))
    return _normalize(raw if isinstance(raw, list) else [], handle=handle)
