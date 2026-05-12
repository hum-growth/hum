#!/usr/bin/env python3
"""Backfill: reflow YouTube transcript sections in existing knowledge files.

Walks the knowledge dir, finds .md files with `video_id:` in frontmatter, and
re-flows their `## Transcript` section using the same logic as new crawls
(strip [music]/[applause]/etc. cues, group ~4 sentences per paragraph).

Idempotent: re-running on already-reflowed files is a no-op.

Usage:
    python3 scripts/backfill_transcripts.py            # backfill in place
    python3 scripts/backfill_transcripts.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from config import load_config
from feed.source.handlers.youtube_transcript import reflow_transcript_text

_FRONTMATTER_RE = re.compile(r"^---\n(.+?)\n---\n", re.DOTALL)
_TRANSCRIPT_HEADING_RE = re.compile(r"^## Transcript\s*$", re.MULTILINE)


def _has_video_id(text: str) -> bool:
    m = _FRONTMATTER_RE.match(text)
    return bool(m and re.search(r"^video_id:\s*\S", m.group(1), re.MULTILINE))


def reflow_file(path: Path, dry_run: bool = False) -> str:
    """Return 'reflowed', 'unchanged', or 'skipped'."""
    text = path.read_text(encoding="utf-8")
    if not _has_video_id(text):
        return "skipped"
    m = _TRANSCRIPT_HEADING_RE.search(text)
    if not m:
        return "skipped"

    head = text[: m.end()].rstrip()
    body = text[m.end():].lstrip("\n")
    new_body = reflow_transcript_text(body)
    if not new_body:
        return "skipped"

    new_text = head + "\n\n" + new_body.rstrip() + "\n"
    if new_text == text:
        return "unchanged"
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return "reflowed"


def main() -> int:
    p = argparse.ArgumentParser(description="Reflow existing transcript files")
    p.add_argument("--dry-run", action="store_true", help="Report only, do not write")
    args = p.parse_args()

    cfg = load_config()
    kdir: Path = cfg["knowledge_dir"]
    if not kdir.exists():
        print(f"knowledge dir not found: {kdir}", file=sys.stderr)
        return 1

    counts = {"reflowed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    for f in sorted(kdir.rglob("*.md")):
        try:
            result = reflow_file(f, dry_run=args.dry_run)
            counts[result] += 1
            if result == "reflowed":
                rel = f.relative_to(kdir)
                print(f"  {'(dry) ' if args.dry_run else ''}reflowed: {rel}")
        except Exception as e:
            counts["error"] += 1
            print(f"  ! {f}: {e}", file=sys.stderr)

    print(
        f"\n{'(dry-run) ' if args.dry_run else ''}"
        f"{counts['reflowed']} reflowed, "
        f"{counts['unchanged']} unchanged, "
        f"{counts['skipped']} skipped (non-transcript), "
        f"{counts['error']} errors"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
