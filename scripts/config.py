#!/usr/bin/env python3
"""
Shared config loader for hum scripts.

Resolution order for data_dir:
  1. HUM_DATA_DIR env var
  2. openclaw.json → skills.entries.hum.config.data_dir (if running inside OpenClaw)
  3. ~/Documents/hum (default)
"""
import json
import os
import re
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "Documents" / "hum"


def _find_openclaw_json() -> Path | None:
    """Look for openclaw.json in parent directories (works inside OpenClaw)."""
    candidate = Path(__file__).resolve().parent.parent
    for _ in range(6):
        candidate = candidate.parent
        oc = candidate / "openclaw.json"
        if oc.exists():
            return oc
    return None


def load_config() -> dict:
    """Load hum config with env var → openclaw.json → default fallback."""
    # 1. Env var takes priority
    env_dir = os.environ.get("HUM_DATA_DIR")
    if env_dir:
        data_dir = Path(os.path.expanduser(env_dir))
    else:
        # 2. Try openclaw.json
        data_dir = DEFAULT_DATA_DIR
        oc_path = _find_openclaw_json()
        if oc_path:
            try:
                with open(oc_path) as f:
                    oc = json.load(f)
                raw = oc.get("skills", {}).get("entries", {}).get("hum", {}).get("config", {}).get("data_dir")
                if raw:
                    data_dir = Path(os.path.expanduser(raw))
            except (json.JSONDecodeError, KeyError):
                pass

    return {
        "data_dir": data_dir,
        "feed_dir": data_dir / "feed",
        "feeds_file": data_dir / "feed" / "feeds.json",
        "feed_raw": data_dir / "feed" / "raw",
        "feed_assets": data_dir / "feed" / "assets",
        "sources_file": data_dir / "feed" / "sources.json",
        "knowledge_dir": data_dir / "knowledge",
        "content_samples_dir": data_dir / "content-samples",
        "ideas_dir": data_dir / "ideas",
        "content_dir": data_dir / "content",
    }


def load_topics(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Parse CONTENT.md into {pillar_name: [keywords]}.

    Falls back to feed/assets/topics.json if CONTENT.md has no pillars.
    """
    if data_dir is None:
        data_dir = load_config()["data_dir"]

    content_md = data_dir / "CONTENT.md"
    topics: dict[str, list[str]] = {}

    if content_md.exists():
        current_pillar = None
        with content_md.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                # H2 heading = pillar name (skip template placeholders)
                h2 = re.match(r"^##\s+(.+)$", line)
                if h2:
                    name = h2.group(1).strip()
                    if name.startswith("[") or name.lower() == "example pillar":
                        current_pillar = None
                        continue
                    current_pillar = name
                    topics[current_pillar] = []
                    continue
                # Keywords line under a pillar
                kw_match = re.match(r"^Keywords:\s*(.+)$", line, re.IGNORECASE)
                if kw_match and current_pillar:
                    raw = kw_match.group(1).strip()
                    keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
                    topics[current_pillar] = keywords

    # Fallback: load from cached topics.json
    if not topics:
        topics_json = data_dir / "feed" / "assets" / "topics.json"
        if topics_json.exists():
            with topics_json.open() as f:
                topics = json.load(f)

    return topics


if __name__ == "__main__":
    cfg = load_config()
    for k, v in cfg.items():
        exists = "✓" if Path(v).exists() else "✗"
        print(f"  {exists} {k}: {v}")

    topics = load_topics(cfg["data_dir"])
    print(f"\n  Topics ({len(topics)} pillars):")
    for name, kws in topics.items():
        print(f"    {name}: {', '.join(kws[:5])}{'...' if len(kws) > 5 else ''}")
