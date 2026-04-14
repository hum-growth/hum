#!/usr/bin/env python3
"""
X (Twitter) connector — cookie GraphQL with browser fallback.

Posts via the web client's internal CreateTweet mutation using auth_token +
ct0 cookies. The official X API v2 path was removed — our account tier
never had write access. When cookie auth fails (ct0 rotation, endpoint
drift, rate limit), post()/post_thread() return ``needs_browser: True``
with compose instructions for the calling agent to drive via CDP.

ct0 rotates every few hours — refresh from browser (F12 → Application →
Cookies → x.com) when posts start returning 403.

Credentials: credentials/x.json.
Formats:
    {"auth_token": "...", "ct0": "..."}                         # single account
    {"accounts": {"<key>": {"auth_token": "...", "ct0": "..."}}} # multi-account

Usage:
  python3 -m act.connectors.x --account <account> --text "hello world"
  python3 -m act.connectors.x --account <account> --segments '["tweet 1", "tweet 2"]'
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .http import http_request

CREDENTIALS_DIR = Path(os.environ.get("CREDENTIALS_DIR", Path.home() / ".hum" / "credentials"))
X_CREDS_PATH = CREDENTIALS_DIR / "x.json"

PLATFORM = "x"

# Internal CreateTweet GraphQL — sourced from fa0311/TwitterInternalAPIDocument
# and trevorhobenshield/twitter-api-client. QueryId and features drift when X
# ships client updates; refresh from those repos if posts start returning 400.
_X_GQL_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_X_CREATE_TWEET_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
_X_CREATE_TWEET_URL = f"https://x.com/i/api/graphql/{_X_CREATE_TWEET_QUERY_ID}/CreateTweet"
_X_CREATE_TWEET_FEATURES: dict[str, bool] = {
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "articles_preview_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


class XConnectorError(RuntimeError):
    pass


# Backward compatibility alias
XPostError = XConnectorError
ConnectorError = XConnectorError


# ── Credentials ─────────────────────────────────────────────────────────────


def load_credentials(account: str | None) -> dict[str, Any]:
    """Load X cookie credentials for the given account.

    Returns a dict with ``auth_token``, ``ct0``, and ``username`` when
    available, or ``{}`` when the file is missing or no usable cookies
    are found.
    """
    if not X_CREDS_PATH.exists():
        return {}
    cred_path = X_CREDS_PATH
    mode = cred_path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"Warning: credential file {cred_path} is readable by group/others. Run: chmod 600 {cred_path}", file=sys.stderr)
    with X_CREDS_PATH.open() as f:
        creds = json.load(f)

    root = creds
    if "accounts" in creds:
        if not account:
            raise ConnectorError("X credentials define multiple accounts. Pass --account.")
        if account not in creds["accounts"]:
            return {}
        creds = creds["accounts"][account]

    auth_token = creds.get("auth_token")
    ct0 = creds.get("ct0")
    if not (auth_token and ct0):
        return {}

    return {
        "username": creds.get("username", root.get("username", account or "unknown")),
        "auth_token": auth_token,
        "ct0": ct0,
    }


def _cookie_available(account: str | None) -> bool:
    """Check if cookie-based GraphQL credentials are available."""
    try:
        creds = load_credentials(account)
        return bool(creds.get("auth_token") and creds.get("ct0"))
    except ConnectorError:
        return False


# ── Cookie GraphQL posting ──────────────────────────────────────────────────


def _cookie_headers(ct0: str, auth_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_X_GQL_BEARER}",
        "Content-Type": "application/json",
        "Cookie": f"auth_token={auth_token}; ct0={ct0}",
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
    }


def _create_tweet_variables(text: str, reply_to: str | None) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "tweet_text": text,
        "dark_request": False,
        "media": {"media_entities": [], "possibly_sensitive": False},
        "semantic_annotation_ids": [],
        "disallowed_reply_hashtags": [],
    }
    if reply_to:
        variables["reply"] = {
            "in_reply_to_tweet_id": reply_to,
            "exclude_reply_user_ids": [],
        }
        variables["batch_compose"] = "BatchSubsequent"
    return variables


def _create_tweet_cookie(
    text: str,
    ct0: str,
    auth_token: str,
    reply_to: str | None = None,
) -> dict[str, Any]:
    """POST to CreateTweet GraphQL and return the parsed tweet result."""
    payload = {
        "variables": _create_tweet_variables(text, reply_to),
        "features": _X_CREATE_TWEET_FEATURES,
        "queryId": _X_CREATE_TWEET_QUERY_ID,
    }
    _, data, _ = http_request(
        "POST",
        _X_CREATE_TWEET_URL,
        headers=_cookie_headers(ct0, auth_token),
        payload=payload,
        exc_factory=ConnectorError,
    )
    if not isinstance(data, dict):
        raise ConnectorError("CreateTweet returned non-JSON response")
    errors = data.get("errors")
    if errors:
        raise ConnectorError(f"CreateTweet error: {errors}")
    result = (
        data.get("data", {})
        .get("create_tweet", {})
        .get("tweet_results", {})
        .get("result", {})
    )
    if not result.get("rest_id"):
        raise ConnectorError(f"CreateTweet missing rest_id: {data}")
    return result


def _extract_screen_name(tweet_result: dict[str, Any], fallback: str) -> str:
    """Pull screen_name from a CreateTweet result, with a safe fallback.

    X has moved this field between ``core`` and ``legacy`` on the user object
    in different client versions, so try both.
    """
    try:
        user_result = tweet_result["core"]["user_results"]["result"]
    except (KeyError, TypeError):
        return fallback
    for sub in ("core", "legacy"):
        name = (user_result.get(sub) or {}).get("screen_name")
        if name:
            return name
    return fallback


def _post_cookie(text: str, account: str) -> dict[str, Any]:
    creds = load_credentials(account)
    result = _create_tweet_cookie(text, creds["ct0"], creds["auth_token"])
    tweet_id = result["rest_id"]
    screen_name = _extract_screen_name(result, creds["username"].lstrip("@"))
    return {
        "method": "cookie",
        "platform": "x",
        "account": screen_name,
        "tweet_id": tweet_id,
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
    }


def _post_thread_cookie(segments: list[str], account: str) -> dict[str, Any]:
    creds = load_credentials(account)
    for idx, seg in enumerate(segments, 1):
        if len(seg) > 280:
            raise ConnectorError(f"Segment {idx} exceeds 280 chars ({len(seg)}).")

    previous_id: str | None = None
    posted_ids: list[str] = []
    screen_name = creds["username"].lstrip("@")
    for seg in segments:
        result = _create_tweet_cookie(
            seg, creds["ct0"], creds["auth_token"], reply_to=previous_id
        )
        previous_id = result["rest_id"]
        posted_ids.append(previous_id)
        screen_name = _extract_screen_name(result, screen_name)

    first_id = posted_ids[0]
    return {
        "method": "cookie",
        "platform": "x",
        "account": screen_name,
        "posted_ids": posted_ids,
        "url": f"https://x.com/{screen_name}/status/{first_id}",
    }


# ── Browser fallback ────────────────────────────────────────────────────────


def _browser_post_fallback(
    segments: list[str],
    account: str,
    media_path: Path | None,
    reason: str,
) -> dict[str, Any]:
    """Return a ``needs_browser: True`` dict with compose instructions.

    The calling agent (publish.py invoker) sees this payload and drives the
    compose flow via its browser tool / CDP relay. ``reason`` is included so
    the caller can surface why cookie auth was skipped or failed.
    """
    is_thread = len(segments) > 1
    action = "post_x_thread" if is_thread else "post_x_tweet"
    steps: list[str] = [
        "Navigate to https://x.com/home",
        "Wait for the home timeline to render (2-3 seconds)",
        "Click the main compose box (\"What's happening?\")",
        "Type the first segment verbatim — do not edit, shorten, or reformat",
    ]
    if media_path:
        steps.append(f"Attach the image at {media_path} via the media button")
    if is_thread:
        steps.extend([
            "Click the '+' (Add post) button below the compose box",
            "Type the next segment verbatim in the new entry",
            "Repeat '+' and type for each remaining segment",
        ])
    steps.extend([
        "Click the 'Post' (or 'Post all') button to publish",
        "Wait for the compose dialog to dismiss",
        "Capture the URL of the newly published post (click it from the timeline if needed)",
    ])
    return {
        "needs_browser": True,
        "platform": "x",
        "action": action,
        "account": account,
        "segments": segments,
        "image": str(media_path) if media_path else None,
        "reason": reason,
        "compose_url": "https://x.com/home",
        "instructions": {
            "action": action,
            "url": "https://x.com/home",
            "steps": steps,
            "output_schema": {
                "posted_ids": ["string (tweet id per segment)"],
                "url": "string (URL of the first posted tweet)",
            },
        },
    }


# ── Public API ──────────────────────────────────────────────────────────────


def post(
    text: str,
    account: str,
    media_path: str | None = None,
) -> dict[str, Any]:
    """Post a single tweet via cookie GraphQL, with browser fallback.

    Returns either ``{method, platform, account, url, tweet_id}`` on success
    or ``{needs_browser: True, ...}`` when cookies are missing/stale or the
    request fails — the calling agent handles the browser path.
    """
    resolved_media = Path(media_path).resolve() if media_path else None
    segments = [text]

    if resolved_media:
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason="Media upload not supported via cookie GraphQL",
        )
    if not _cookie_available(account):
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason="X cookie credentials (auth_token + ct0) not found in credentials/x.json",
        )
    try:
        return _post_cookie(text, account)
    except ConnectorError as err:
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason=f"Cookie post failed: {err}",
        )


def post_thread(
    segments: list[str],
    account: str,
    media_path: str | None = None,
) -> dict[str, Any]:
    """Post a thread via cookie GraphQL, with browser fallback.

    Returns either ``{method, platform, account, url, posted_ids}`` on success
    or ``{needs_browser: True, ...}`` when cookies are missing/stale or the
    request fails — the calling agent handles the browser path.
    """
    resolved_media = Path(media_path).resolve() if media_path else None

    if resolved_media:
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason="Media upload not supported via cookie GraphQL",
        )
    if not _cookie_available(account):
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason="X cookie credentials (auth_token + ct0) not found in credentials/x.json",
        )
    try:
        return _post_thread_cookie(segments, account)
    except ConnectorError as err:
        return _browser_post_fallback(
            segments, account, resolved_media,
            reason=f"Cookie thread post failed: {err}",
        )


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
    """Follow an X account via Bird API (CreateFriendship GraphQL)."""
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(_root))
    from lib import bird_x as _bird

    creds = load_credentials(account)
    if not creds:
        return {"handle": handle, "status": "error", "message": "credentials not found"}
    _bird.set_credentials(creds["auth_token"], creds["ct0"])

    results = _bird.follow_accounts([handle])
    if not results:
        return {"handle": handle, "status": "error", "message": "no result from bird"}
    r = results[0]
    return {
        "handle": handle,
        "status": "followed" if r.get("success") else "error",
        "message": r.get("error", ""),
    }


def get_stats(
    account: str,
    post_url: str | None = None,
) -> dict[str, Any]:
    """Get engagement stats for an X account or post.

    Always returns ``needs_browser: True`` — stats come from profile scraping
    via the calling agent's browser session.
    """
    creds = load_credentials(account)
    username = creds.get("username", account) if creds else account
    return {
        "needs_browser": True,
        "platform": "x",
        "account": username,
        "profile_url": f"https://x.com/{username.lstrip('@')}",
    }


def _browser_stats(username: str) -> dict[str, Any]:
    """Scrape X profile stats via HTTP (no browser needed).

    X embeds profile data as JSON in the page HTML. We fetch the page,
    extract the __INITIAL_STATE__ JSON, and parse out the key metrics.
    """
    import re

    url = f"https://x.com/{username.lstrip('@')}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return {
            "method": "browser",
            "platform": "x",
            "account": username,
            "error": f"Failed to fetch profile: {exc}",
        }

    # Extract __INITIAL_STATE__ JSON from HTML
    match = re.search(
        r'id="__INITIAL_STATE__"\s*>\s*({.*?})\s*</script>',
        html,
        re.DOTALL,
    )
    if not match:
        # Fallback: try to extract individual stats via regex
        return _extract_stats_from_html(html, username)

    try:
        import json as _json

        state = _json.loads(match.group(1))
    except Exception:
        return _extract_stats_from_html(html, username)

    # Navigate to user entity in state
    users = state.get("users", {})
    user = users.get(username.lstrip("@"), {})
    if not user:
        # Try finding by screen_name
        for k, v in users.items():
            if v.get("screen_name", "").lower() == username.lstrip("@").lower():
                user = v
                break

    follower_count = user.get("follower_count", 0) or 0
    following_count = user.get("following_count", 0) or 0
    statuses_count = user.get("statuses_count", 0) or 0

    # Extract latest tweets from timeline
    tweets = []
    timeline = (
        state.get("featureSwitchTimeline", {})
        .get("timeline", {})
        .get("instructions", [{}])
    )
    entries = []
    for instr in timeline:
        for entry in instr.get("addEntries", {}).get("entries", []):
            entries.append(entry)

    for entry in entries[:10]:
        tweet_data = entry.get("content", {}).get("tweet", {})
        if not tweet_data:
            # Variant format
            tweet_data = entry.get("content", {})
        tweet_id = tweet_data.get("id_str", "")
        full_text = tweet_data.get("full_text", "") or tweet_data.get("text", "")
        created_at = tweet_data.get("created_at", "")
        retweet_count = tweet_data.get("retweet_count", 0) or 0
        favorite_count = tweet_data.get("favorite_count", 0) or 0
        reply_count = tweet_data.get("reply_count", 0) or 0
        view_count = tweet_data.get("views", {}).get("count", 0) or 0

        if full_text:
            tweets.append({
                "id": tweet_id,
                "text": full_text[:200],
                "created_at": created_at,
                "retweets": retweet_count,
                "likes": favorite_count,
                "replies": reply_count,
                "views": view_count,
                "url": f"https://x.com/{username.lstrip('@')}/status/{tweet_id}",
            })

    return {
        "method": "browser",
        "platform": "x",
        "account": username,
        "url": url,
        "profile": {
            "followers": follower_count,
            "following": following_count,
            "posts": statuses_count,
        },
        "recent_posts": tweets,
    }


def _extract_stats_from_html(html: str, username: str) -> dict[str, Any]:
    """Fallback: extract stats from HTML when JSON state is unavailable."""
    import re

    followers = 0
    following = 0
    posts = 0

    fmatch = re.search(r'"follower_count"\s*:\s*(\d+)', html)
    if fmatch:
        followers = int(fmatch.group(1))

    fimatch = re.search(r'"following_count"\s*:\s*(\d+)', html)
    if fimatch:
        following = int(fimatch.group(1))

    pmatch = re.search(r'"statuses_count"\s*:\s*(\d+)', html)
    if pmatch:
        posts = int(pmatch.group(1))

    # Try alternate HTML patterns
    if not followers:
        m = re.search(r'([\d,]+)\s+Followers', html)
        if m:
            followers = int(m.group(1).replace(",", ""))

    return {
        "method": "browser",
        "platform": "x",
        "account": username,
        "profile": {
            "followers": followers,
            "following": following,
            "posts": posts,
        },
        "recent_posts": [],
        "note": "Limited data extracted from HTML",
    }


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
