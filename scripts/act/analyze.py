#!/usr/bin/env python3
"""
analyze.py — Gather insights and analytics about your social accounts.

Pulls engagement stats, follower growth, top-performing posts, and
audience patterns across platforms.

Usage (CLI):
    python3 scripts/act/analyze.py --platform x --account <account>
    python3 scripts/act/analyze.py --platform linkedin --account <account>
    python3 scripts/act/analyze.py --platform all --account <account>

Usage (library):
    from act.analyze import analyze_account, analyze_post
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_ROOT))

from act.connectors import load as load_connector


def analyze_account(
    platform: str,
    account: str,
) -> dict[str, Any]:
    """Gather account-level insights for a platform.

    Returns dict with engagement metrics, follower stats, and recent post performance.
    """
    connector = load_connector(platform)
    return connector.get_stats(account)


def analyze_post(
    platform: str,
    account: str,
    post_url: str,
) -> dict[str, Any]:
    """Gather detailed analytics for a specific post.

    Returns dict with impressions, engagement rate, comments, shares.
    """
    connector = load_connector(platform)
    return connector.get_stats(account, post_url)


def analyze_all(account: str) -> dict[str, Any]:
    """Gather insights across all platforms."""
    results = {}
    for platform in ["x", "linkedin"]:
        try:
            results[platform] = analyze_account(platform, account)
        except NotImplementedError:
            results[platform] = {"status": "not_implemented"}
        except Exception as e:
            results[platform] = {"status": "error", "message": str(e)}
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Gather account insights and analytics")
    parser.add_argument("--platform", required=True, choices=["x", "linkedin", "all"])
    parser.add_argument("--account", required=True, help="Account key from credentials")
    parser.add_argument("--post-url", help="Specific post URL to analyze")
    args = parser.parse_args()

    try:
        if args.platform == "all":
            result = analyze_all(args.account)
        elif args.post_url:
            result = analyze_post(args.platform, args.account, args.post_url)
        else:
            result = analyze_account(args.platform, args.account)
        print(json.dumps(result, indent=2))
    except NotImplementedError as e:
        print(f"NOT IMPLEMENTED: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
