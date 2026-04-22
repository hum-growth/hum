#!/usr/bin/env python3
from __future__ import annotations
"""
Shared config loader for hum scripts.

Resolution order for data_dir:
  1. HUM_DATA_DIR env var
  2. openclaw.json → skills.entries.hum.config.hum_data_dir (if running inside OpenClaw)
  3. openclaw.json → skills.entries.hum.config.data_dir (legacy fallback)
  4. ~/Documents/hum (default)
"""
import json
import os
import re
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "Documents" / "hum"


def _find_openclaw_json() -> Path | None:
    """Look for openclaw.json in parent directories and ~/.openclaw/."""
    # Walk up from script location (may be symlinked)
    candidate = Path(__file__).resolve().parent.parent
    for _ in range(6):
        candidate = candidate.parent
        oc = candidate / "openclaw.json"
        if oc.exists():
            return oc
    # Fallback: check ~/.openclaw/ directly
    home_oc = Path.home() / ".openclaw" / "openclaw.json"
    if home_oc.exists():
        return home_oc
    return None


def load_config() -> dict:
    """Load hum config with env var → openclaw.json → default fallback."""
    oc_path = _find_openclaw_json()
    oc_data = {}
    if oc_path:
        try:
            with open(oc_path) as f:
                oc_data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass

    # 1. Env var takes priority for data_dir
    env_dir = os.environ.get("HUM_DATA_DIR")
    if env_dir:
        data_dir = Path(os.path.expanduser(env_dir))
    else:
        # 2. Try openclaw.json (hum_data_dir preferred, data_dir for legacy compat)
        data_dir = DEFAULT_DATA_DIR
        hum_cfg = oc_data.get("skills", {}).get("entries", {}).get("hum", {}).get("config", {})
        raw = hum_cfg.get("hum_data_dir") or hum_cfg.get("data_dir")
        if raw:
            data_dir = Path(os.path.expanduser(raw))

    # Image model: env var → openclaw.json → default
    image_model = os.environ.get("HUM_IMAGE_MODEL")
    if not image_model:
        image_model = oc_data.get("skills", {}).get("entries", {}).get("hum", {}).get("config", {}).get("hum_image_model")
    image_model = image_model or "gemini"

    # Delivery targets: env var → openclaw.json → None
    hum_cfg = oc_data.get("skills", {}).get("entries", {}).get("hum", {}).get("config", {})
    digest_target = os.environ.get("HUM_DIGEST_TARGET") or hum_cfg.get("hum_digest_target") or None
    brainstorm_target = os.environ.get("HUM_BRAINSTORM_TARGET") or hum_cfg.get("hum_brainstorm_target") or None
    engage_target = os.environ.get("HUM_ENGAGE_TARGET") or hum_cfg.get("hum_engage_target") or None

    return {
        "data_dir": data_dir,
        "image_model": image_model,
        "digest_target": digest_target,
        "brainstorm_target": brainstorm_target,
        "engage_target": engage_target,
        "feed_dir": data_dir / "feed",
        "feeds_file": data_dir / "feed" / "feeds.json",
        "feed_raw": data_dir / "feed" / "raw",
        "feed_assets": data_dir / "feed" / "assets",
        "sources_file": data_dir / "feed" / "sources.json",
        "knowledge_dir": data_dir / "knowledge",
        "content_samples_dir": data_dir / "content-samples",
        "ideas_dir": data_dir / "ideas",
        "content_dir": data_dir / "content",
        "content_drafts_dir": data_dir / "content" / "drafts",
        "content_published_dir": data_dir / "content" / "published",
        "content_images_dir": data_dir / "content" / "images",
        "loop_dir": data_dir / "loop",
    }


def load_visual_style(data_dir: Path | None = None) -> str | None:
    """Parse VOICE.md for the '## Visual Style' section.

    Returns the section content as a string, or None if absent/empty.
    """
    if data_dir is None:
        data_dir = load_config()["data_dir"]

    voice_md = data_dir / "VOICE.md"
    if not voice_md.exists():
        return None

    lines: list[str] = []
    in_section = False

    with voice_md.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\n")
            if re.match(r"^##\s+Visual Style", stripped):
                in_section = True
                continue
            if in_section:
                if re.match(r"^##\s+", stripped):
                    break
                lines.append(stripped)

    text = "\n".join(lines).strip()
    # Strip HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    return text or None


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


def load_channel_handle(platform: str, data_dir: Path | None = None) -> str | None:
    """Return the configured handle for a platform from CHANNELS.md.

    Supports two formats:
      - Header: `## X (@handle)` — handle in the section header
      - Field:  `## X` followed by `- **handle:** @handle` in the section body

    Matching is case-insensitive on the platform name. Returns the handle
    without the leading `@`, or None if not found.
    """
    if data_dir is None:
        data_dir = load_config()["data_dir"]

    channels_md = data_dir / "CHANNELS.md"
    if not channels_md.exists():
        return None

    platform_lower = platform.strip().lower()
    try:
        with channels_md.open(encoding="utf-8") as f:
            in_section = False
            for line in f:
                # Match section header — try inline handle first
                m = re.match(r"^##\s+([^\s(]+)\s*(?:\(@([^)\s]+)\))?", line)
                if m:
                    in_section = m.group(1).strip().lower() == platform_lower
                    if in_section and m.group(2):
                        return m.group(2).strip()
                    continue
                # Inside the matching section, look for `- **handle:** @handle`
                if in_section:
                    hm = re.match(r"\s*-\s+\*\*handle:\*\*\s+@?(\S+)", line)
                    if hm:
                        return hm.group(1).strip()
    except OSError:
        return None
    return None


def _parse_count(value: str) -> int:
    """Return the leading integer from a field value, e.g. '5' or '5. some text' → 5."""
    m = re.match(r"^\s*(\d+)", value)
    return int(m.group(1)) if m else 0


def load_channel_config(platform: str, data_dir: Path | None = None) -> dict:
    """Return the full parsed config for a channel from CHANNELS.md.

    Reads all ``- **key:** value`` fields under the ``## Platform`` section.
    Returns:

        handle                        str | None
        follows_per_run               int   (0 = skip)
        follow_target                 str   (free text, '' if absent)
        outbound_suggestions_per_run  int
        outbound_target               str   (free text, '' if absent)
        inbound_suggestions_per_run   int | None  (None = no cap)
        inbound_no_cap                bool
        raw                           dict[str, str]
    """
    if data_dir is None:
        data_dir = load_config()["data_dir"]

    channels_md = data_dir / "CHANNELS.md"
    if not channels_md.exists():
        return {}

    platform_lower = platform.strip().lower()
    raw_fields: dict[str, str] = {}
    in_section = False

    try:
        with channels_md.open(encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^##\s+([^\s(#]+)", line)
                if m:
                    if in_section:
                        break  # left our section
                    in_section = m.group(1).strip().lower() == platform_lower
                    continue
                if not in_section:
                    continue
                fm = re.match(r"\s*-\s+\*\*([^*]+):\*\*\s*(.*)", line)
                if fm:
                    raw_fields[fm.group(1).strip().lower()] = fm.group(2).strip()
    except OSError:
        return {}

    if not raw_fields:
        return {}

    handle = raw_fields.get("handle", "").lstrip("@") or None

    follows_count = _parse_count(raw_fields.get("follows_per_run", "0"))
    outbound_count = _parse_count(raw_fields.get("outbound_suggestions_per_run", "0"))

    inbound_raw = raw_fields.get("inbound_suggestions_per_run", "0")
    inbound_no_cap = "no cap" in inbound_raw.lower() or (
        "all" in inbound_raw.lower() and "unanswered" in inbound_raw.lower()
    )
    inbound_m = re.match(r"^\s*(\d+)", inbound_raw)
    inbound_count: int | None = (
        int(inbound_m.group(1)) if inbound_m else (None if inbound_no_cap else 0)
    )

    default_follow_target = "Find relevant accounts worth following in the user's niche, prioritizing real practitioners and excluding spam, bots, and already-followed accounts."
    default_outbound_target = "Find recent posts worth replying to where the user can add a thoughtful, specific point and build visibility with the right audience."
    default_inbound_target = "Reply thoughtfully to inbound responses that deserve engagement, add value, and strengthen the conversation."

    inbound_target = (
        raw_fields.get("inbound_target", "")
        or raw_fields.get("inbound_suggestions_per_run", "")
        or default_inbound_target
    )

    return {
        "platform": platform,
        "handle": handle,
        "follows_per_run": follows_count,
        "follow_target": raw_fields.get("follow_target", "") or default_follow_target,
        "outbound_suggestions_per_run": outbound_count,
        "outbound_target": raw_fields.get("outbound_target", "") or default_outbound_target,
        "inbound_suggestions_per_run": inbound_count,
        "inbound_no_cap": inbound_no_cap,
        "inbound_target": inbound_target,
        "raw": raw_fields,
    }


def load_x_credentials() -> dict[str, str | None]:
    """Load X/Twitter session credentials for Bird-based scraping.

    Priority order:
      1. HUM_X_AUTH_TOKEN / HUM_X_CT0 env vars
      2. ~/.hum/credentials/x.json → "auth_token" / "ct0"
      3. AUTH_TOKEN / CT0 env vars (shared with last30days)

    Returns dict with keys: auth_token, ct0 (either may be None).
    """
    auth_token = os.environ.get("HUM_X_AUTH_TOKEN")
    ct0 = os.environ.get("HUM_X_CT0")

    if not (auth_token and ct0):
        creds_file = Path.home() / ".hum" / "credentials" / "x.json"
        if creds_file.exists():
            try:
                with open(creds_file) as f:
                    data = json.load(f)
                auth_token = auth_token or data.get("auth_token")
                ct0 = ct0 or data.get("ct0")
            except (json.JSONDecodeError, OSError):
                pass

    if not (auth_token and ct0):
        auth_token = auth_token or os.environ.get("AUTH_TOKEN")
        ct0 = ct0 or os.environ.get("CT0")

    return {"auth_token": auth_token, "ct0": ct0}


if __name__ == "__main__":
    cfg = load_config()
    for k, v in cfg.items():
        if isinstance(v, Path):
            exists = "✓" if v.exists() else "✗"
            print(f"  {exists} {k}: {v}")
        else:
            val = f"{v[:4]}..." if isinstance(v, str) and len(v) > 8 else v
            print(f"    {k}: {val}")

    topics = load_topics(cfg["data_dir"])
    print(f"\n  Topics ({len(topics)} pillars):")
    for name, kws in topics.items():
        print(f"    {name}: {', '.join(kws[:5])}{'...' if len(kws) > 5 else ''}")
