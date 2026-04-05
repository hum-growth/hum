"""YouTube transcript extraction via yt-dlp.

Only includes transcript fetching — search lives in feed/source/youtube.py.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

TRANSCRIPT_MAX_WORDS = 500


def _log(msg: str):
    sys.stderr.write(f"[YouTube] {msg}\n")
    sys.stderr.flush()


def _clean_vtt(vtt_text: str) -> str:
    """Convert VTT subtitle format to clean plaintext."""
    text = re.sub(r'^WEBVTT.*?\n\n', '', vtt_text, flags=re.DOTALL)
    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*\n', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    lines = text.strip().split('\n')
    seen = set()
    unique = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique.append(stripped)
    return re.sub(r'\s+', ' ', ' '.join(unique)).strip()


def fetch_transcript(video_id: str, temp_dir: str) -> Optional[str]:
    """Fetch auto-generated transcript for a YouTube video."""
    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "--skip-download",
        "--no-warnings",
        "-o", f"{temp_dir}/%(id)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    preexec = os.setsid if hasattr(os, 'setsid') else None

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, preexec_fn=preexec,
        )
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            proc.wait(timeout=5)
            return None
    except FileNotFoundError:
        return None

    vtt_path = Path(temp_dir) / f"{video_id}.en.vtt"
    if not vtt_path.exists():
        for p in Path(temp_dir).glob(f"{video_id}*.vtt"):
            vtt_path = p
            break
        else:
            return None

    try:
        raw = vtt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    transcript = _clean_vtt(raw)

    words = transcript.split()
    if len(words) > TRANSCRIPT_MAX_WORDS:
        transcript = ' '.join(words[:TRANSCRIPT_MAX_WORDS]) + '...'

    return transcript if transcript else None


def fetch_transcripts_parallel(
    video_ids: List[str],
    max_workers: int = 5,
) -> Dict[str, Optional[str]]:
    """Fetch transcripts for multiple videos in parallel."""
    if not video_ids:
        return {}

    _log(f"Fetching transcripts for {len(video_ids)} videos")

    results = {}
    with tempfile.TemporaryDirectory() as temp_dir:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(fetch_transcript, vid, temp_dir): vid
                for vid in video_ids
            }
            for future in as_completed(futures):
                vid = futures[future]
                try:
                    results[vid] = future.result()
                except Exception:
                    results[vid] = None

    got = sum(1 for v in results.values() if v)
    _log(f"Got transcripts for {got}/{len(video_ids)} videos")
    return results
