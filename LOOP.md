# Hum Daily Loop

`/hum loop` runs the **daily digest only** — fetches all feed sources, ranks them, formats a digest, and sends it via Telegram. Engage, brainstorm, and learn are NOT part of this loop; run them as separate commands when you want them.

The cron job `Hum Digest Loop` (5:45am SGT, daily) calls `loop.py --step digest --supervised` directly — same behaviour as `/hum loop`.

Prerequisites: run `bash setup.sh` once, then `source venv/bin/activate` before running anything below (or substitute your own Python path). All examples assume `python3` resolves to the venv and the cwd is the skill folder.

## Digest

Runs the full feed pipeline: fetch → rank → format → send.

All sources fetch directly via API — no browser automation:
- **X home feed**: Bird API (`filter:follows since:<last_crawled>`)
- **X profiles**: Bird API (`from:<handle> since:<last_crawled>`)
- **Hacker News**: Algolia public API
- **YouTube**: yt-dlp (for `sources.json` YouTube channels)

All items merge into `feeds.json`, then rank and format a digest sent via Telegram.

Knowledge sources (RSS, sitemaps, YouTube transcripts, podcasts from `knowledge/index.md`) are also crawled as part of this step via `refresh.py --type knowledge`.

```bash
python3 scripts/loop.py --step digest
```

For cron / unattended invocation, use the supervised entry point — it adds a per-step lock, hard wall-clock timeout, output verification, and emits a single `HUM_RESULT` summary line:

```bash
python3 scripts/loop.py --step digest --supervised
```

Or run the full pipeline manually (this one *does* include knowledge via `refresh.py --type all`):
```bash
python3 scripts/feed/refresh.py --type all
python3 scripts/feed/ranker.py --input <feeds_file> --output <ranked_file>
python3 scripts/feed/digest.py --input <feeds_file> --youtube-input <youtube_file> --max-posts 12
```

Requires `AUTH_TOKEN` and `CT0` session cookies in `~/.hum/credentials/x.json` (or `HUM_X_AUTH_TOKEN` / `HUM_X_CT0` env vars) for X sources. If missing, X fetch is skipped and the rest still runs.

## Manual-only commands (not part of `/hum loop`)

These used to run as part of the loop and now run only when explicitly invoked:

```bash
python3 scripts/loop.py --step engage      # follow candidates + reply suggestions
python3 scripts/loop.py --step brainstorm  # ranked content ideas from feed + knowledge
python3 scripts/loop.py --step learn       # weekly strategy refresh (was Sundays only)
```

Each writes its output to `<data_dir>/loop/<YYYY-MM-DD>/<step>.md` and, if a `*_target` is configured, sends to Telegram automatically.

## Supervisor flags

`--supervised` makes `loop.py` fork itself: the parent enforces a wall-clock timeout, holds a per-step lock at `/tmp/hum-loop-<step>.lock.d`, verifies expected output files, and prints exactly one `HUM_RESULT` line on stdout. Optional flags:

```bash
--hard-timeout <seconds>   # default 1100 (must be < cron's timeoutSeconds)
--kill-grace <seconds>     # default 60 (SIGTERM → wait → SIGKILL)
```

`HUM_RESULT` line format:
```
HUM_RESULT step=<step> exit=<n> file=<ok|missing> duration_s=<n> [missing=<csv>]
```

Exit codes: `0` ok / `1` step failed / `2` step ok but expected output missing / `11` lock busy / `124` wall-clock timeout.
