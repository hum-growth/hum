"""atomic_io.py — Crash-safe JSON write helpers for hum.

The previous in-place write pattern (`Path(p).write_text(json.dumps(...))`) leaves
truncated/corrupt files when a process is killed mid-write. The next run then
either errors out or silently treats the corrupt file as empty (because most
load sites catch JSONDecodeError and fall back to []), losing accumulated state.

These helpers fix that with a write-temp-then-rename pattern, and add a
dedupe-key-based merge so per-step retries don't duplicate items.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write JSON to `path` atomically.

    Writes to a sibling temp file in the same directory (so `os.replace` is a
    same-filesystem rename, not a copy), fsyncs the data, then renames over the
    target. A SIGKILL between fsync and rename leaves the original file intact;
    a SIGKILL after rename leaves the new file complete. The corrupt-mid-write
    state is no longer reachable.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def atomic_merge_json(
    path: Path,
    new_items: list[dict],
    dedupe_key: Callable[[dict], str | None],
) -> tuple[int, int]:
    """Atomically merge `new_items` into the JSON list at `path`.

    Reads the existing list (treating a missing/corrupt/non-list file as []),
    deduplicates the union by `dedupe_key(item)`, and writes the result with
    `atomic_write_json`. Items whose key is None or empty fall through and are
    appended without dedupe — same behavior as the prior URL-based merge for
    items that lacked URLs.

    Returns `(added, total)`.
    """
    path = Path(path)
    existing: list[dict] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        except (json.JSONDecodeError, OSError):
            existing = []

    seen: set[str] = set()
    merged: list[dict] = []
    for item in existing + new_items:
        if not isinstance(item, dict):
            continue
        key = dedupe_key(item)
        if key:
            if key in seen:
                continue
            seen.add(key)
        merged.append(item)

    added = len(merged) - len(existing)
    atomic_write_json(path, merged)
    return added, len(merged)


# ── Dedupe key strategy ─────────────────────────────────────────────────────
#
# Per-source stable keys, in priority order:
#   X tweet:    f"x:{tweet_id}"
#   HN story:   f"hn:{object_id}"
#   YouTube:    f"yt:{video_id}"
#   knowledge:  f"rss:{sha1(canonical_url)}"
#   fallback:   url-based sha1 (same shape as knowledge) so dedupe still works
#
# Items get this stamped at write time via `compute_dedupe_key`. Older items in
# feeds.json that predate this code lack the field; the URL fallback keeps them
# de-duplicating correctly during the migration window.

_UTM_RE = re.compile(r"^(utm_|fbclid$|gclid$|mc_eid$|mc_cid$)")
_YOUTUBE_VIDEO_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/))([\w-]{6,})"
)


def _canonical_url(url: str) -> str:
    """Strip trackers, fragments, trailing slashes from a URL for stable dedupe."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    cleaned_query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not _UTM_RE.match(k.lower())]
    )
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), netloc, path, cleaned_query, ""))


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def compute_dedupe_key(item: dict) -> str | None:
    """Return a stable per-item dedupe key, or None if no signal is available."""
    if not isinstance(item, dict):
        return None

    existing = item.get("dedupe_key")
    if existing:
        return existing

    source = (item.get("source") or "").lower()
    url = item.get("url") or ""

    tweet_id = item.get("tweet_id")
    if tweet_id:
        return f"x:{tweet_id}"

    if source == "hn":
        object_id = item.get("object_id")
        if object_id:
            return f"hn:{object_id}"

    if source == "youtube" or "youtube.com" in url or "youtu.be" in url:
        m = _YOUTUBE_VIDEO_ID_RE.search(url)
        if m:
            return f"yt:{m.group(1)}"

    canonical = _canonical_url(url)
    if canonical:
        prefix = "rss" if source in ("knowledge", "rss") else (source or "url")
        return f"{prefix}:{_sha1(canonical)}"

    return None


def stamp_dedupe_key(item: dict) -> dict:
    """Mutate `item` in place to add a `dedupe_key` field. Returns the item."""
    if isinstance(item, dict) and not item.get("dedupe_key"):
        key = compute_dedupe_key(item)
        if key:
            item["dedupe_key"] = key
    return item
