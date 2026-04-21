"""
blocklist.py — Shared author blocklist for digest and engage.

Stored at <data_dir>/feed/assets/blocklist.json as:
    {"authors": ["@handle1", "name without @", ...]}

Matching is case-insensitive and tolerant of a leading '@'. A raw handle
string and '@handle' both match the same blocklist entry.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config


def blocklist_path() -> str:
    cfg = load_config()
    return str(cfg["feed_assets"] / "blocklist.json")


def _normalize(handle: str) -> str:
    return (handle or "").strip().lstrip("@").lower()


def load_blocklist() -> dict:
    path = blocklist_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {"authors": list(data.get("authors", []))}
        except (json.JSONDecodeError, OSError):
            pass
    return {"authors": []}


def save_blocklist(data: dict) -> None:
    path = blocklist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Store lowercased, @-free canonical form + preserve original casing on first seen
    authors = data.get("authors", [])
    seen = {}
    for a in authors:
        key = _normalize(a)
        if key and key not in seen:
            seen[key] = a.strip()
    out = {"authors": sorted(seen.values(), key=str.lower)}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)


def is_blocked(author: str, blocklist: dict | None = None) -> bool:
    if blocklist is None:
        blocklist = load_blocklist()
    target = _normalize(author)
    if not target:
        return False
    return any(_normalize(a) == target for a in blocklist.get("authors", []))


def add(handle: str) -> tuple[bool, dict]:
    """Add a handle. Returns (was_new, blocklist)."""
    data = load_blocklist()
    if is_blocked(handle, data):
        return False, data
    data["authors"].append(handle.strip())
    save_blocklist(data)
    return True, load_blocklist()


def remove(handle: str) -> tuple[bool, dict]:
    """Remove a handle. Returns (was_present, blocklist)."""
    data = load_blocklist()
    target = _normalize(handle)
    before = len(data["authors"])
    data["authors"] = [a for a in data["authors"] if _normalize(a) != target]
    changed = len(data["authors"]) != before
    if changed:
        save_blocklist(data)
    return changed, load_blocklist()
