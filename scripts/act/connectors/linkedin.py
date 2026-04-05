#!/usr/bin/env python3
"""
LinkedIn connector — API-only.

Uses LinkedIn REST API for posting. Browser-based actions (when API is
unavailable or for article publishing) should be handled by the agent
via the browser tool.

Credentials: credentials/linkedin.json or env vars.
Format:
    {"accounts": {"account-key": {"author_urn": "urn:li:person:...", "access_token": "..."}}}

Usage:
  python3 -m act.connectors.linkedin --account <account> --text "hello world"
  python3 -m act.connectors.linkedin --account <account> --text "hello" --image /path/to/image.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CREDENTIALS_DIR = Path(os.environ.get("CREDENTIALS_DIR", Path.home() / ".hum" / "credentials"))
LINKEDIN_CREDS_PATH = CREDENTIALS_DIR / "linkedin.json"

PLATFORM = "linkedin"


class LinkedInConnectorError(RuntimeError):
    pass


# Backward compatibility alias
LinkedInPostError = LinkedInConnectorError
ConnectorError = LinkedInConnectorError


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


def _http_bytes(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        raise ConnectorError(f"{method} {url} → {err.code}: {raw}") from err
    except urllib.error.URLError as err:
        raise ConnectorError(f"{method} {url} → {err.reason}") from err


# ── Credentials ─────────────────────────────────────────────────────────────


def load_credentials(account: str | None) -> dict[str, Any]:
    """Load LinkedIn API credentials for the given account."""
    if not LINKEDIN_CREDS_PATH.exists():
        return {}
    with LINKEDIN_CREDS_PATH.open() as f:
        creds = json.load(f)

    if "accounts" in creds:
        if not account:
            raise ConnectorError("LinkedIn credentials define multiple accounts. Pass --account.")
        if account not in creds["accounts"]:
            return {}
        creds = creds["accounts"][account]

    token = os.environ.get("LINKEDIN_ACCESS_TOKEN", creds.get("access_token"))
    author_urn = os.environ.get("LINKEDIN_AUTHOR_URN", creds.get("author_urn"))
    if not token or not author_urn:
        return {}

    return {
        "access_token": token,
        "author_urn": author_urn,
        "profile_url": creds.get("profile_url", ""),
    }


def _api_available(account: str | None) -> bool:
    try:
        creds = load_credentials(account)
        return bool(creds.get("access_token") and creds.get("author_urn"))
    except ConnectorError:
        return False


def _linkedin_headers(token: str) -> dict[str, str]:
    version = datetime.now(timezone.utc).strftime("%Y%m")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "Linkedin-Version": version,
    }


# ── API posting ─────────────────────────────────────────────────────────────


def _upload_image_api(token: str, author_urn: str, image_path: Path) -> str:
    """Upload an image to LinkedIn. Returns image URN."""
    init_payload = {"initializeUploadRequest": {"owner": author_urn}}
    _, data, _ = _http_json(
        "POST",
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=_linkedin_headers(token),
        payload=init_payload,
    )
    value = data.get("value", {})
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise ConnectorError(f"LinkedIn image initializeUpload failed: {data}")

    _http_bytes(
        "PUT",
        upload_url,
        headers={"Authorization": f"Bearer {token}"},
        body=image_path.read_bytes(),
    )
    return image_urn


def _post_api(
    text: str,
    account: str,
    image_path: Path | None = None,
) -> dict[str, Any]:
    """Post to LinkedIn feed via REST API."""
    creds = load_credentials(account)
    if not creds.get("access_token"):
        raise ConnectorError("LinkedIn API credentials not available.")

    token = creds["access_token"]
    author_urn = creds["author_urn"]

    payload: dict[str, Any] = {
        "author": author_urn,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if image_path:
        image_urn = _upload_image_api(token, author_urn, image_path)
        payload["content"] = {
            "media": {"id": image_urn, "altText": "Post image"},
        }

    _, _, headers = _http_json(
        "POST",
        "https://api.linkedin.com/rest/posts",
        headers=_linkedin_headers(token),
        payload=payload,
    )
    post_urn = headers.get("x-restli-id") or headers.get("X-RestLi-Id")
    if not post_urn:
        raise ConnectorError("LinkedIn post succeeded but did not return x-restli-id.")

    encoded_urn = urllib.parse.quote(post_urn, safe="")
    url = f"https://www.linkedin.com/feed/update/{encoded_urn}/"

    return {
        "method": "api",
        "platform": "linkedin",
        "account": account,
        "author_urn": author_urn,
        "post_urn": post_urn,
        "url": url,
    }


# ── Public API ──────────────────────────────────────────────────────────────


def post(
    text: str,
    account: str,
    image_path: str | None = None,
) -> dict[str, Any]:
    """Post to LinkedIn via API.

    Returns dict with: method, platform, account, url, author_urn, post_urn.
    Raises ConnectorError if API credentials are missing or the request fails.
    """
    resolved_image = Path(image_path).resolve() if image_path else None
    if not _api_available(account):
        raise ConnectorError(
            "LinkedIn API credentials not available. "
            "Add credentials to credentials/linkedin.json or set LINKEDIN_ACCESS_TOKEN + LINKEDIN_AUTHOR_URN."
        )
    return _post_api(text, account, resolved_image)


# ── Stubs (not yet implemented) ─────────────────────────────────────────────


def post_thread(
    segments: list[str],
    account: str,
    media_path: str | None = None,
) -> dict[str, Any]:
    """Post a thread/series on LinkedIn. Not yet implemented."""
    raise NotImplementedError("LinkedIn post_thread not yet implemented")


def comment(
    post_url: str,
    text: str,
    account: str,
) -> dict[str, Any]:
    """Reply to an existing LinkedIn post. Not yet implemented."""
    raise NotImplementedError("LinkedIn comment not yet implemented")


def follow(
    handle: str,
    account: str,
) -> dict[str, Any]:
    """Follow a LinkedIn account. Not yet implemented."""
    raise NotImplementedError("LinkedIn follow not yet implemented")


def get_stats(
    account: str,
    post_url: str | None = None,
) -> dict[str, Any]:
    """Get engagement stats for a LinkedIn account or post. Not yet implemented."""
    raise NotImplementedError("LinkedIn stats not yet implemented")


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post to LinkedIn via API")
    parser.add_argument("--account", required=True, help="Account key from credentials")
    parser.add_argument("--text", required=True, help="Post text")
    parser.add_argument("--image", default=None, help="Image path to attach")
    args = parser.parse_args()

    try:
        result = post(args.text, args.account, args.image)
        print(json.dumps(result, indent=2))
    except ConnectorError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
