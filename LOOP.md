# Hum Daily Loop

Run every morning. Follow each step in order. If a step fails, note it and continue.

Python: `/opt/homebrew/bin/python3`
Scripts: `skills/hum/scripts`

## Step 1 — Scrape X feed (browser)

Run `scripts/feed/refresh.py`. It prints JSON browser automation instructions. **Follow them:**

1. Open https://x.com/home in browser (profile='user')
2. Scroll 5 times, 2s pause between scrolls
3. For each visible tweet: extract author, text, likes, URL
4. Classify posts by the topic keywords in the JSON output
5. Save results to the `output` path shown in the JSON

## Step 2 — Fetch YouTube (parallel with step 1)

Run `scripts/feed/source/youtube.py --days 7`. This fetches directly via yt-dlp — no browser needed.

## Step 3 — Rank

Run `scripts/feed/ranker.py`. Depends on step 1 completing (needs feeds.json).

## Step 4 — Format and send digest

Run `scripts/feed/digest.py`. Deliver the output to the configured digest target:

1. Run `python3 -c "from skills.hum.scripts.config import load_config; c=load_config(); print(c['digest_target'] or '')"` to read the target.
2. If a target is returned, send there. If empty, log a warning and skip delivery.

## Step 5 — Engage (parallel with steps 3-4)

1. Open X and LinkedIn in browser
2. Check recent posts for unanswered comments
3. Draft reply suggestions for user approval
4. Suggest 3-5 new accounts to follow based on feed sources

## Step 6 — Brainstorm

Run `scripts/create/brainstorm.py --max 8`. Present top ideas and ask:
- "Any topics to add to the pipeline?"
- "Want to work on any posts today?"

## Step 7 — Learn (Sundays only)

Run `/hum learn` as defined in COMMANDS.md. Analyze feed trends, research platform algorithms, update context files.
