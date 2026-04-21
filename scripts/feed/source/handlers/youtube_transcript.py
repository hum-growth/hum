"""YouTube channel handler -- channel videos.xml + youtube-transcript-api.

Crawls full transcripts into the knowledge base (distinct from feed/source/youtube.py
which produces short feed digest items via yt-dlp).

Video list resolution order:
  1. YouTube RSS feed (videos.xml) — fast, no extra deps
  2. yt-dlp flat-playlist — fallback when RSS is blocked (common on server IPs)
"""

import json
import subprocess
import time
import feedparser

from .common import (
    DELAY,
    source_dir,
    existing_urls,
    make_filename,
    build_frontmatter,
)

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _YT_API = YouTubeTranscriptApi()
except Exception as e:
    _YT_API = None
    print(f"! youtube-transcript-api unavailable: {e}")


def _channel_feed_url(ref: str) -> str:
    """Accept a bare channel ID (UC...) or a full videos.xml URL."""
    if ref.startswith("http"):
        return ref
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={ref}"


def _fetch_video_list_via_ytdlp(channel_ref: str, max_videos: int = 50) -> list[dict]:
    """Fetch video list via yt-dlp flat-playlist as fallback when RSS is blocked.

    Returns list of dicts with keys: id, title, upload_date, url.
    """
    if channel_ref.startswith("http"):
        channel_url = channel_ref
    elif channel_ref.startswith("UC"):
        channel_url = f"https://www.youtube.com/channel/{channel_ref}/videos"
    else:
        channel_url = channel_ref

    try:
        result = subprocess.run(
            [
                "yt-dlp", "--flat-playlist", "-J",
                "--playlist-items", f"1:{max_videos}",
                channel_url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        entries = []
        for e in data.get("entries") or []:
            vid_id = e.get("id", "")
            if not vid_id:
                continue
            entries.append({
                "id": vid_id,
                "title": e.get("title", "Untitled"),
                "upload_date": e.get("upload_date", ""),  # YYYYMMDD
                "url": f"https://www.youtube.com/watch?v={vid_id}",
            })
        return entries
    except Exception as e:
        print(f"     ! yt-dlp fallback failed: {e}")
        return []


def _fetch_transcript(video_id: str) -> str:
    """Return plain-text transcript or empty string on failure."""
    if _YT_API is None:
        return ""
    try:
        t = _YT_API.fetch(video_id)
        raw = t.to_raw_data()
        return "\n".join(seg["text"].strip() for seg in raw if seg.get("text"))
    except Exception as e:
        print(f"     ! transcript unavailable: {e}")
        return ""


def crawl(source: dict, max_articles: int = 0, recrawl: bool = False) -> int:
    key = source["key"]
    name = source["name"]
    author = source["author"]
    feed_url = _channel_feed_url(source["url"])

    out_dir = source_dir(key)
    out_dir.mkdir(parents=True, exist_ok=True)

    already = set() if recrawl else existing_urls(key)
    if already:
        print(f"   {len(already)} videos already saved -- skipping those")

    # Try RSS feed first; fall back to yt-dlp if blocked
    feed = feedparser.parse(feed_url)
    rss_entries = feed.entries or []

    if rss_entries:
        print(f"   {len(rss_entries)} videos in RSS feed")
        # Normalise to a common shape
        window = max(max_articles * 3, 30) if max_articles else len(rss_entries)
        video_list = []
        for e in rss_entries[:window]:
            u = e.get("link", "")
            vid_id = e.get("yt_videoid") or (u.split("v=")[-1].split("&")[0] if "v=" in u else "")
            raw_date = e.get("published", "")[:10]
            video_list.append({"id": vid_id, "title": e.get("title", "Untitled"),
                                "upload_date": raw_date.replace("-", ""), "url": u})
    else:
        print(f"   ! RSS unavailable ({feed_url}) — trying yt-dlp")
        fetch_limit = max(max_articles * 3, 30) if max_articles else 50
        video_list = _fetch_video_list_via_ytdlp(source["url"], max_videos=fetch_limit)
        if not video_list:
            print(f"   ! yt-dlp fallback also failed — skipping")
            return 0
        print(f"   {len(video_list)} videos via yt-dlp")

    # Sort newest-first by upload_date (YYYYMMDD or YYYY-MM-DD)
    video_list.sort(key=lambda v: v.get("upload_date", ""), reverse=True)
    if max_articles:
        video_list = video_list[:max(max_articles * 3, 30)]

    saved = 0
    for entry in video_list:
        if max_articles and saved >= max_articles:
            break

        url = entry["url"]
        video_id = entry["id"]
        title = entry["title"]
        raw_date = entry.get("upload_date", "")
        if not url or url in already:
            continue

        # Normalise YYYYMMDD → YYYY-MM-DD
        if len(raw_date) == 8 and raw_date.isdigit():
            date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        elif raw_date:
            date = raw_date[:10]
        else:
            import datetime as _dt
            date = _dt.date.today().strftime("%Y-%m-%d")

        filename = make_filename(date, video_id)
        dest = out_dir / filename
        if not recrawl and dest.exists():
            continue

        print(f"   -> {title[:60]}")
        transcript = _fetch_transcript(video_id)
        description = ""
        media = entry.get("media_description") or entry.get("summary") or ""
        if media:
            description = str(media).strip()

        if transcript:
            body = f"## Transcript\n\n{transcript}\n"
        elif description:
            body = f"## Description\n\n{description}\n\n_Transcript unavailable._\n"
        else:
            print(f"     skipped (no transcript and no description)")
            time.sleep(DELAY)
            continue

        fm = build_frontmatter(
            title, date, url, key, name, author,
            extra={"video_id": video_id, "video_url": url},
        )
        dest.write_text(fm + body, encoding="utf-8")
        saved += 1
        time.sleep(DELAY)

    print(f"   done: {saved} new videos saved")
    return saved
