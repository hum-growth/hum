#!/usr/bin/env python3
"""
X (Twitter) connector — API-only.

Uses X API v2 for posting. Browser-based actions (when API is unavailable)
should be handled by the agent via the browser tool.

Credentials: credentials/x.json or X_USER_ACCESS_TOKEN env var.
Format:
    {"accounts": {"account-key": {"username": "@handle", "user_access_token": "..."}}}

Usage:
  python3 -m act.connectors.x --account <account> --text "hello world"
  python3 -m act.connectors.x --account <account> --text "hello" --image /path/to/image.png
  python3 -m act.connectors.x --account <account> --segments '["tweet 1", "tweet 2"]'
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CREDENTIALS_DIR = Path(os.environ.get("CREDENTIALS_DIR", Path.home() / ".hum" / "credentials"))
X_CREDS_PATH = CREDENTIALS_DIR / "x.json"

PLATFORM = "x"


class XConnectorError(RuntimeError):
    pass


# Backward compatibility alias
XPostError = XConnectorError
ConnectorError = XConnectorError


# ── HTTP helpers ────────────────────────────────────────────────────────────


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    body = None
    req_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8") if resp.readable() else ""
            data = json.loads(raw) if raw else {}
            return resp.status, data, dict(resp.headers.items())
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"raw": raw}
        raise ConnectorError(f"{method} {url} → {err.code}: {json.dumps(data)}") from err
    except urllib.error.URLError as err:
        raise ConnectorError(f"{method} {url} → {err.reason}") from err


# ── Credentials ─────────────────────────────────────────────────────────────


def load_credentials(account: str | None) -> dict[str, Any]:
    """Load X API credentials for the given account."""
    if not X_CREDS_PATH.exists():
        return {}
    with X_CREDS_PATH.open() as f:
        creds = json.load(f)

    root = creds
    if "accounts" in creds:
        if not account:
            raise ConnectorError("X credentials define multiple accounts. Pass --account.")
        if account not in creds["accounts"]:
            return {}
        creds = creds["accounts"][account]

    token = (
        os.environ.get("X_USER_ACCESS_TOKEN")
        or os.environ.get("TWITTER_USER_ACCESS_TOKEN")
        or creds.get("user_access_token")
    )
    if not token:
        return {}

    return {
        "user_access_token": token,
        "username": creds.get("username", root.get("username", account or "unknown")),
    }


def _api_available(account: str | None) -> bool:
    """Check if X API credentials are available."""
    try:
        creds = load_credentials(account)
        return bool(creds.get("user_access_token"))
    except ConnectorError:
        return False


def _x_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── API posting ─────────────────────────────────────────────────────────────


def _upload_media_api(token: str, image_path: Path) -> str:
    """Upload an image to X via API. Returns media_id."""
    content_type, _ = mimetypes.guess_type(image_path.name)
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ConnectorError("X API media upload supports jpg, png, or webp.")

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "media": encoded,
        "media_category": "tweet_image",
        "media_type": content_type,
        "shared": False,
    }
    _, data, _ = _http_json(
        "POST",
        "https://api.x.com/2/media/upload",
        headers=_x_headers(token),
        payload=payload,
    )
    media_id = data.get("data", {}).get("id")
    if not media_id:
        raise ConnectorError(f"X media upload did not return an id: {data}")
    return media_id


def _post_api(
    text: str,
    account: str,
    media_path: Path | None = None,
) -> dict[str, Any]:
    """Post a single tweet via X API v2."""
    creds = load_credentials(account)
    token = creds["user_access_token"]

    payload: dict[str, Any] = {"text": text}
    if media_path:
        media_id = _upload_media_api(token, media_path)
        payload["media"] = {"media_ids": [media_id]}

    _, data, _ = _http_json(
        "POST",
        "https://api.x.com/2/tweets",
        headers=_x_headers(token),
        payload=payload,
    )
    tweet_id = data.get("data", {}).get("id")
    if not tweet_id:
        raise ConnectorError(f"X post create failed: {data}")

    return {
        "method": "api",
        "platform": "x",
        "account": creds["username"],
        "tweet_id": tweet_id,
        "url": f"https://x.com/{creds['username']}/status/{tweet_id}",
    }


def _post_thread_api(
    segments: list[str],
    account: str,
    media_path: Path | None = None,
) -> dict[str, Any]:
    """Post a thread via X API v2 (reply chain)."""
    creds = load_credentials(account)
    token = creds["user_access_token"]

    for idx, seg in enumerate(segments, 1):
        if len(seg) > 280:
            raise ConnectorError(f"Segment {idx} exceeds 280 chars ({len(seg)}).")

    media_id = _upload_media_api(token, media_path) if media_path else None
    previous_id = None
    posted_ids: list[str] = []

    for idx, seg in enumerate(segments):
        payload: dict[str, Any] = {"text": seg}
        if idx == 0 and media_id:
            payload["media"] = {"media_ids": [media_id]}
        if previous_id:
            payload["reply"] = {"in_reply_to_tweet_id": previous_id}

        _, data, _ = _http_json(
            "POST",
            "https://api.x.com/2/tweets",
            headers=_x_headers(token),
            payload=payload,
        )
        tweet_id = data.get("data", {}).get("id")
        if not tweet_id:
            raise ConnectorError(f"X post create failed at segment {idx + 1}: {data}")
        previous_id = tweet_id
        posted_ids.append(tweet_id)

    first_id = posted_ids[0]
    return {
        "method": "api",
        "platform": "x",
        "account": creds["username"],
        "posted_ids": posted_ids,
        "url": f"https://x.com/{creds['username']}/status/{first_id}",
    }


# ── Public API ──────────────────────────────────────────────────────────────


def post(
    text: str,
    account: str,
    media_path: str | None = None,
) -> dict[str, Any]:
    """Post a single tweet via X API.

    Returns dict with: method, platform, account, url, tweet_id.
    Raises ConnectorError if API credentials are missing or the request fails.
    """
    resolved_media = Path(media_path).resolve() if media_path else None
    if not _api_available(account):
        raise ConnectorError(
            "X API credentials not available. "
            "Add credentials to credentials/x.json or set X_USER_ACCESS_TOKEN."
        )
    return _post_api(text, account, resolved_media)


def post_thread(
    segments: list[str],
    account: str,
    media_path: str | None = None,
) -> dict[str, Any]:
    """Post a thread via X API (reply chain).

    Returns dict with: method, platform, account, url, posted_ids.
    Raises ConnectorError if API credentials are missing or the request fails.
    """
    resolved_media = Path(media_path).resolve() if media_path else None
    if not _api_available(account):
        raise ConnectorError(
            "X API credentials not available. "
            "Add credentials to credentials/x.json or set X_USER_ACCESS_TOKEN."
        )
    return _post_thread_api(segments, account, resolved_media)


# ── Stubs (not yet implemented) ─────────────────────────────────────────────


def comment(
    post_url: str,
    text: str,
    account: str,
) -> dict[str, Any]:
    """Reply to an existing X post. Not yet implemented."""
    raise NotImplementedError("X comment not yet implemented")


def follow(
    handle: str,
    account: str,
) -> dict[str, Any]:
    """Follow an X account. Not yet implemented."""
    raise NotImplementedError("X follow not yet implemented")


def get_stats(
    account: str,
    post_url: str | None = None,
) -> dict[str, Any]:
    """Get engagement stats for an X account or post. Not yet implemented."""
    raise NotImplementedError("X stats not yet implemented")


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post to X via API")
    parser.add_argument("--account", required=True, help="Account key from credentials")
    parser.add_argument("--text", help="Tweet text (single post)")
    parser.add_argument("--segments", help="JSON array of thread segments")
    parser.add_argument("--image", default=None, help="Image path to attach")
    args = parser.parse_args()

    try:
        if args.segments:
            segs = json.loads(args.segments)
            result = post_thread(segs, args.account, args.image)
        elif args.text:
            result = post(args.text, args.account, args.image)
        else:
            parser.error("Provide --text or --segments")
        print(json.dumps(result, indent=2))
    except ConnectorError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
