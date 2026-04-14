# Hum Daily Loop

Run every morning via `python3 scripts/loop.py` or `/hum loop`. Follow each step in order. If a step fails, note it and continue.

Prerequisites: run `bash setup.sh` once, then `source venv/bin/activate` before running anything below (or substitute your own Python path). All examples assume `python3` resolves to the venv and the cwd is the skill folder.

## Step 1 — Feed Digest

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

Or run the full pipeline manually (this one *does* include knowledge via `refresh.py --type all`):
```bash
python3 scripts/feed/refresh.py --type all
python3 scripts/feed/ranker.py --input <feeds_file> --output <ranked_file>
python3 scripts/feed/digest.py --input <feeds_file> --youtube-input <youtube_file> --max-posts 12
```

Requires `AUTH_TOKEN` and `CT0` session cookies in `~/.hum/credentials/x.json` (or `HUM_X_AUTH_TOKEN` / `HUM_X_CT0` env vars) for X sources. If missing, X fetch is skipped and the rest still runs.

## Step 2 — Engage (parallel with digest)

Analyzes recent X posts in `feeds.json` and surfaces follow candidates, outbound reply suggestions, and inbound replies awaiting response.

```bash
python3 scripts/loop.py --step engage
```

Sending is handled automatically by the script. If `engage_target` is configured, a compact mobile-friendly version is sent to that target. The full output (including agent instructions) is saved to `<data_dir>/loop/<date>/engage.md`.

## Step 3 — Brainstorm

Run `scripts/create/brainstorm.py --max 8`. Present top ideas and ask:
- "Any topics to add to the pipeline?"
- "Want to work on any posts today?"

```bash
python3 scripts/loop.py --step brainstorm
```

Sending is handled automatically by the script. If `brainstorm_target` is configured, the scored items list is sent to that target. The full output (including agent instructions) is saved to `<data_dir>/loop/<date>/brainstorm.md`.

## Step 4 — Learn (Sundays only)

Run `/hum learn` as defined in COMMANDS.md. Analyze feed trends, research platform algorithms, update context files.

```bash
python3 scripts/loop.py --step learn
```

## Full loop

```bash
python3 scripts/loop.py                     # full daily loop
python3 scripts/loop.py --dry-run           # format output but don't send
python3 scripts/loop.py --max-posts 15      # override digest size
python3 scripts/loop.py --skip-youtube      # skip YouTube fetch
```
