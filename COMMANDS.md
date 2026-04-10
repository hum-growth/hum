# Hum Commands

Commands for a user's content writing workflow. These are handled by the main session.

## Data Directory

All data lives in `<data_dir>` (set via `HUM_DATA_DIR` env var, defaults to `~/Documents/hum`). In the command docs below, `<data_dir>` means this directory.

---

## /hum init

**What it does:**
Sets up the hum data directory with template files and required folders. Safe to run multiple times — skips anything that already exists.

```bash
python3 skills/hum/scripts/init.py
```

**Creates:**
- `VOICE.md` — tone, style rules, word preferences
- `CONTENT.md` — content pillars with keywords for feed classification
- `AUDIENCE.md` — target audience definition
- `CHANNELS.md` — publishing platforms (LinkedIn, X), frequency, and engage settings

**Folders:**
- `feed/`, `feed/raw/`, `feed/assets/`
- `content/`, `content-samples/`, `knowledge/`, `ideas/`

After running, edit each file to set up your profile. The content pillars in `CONTENT.md` drive feed classification and brainstorming.

---

## /hum loop

**What it does:**
Runs the full daily morning workflow. Read `LOOP.md` in the skill root and follow every step.

Use `/opt/homebrew/bin/python3` for all script calls.

---

## /hum refresh-feed

**What it does:**
Crawls X/Twitter, YouTube, Hacker News, Product Hunt, and YC — ranks items, sends a formatted digest via Telegram, and saves aggregated data to `<data_dir>/feed/feeds.json`.

**This command is also triggered automatically by the "Morning Digest" cron job at 6:00am SGT daily.**

**Scrape sources:** See `<data_dir>/feed/sources.json` for all feed sources. Manage with `/hum sources`.

### Step 1 — Scrape X/Twitter feed

Use browser tool to scroll Twitter home feed. Must be logged in on the browser profile.

```
browser(action="navigate", url="https://x.com/home")
# Scroll 5–6 times with 2s pause each to load ~40–60 posts
# For each visible tweet: extract author handle, full text, likes, tweet URL
```

Run `scripts/feed/refresh.py` to see topic keyword lists. Save extracted posts as `<data_dir>/feed/feeds.json`:
```json
[{"author": "@handle", "text": "...", "likes": 123, "url": "https://x.com/...", "topics": ["AI"]}]
```

### Step 2 — Pull YouTube creator updates

```bash
python3 skills/hum/scripts/feed/source/youtube.py \
  --file <data_dir>/feed/sources.json \
  --days 7 \
  --output <data_dir>/feed/raw/youtube_feed.json
```

### Step 3 — Rank and aggregate

```bash
python3 skills/hum/scripts/feed/ranker.py \
  --input <data_dir>/feed/feeds.json \
  --output <data_dir>/feed/raw/feed_ranked.json
```

Merge all source outputs (X, YouTube, HN, PH, YC) into `feed/feeds.json` — a single aggregated JSON file that other commands (like `/hum brainstorm`) read from.

### Step 4 — Format & send digest

```bash
python3 skills/hum/scripts/feed/digest.py \
  --input <data_dir>/feed/feeds.json \
  --youtube-input <data_dir>/feed/raw/youtube_feed.json \
  --max-posts 12
```

Send the output via Telegram. Use:
```
message(action="send", channel="telegram", target="<user>", message="<digest>")
```

Target: **up to 4 posts per category** (AI / Startups / Crypto). Skip any section with 0 matches.

### Step 5 — Archive

Append today's digest to `<data_dir>/feed/feeds.json` for historical reference. Insert new entries at the top, before previous entries.

**Telegram output format:**
```
🌅 Morning Digest — [date]

━━━━━━━━━━━━
🐦 X
━━━━━━━━━━━━
AI
1. @handle — [text] · [url]

Startups
1. @handle — [text] · [url]

Crypto
1. @handle — [text] · [url]

Within the AI / Startups / Crypto sections, YouTube items are prefixed with `▶`:
1. ▶ [Channel] — [video title]
   [summary]
   [url]

━━━━━━━━━━━━
🔥 Hacker News
━━━━━━━━━━━━
1. [Title] — [points]pts · [url]

━━━━━━━━━━━━
🚀 Product Hunt
━━━━━━━━━━━━
1. [Name] — [tagline] · [upvotes]↑ · [url]

━━━━━━━━━━━━
🌱 YC Watch
━━━━━━━━━━━━
1. [Company] ([batch]) — [description] [url]
```

---

## /hum sources

**What it does:**
Manage feed sources — list, add, or remove X accounts, YouTube creators, and websites.

```bash
# List all sources
python3 skills/hum/scripts/feed/sources.py list

# Add an X account
python3 skills/hum/scripts/feed/sources.py add x <handle> [category]

# Add a YouTube creator
python3 skills/hum/scripts/feed/sources.py add youtube <url> [name]

# Add a website
python3 skills/hum/scripts/feed/sources.py add website <name> <url>

# Remove a source
python3 skills/hum/scripts/feed/sources.py remove <handle_or_name>
```

Sources are stored in `<data_dir>/feed/sources.json`.

---

## /hum config

**What it does:**
Show current hum config.

Display current configuration:
```
Hum Config
  data_dir: ~/Documents/hum
  image_model: gemini
```

To change, set environment variables or edit `openclaw.json`:
```json
{
  "skills": {
    "entries": {
      "hum": {
        "enabled": true,
        "config": {
          "hum_data_dir": "~/Documents/hum",
          "image_model": "gemini"
        }
      }
    }
  }
}
```

Or run `python3 skills/hum/scripts/config.py` to verify resolved paths.

---

## /hum brainstorm

**What it does:**
Researches trending content across multiple platforms for each of the user's content pillars, then generates and saves content ideas to `ideas/ideas.json`.

**Sources searched (per pillar):**
- YouTube — trending videos, popular creators, transcript insights
- X (Twitter) — posts, threads, engagement signals
- Reddit — threads, top comments, community sentiment
- Hacker News — stories, technical discussions, points/comments
- Polymarket — prediction markets, real-money signals on outcomes
- Web search — blogs, news, tutorials, industry publications

### Step 1 — Load context

1. Read `<data_dir>/CONTENT.md` to extract the content pillars
2. Read `<data_dir>/VOICE.md` and `<data_dir>/AUDIENCE.md` for voice and audience context
3. Read `<data_dir>/content-samples/` for voice reference
4. Read `<data_dir>/ideas/ideas.json` to know what ideas already exist (avoid duplicates)
5. Read `feed/feeds.json` for recent feed context (what's trending in the user's sources)
6. Read `<data_dir>/knowledge/` for any user-curated reference material

### Step 2 — Research each pillar

For each content pillar in CONTENT.md, research using WebSearch queries:
- `{pillar topic} trends 2026`
- `{pillar topic} discussion opinions`
- Exclude reddit.com, x.com, twitter.com (covered by script)

### Step 3 — Synthesize and generate ideas

For each pillar's research results:
1. Read the full output — it contains Reddit, X, YouTube, Hacker News, Polymarket, and web data
2. Identify the highest-signal findings: cross-platform mentions, high-engagement posts, strong opinions, surprising data
3. Cross-reference with existing ideas in `ideas.json` to find gaps
4. Generate 3–5 content ideas per pillar, each grounded in specific research findings

### Step 4 — Save ideas

Append each idea to `<data_dir>/ideas/ideas.json`. Each idea is an object:

```json
{
  "id": "I042",
  "title": "CFOs Replacing Headcount with AI Agents",
  "pillar": "AI in Finance",
  "status": "pending",
  "date": "2026-04-03",
  "post_type": "LinkedIn Post",
  "hook": "The opening line or tension — one sentence",
  "angle": "The specific POV or contrarian take — 1-2 sentences",
  "evidence": [
    "Source 1: specific data point with platform attribution — e.g. per @handle on X",
    "Source 2",
    "Source 3"
  ],
  "why_now": "Why this is timely — what triggered it in the research",
  "notes": "Optional: format suggestions, media ideas, related ideas"
}
```

**ID assignment:** Increment from the highest existing ID in `ideas.json` (e.g. I001, I002...).

### Step 5 — Present summary

Show the user a summary grouped by pillar:

```
📋 Brainstorm Results

Researched [N] pillars across YouTube, X, Reddit, HN, Polymarket, and web.

**[Pillar 1]**
  I042 · CFOs Replacing Headcount with AI Agents (LinkedIn Post)
  I043 · The Real Cost of Manual Reconciliation (X Thread)
  I044 · Why FP&A Teams Are Shrinking (LinkedIn Post)

**[Pillar 2]**
  I045 · ...

[N] new ideas saved to ideas.json
```

Ask: "Want to refine any of these, change post types, or discard some?"

### Rules

- Research takes 5–15 minutes depending on the number of pillars — tell the user upfront
- Always ground ideas in specific research findings, not generic knowledge
- Each idea must cite at least one real source from the research
- Avoid duplicating ideas already in `ideas.json`
- Prefer ideas where the same signal appears across multiple platforms (strongest evidence)

---

## /hum learn

**What it does:**
Analyzes the feed and researches current platform algorithms to improve content strategy. Updates context files in `<data_dir>` based on what it learns.

**When to use it:**
- Weekly strategy refresh, especially on Saturdays
- After a meaningful batch of new posts has been published
- When content performance feels flat

**Steps:**
1. Read `<data_dir>/VOICE.md`, `<data_dir>/AUDIENCE.md`, `<data_dir>/CHANNELS.md`, and `<data_dir>/CONTENT.md`
2. Read `feed/feeds.json` — analyze what topics are trending, which posts got high engagement, what formats are performing
3. Scrape your own post performance via browser:

   Run:
   ```bash
   python3 -m act.analyze --platform all --account <account>
   ```
   This returns browser instructions — follow them to scrape your X and LinkedIn profiles.

   From the scraped data, identify:
   - Top 3 posts by engagement (likes + replies + reposts) on each platform
   - Patterns: which formats (tweet, thread, LinkedIn post), hooks, or topics performed best
   - Any clear underperformers
   - If no posts are found or data is thin, note it clearly — do not fabricate

4. Research platform algorithms with web search:
   - For each channel in `CHANNELS.md`, search for current algorithm priorities, favored formats, and distribution patterns
   - e.g. `"LinkedIn algorithm 2026" what content gets reach`, `"X Twitter algorithm" post format engagement 2026`
   - Focus on actionable signals: post length, format, timing, media usage, engagement patterns
5. Update context files in `<data_dir>`:
   - Update `VOICE.md` if tone/style adjustments are supported by evidence
   - Update `CHANNELS.md` with new platform-specific tactics or format guidance
   - Update `CONTENT.md` if certain pillars are trending or underperforming
   - Only make changes backed by evidence — do not speculate
6. Present a concise summary:
   - Your top-performing posts and what they have in common
   - What the algorithms are currently favoring
   - What changes were made to context files
   - What to test next week

**Rules:**
- Use web search for current algorithm and trend research
- Be specific with dates in trend observations
- Only update context files when evidence supports it — don't make speculative changes
- Prefer evidence from feed data and platform research over generic social-media advice

---

## /hum ideas

**What it does:**
Displays the full idea list from `<data_dir>/ideas/ideas.json`.

**Output format:**
```
📋 Ideas

✅ Approved (ready to draft)
I001 · The K-shaped VC market
I002 · AI agents as headcount
...

📝 Drafted (awaiting publish)
I006 · [idea]

🟡 Pending (not yet approved)
(none)

✔️ Published
(none)
```

Always show count per status. End with: "Use `/hum create [platform] [post type] [idea ID]` to draft — e.g. `/hum create LinkedIn Post I001`"

---

## /hum content

**What it does:**
Lists drafts and assets across the content pipeline.

**Steps:**
1. Read files in `<data_dir>/content/drafts/` (unPublished drafts)
2. Read files in `<data_dir>/content/published/` (sent posts)
3. Read files in `<data_dir>/content/images/` (generated images)
4. Show them grouped by status and platform
5. Include draft status metadata if present in the file header
6. If no drafts exist, say so plainly

**Output format:**
```
🗂 Drafts

LinkedIn
- LinkedIn - The Finance Team of 2028.md · outline — 2026-03-24

X
- X - AI Agents as Headcount.md · ready — 2026-03-25

✅ Published
- X - OpEx Structure with AI.md — published 2026-04-07

🖼 Images
- ai-math-trap-2026-04-08.png
```

End with: "Use `/hum publish [draft file]` to publish or `/hum create [platform] [post type] [idea ID]` to draft something new."

---

## /hum create [platform] [post type] [idea ID or keyword]

**What it does:**
Researches an idea, proposes an outline for approval, then drafts content in the user's voice.

**Full command spec:** [`scripts/create/CREATE.md`](scripts/create/CREATE.md)

**Flow:** Load context → Deep research (3-5 web searches) → Propose outline → User approval → Write draft → Save & track

### Image generation

When drafting, set the `image_prompt` field on the post. The `validate()` call automatically generates the image using the configured provider (default: Gemini).

To include an image with a draft:
- Add `image_prompt: "your image description"` when creating the post
- `validate(post)` ��� generates image → sets `media_path`
- `format_preview(post)` → shows `(run validate() to generate the image)` until generated
- If `VOICE.md` has a `## Visual Style` section, it is appended to the prompt automatically

Providers:
1. `gemini` — gemini-2.5-flash-image (default)
2. `openai` — gpt-image-1
3. `grok` — grok-2-image (xAI API)
4. `minimax` — image-01

Set the active provider in `openclaw.json` → `skills.entries.hum.config.image_model` or via the `HUM_IMAGE_MODEL` env var.

---

## /hum publish [draft file or idea ID]

**What it does:**
Publishes an approved draft to X or LinkedIn via platform connectors (API-based).

**⚠️ Always confirm with the user before posting. Show the final text and ask "Ready to publish?" before running any publish script.**

### Shared steps

**Steps:**
1. Read the draft from `<data_dir>/content/drafts/`
2. Read connector docstrings in `skills/hum/scripts/act/connectors/` for credential shape and connector details
3. Show the exact final text and ask: "Ready to publish to [platform]?"
4. Run a preview first:
   `python3 workspace/skills/hum/scripts/act/publish.py --draft "[draft path]"`
5. On confirmation, publish using:
   `python3 workspace/skills/hum/scripts/act/publish.py --draft "[draft path]" --account "[account]" --publish --update-draft`
6. If a LinkedIn image or X first-post image exists, include:
   `--image "/absolute/path/to/image.png"`
7. Confirm success from the returned URL / ID
8. Update `ideas.json`: status → `published`, add date and URL

### Account selection
- Use the account mappings defined by the current user's `CHANNELS.md`.
- If the target API account is missing credentials, stop and tell the user which account file entry is missing

### Rules
- Never publish without explicit "yes" / "publish" / "post it" confirmation
- Always show the exact text that will be posted before running the script
- If posting fails (missing token, scope issue, rate limit, validation error, etc.) — report the error, do not retry silently
- After publishing, save the post URL to the draft file

### Platform constraints

- **X:** Supports single posts and numbered threads through the script. First-post image attachment is supported via `--image`.
- **LinkedIn:** Supports feed posts and single-image feed posts through API.
- **Other channels:** If `CHANNELS.md` lists additional platforms, follow those channel-specific workflows instead of `publish.py`.
- **LinkedIn native long-form articles:** do not assume they are API-publishable. If the draft is a real long-form article, follow the LinkedIn Article workflow below.

---

### LinkedIn Article Publish Workflow

When publishing a LinkedIn article (long-form, `_Format: LinkedIn Article_` in the draft), follow these steps in order:

#### Step 1 — Generate cover image

Use an image generation API (Gemini or MiniMax) to create a cover image for the article.

- **API key:** uses the configured image provider (see `/hum config`)
- **Prompt:** generate a LinkedIn article cover image for the article title, matching the user's style preferences
- **Save to:** `<data_dir>/content/images/LinkedIn Cover - [article title].png`
- Show the image to the user and ask for approval before proceeding

#### Step 2 — Draft the intro feed post

Write a short LinkedIn feed post (100–150 words) to introduce the article:
- Opens with the article's core tension or hook (not "I wrote an article")
- 2–3 sentences of substance — what the reader will get
- Ends with: "Full article 👇" or "Link in comments." (choose based on `CHANNELS.md` rules)
- Save to: `<data_dir>/content/drafts/LinkedIn Post - [article title].md`
- Show to user for approval before publishing

#### Step 3 — Publish the article (browser)

LinkedIn articles must be published via the LinkedIn article editor — not API.

1. Open `https://www.linkedin.com/article/new/` via the browser tool
2. Paste the article content from the draft file
3. Upload the cover image from Step 1
4. Set the article title
5. Click Publish
6. Capture the published article URL

#### Step 4 — Publish the intro feed post

Once the article URL is known:
1. Append the URL to the intro feed post draft
2. Publish via:
```bash
cd workspace/skills/hum/scripts && python3 -m act.connectors.linkedin \
  --account <account> \
  --text "<intro post text>\n\n<article URL>"
```

#### Step 5 — Update tracking

- Update `ideas.json`: status → `published`, add date and article URL
- Update the draft file with publish metadata

---

## /hum engage [linkedin | x | all]

**What it does:**
Three things in one command:
1. **Follow** 5–10 relevant accounts on X (finance, CFO, AI, fintech)
2. **Suggest replies** to posts from accounts the user's active X account follows — insightful engagement to build visibility
3. **Draft responses** to replies/comments on the user's own posts

Default: `all` (both platforms). Specify `x` or `linkedin` to scope.

**Before running:** Read `<data_dir>/CHANNELS.md` → "Engage Command Settings" to get the per-platform config (follows, engagement plays, response count).

**⚠️ Never post anything without the user's explicit approval.**

---

### Part 1 — Follow relevant accounts (X only)

1. Open https://x.com in the OpenClaw browser using the user's active X account
2. Search for 5–10 accounts in categories that match the user's niche and audience, using `<data_dir>/AUDIENCE.md` and `<data_dir>/CHANNELS.md` as the source of truth. Derive the relevant topics, industries, and account types from those files — do not assume a specific niche.
3. For each: navigate to their profile, click Follow if not already following
4. Skip accounts already followed (check before clicking)
5. Report: "Followed X new accounts: [list]"

**Prioritise accounts that:**
- Have 10K+ followers in finance/CFO/AI space
- Post regularly (active in last 7 days)
- Are not already followed

---

### Part 2 — Suggest outbound replies (engagement plays)

**Goal:** Reply to recent posts from niche accounts in a way that feels authentic, adds real value, and builds visibility in the right circles.

**Step 1 — Identify target accounts**

Pull target accounts from two sources:
1. `<data_dir>/feed/sources.json` — entries with `type: x_profile` or `type: linkedin_profile`
2. `<data_dir>/knowledge/index.md` — the "Influencers & Thought Leaders" tables (Name + Platform columns)

Combine into a single candidate list. Prioritise accounts that appear in both sources — these are most relevant to the user's niche.

**Step 2 — Find recent posts worth replying to**

For each candidate account (work through 8–12 to find 3–5 good matches):
1. Navigate to their profile in the browser
2. Find their most recent post from the **last 48 hours** — skip if nothing recent
3. Read the full post text carefully

A post is worth replying to if it:
- Makes a specific claim, shares data, or argues a position
- Is in the user's niche (check against `<data_dir>/CONTENT.md` content pillars)
- Has some engagement already — signals the conversation is active
- Is not a repost or share of someone else's content

**Step 3 — Draft replies**

Before drafting, read `<data_dir>/VOICE.md` for tone and style.

For each selected post, draft a reply that:
- **Anchors to something specific** in the post — a stat, a claim, a specific phrase. Never summarise generically.
- **Does one of:** adds a related data point, offers a contrarian take with a concrete reason, or extends the argument with a specific example
- **Feels like a person wrote it** — no filler openers ("Great point!", "Love this", "So true", "This is spot on"). Start with the substance.
- **Is concise** — 1–2 sentences for X (under 280 chars), 2–3 sentences for LinkedIn
- **Matches the user's voice** per VOICE.md — calm, direct, grounded in specifics

**Step 4 — Present for approval**

```
💬 Engagement Plays — suggested replies

1. @[account] on [platform] — "[exact quote or key claim from their post]"
   → [drafted reply]

2. @[account] on [platform] — "[exact quote or key claim from their post]"
   → [drafted reply]
```

Ask: "Which should I post? Any edits?"

---

### Part 3 — Gather posts and inbound comments

**LinkedIn:**
1. Open the user's recent LinkedIn activity page in the OpenClaw browser
2. If not logged in, stop and ask the user to log in first
3. Scan the 5 most recent posts
4. For each post, click into it and read the comments section
5. Collect comments that haven't been replied to by the user yet
6. Skip: generic congratulations ("Great post!"), spam, and comments with no substance worth engaging

**X (Twitter):**
1. Open the user's relevant X account
2. Check the 5 most recent tweets/threads from the relevant configured account
3. For each, click into it and read the replies
4. Collect replies that haven't been responded to yet
5. Skip: bots, spam, generic "nice" replies, and trolls not worth engaging

### Part 4 — Draft inbound responses

1. Read `<data_dir>/VOICE.md` for tone and style
2. For each comment worth responding to, draft a reply that:
   - Matches the user's voice — calm, direct, no fluff if that is what `VOICE.md` specifies
   - Adds value — extends the point, shares a specific insight, asks a follow-up
   - Is concise — 1–3 sentences max for LinkedIn, under 280 chars for X
   - Feels human — not templated, not sycophantic
   - Engages thoughtfully with the commenter's specific point
3. Present all drafts in a numbered table:

```
💬 Responses Ready

LinkedIn — [Post title / first line]
  1. @[commenter]: "[their comment summary]"
     → [your drafted response]

  2. @[commenter]: "[their comment summary]"
     → [your drafted response]

X — [Tweet text summary]
  3. @[replier]: "[their reply summary]"
     → [your drafted response]

  (no replies needing response)
```

4. Ask: "Which responses should I post? (numbers, 'all', or 'none')"

### Part 5 — Post approved responses

**On approval (numbers or "all"):**

**LinkedIn:**
1. Navigate to the specific post
2. Find the comment being replied to
3. Click "Reply" under that comment
4. Type the approved response text
5. Click the reply/submit button
6. Screenshot to confirm

**X:**
1. Navigate to the specific tweet/reply
2. Click "Reply"
3. Type the approved response text
4. Click "Reply" to post
5. Screenshot to confirm

**After posting all approved responses:**
- Report: "Posted X/Y responses. [list which ones]"
- If any failed (not logged in, rate limit, element not found), report the error per response

### Response style guidelines

- **Don't be a bot.** Vary sentence structure. Don't start every reply the same way.
- **Add, don't echo.** Never just agree — extend the thought with a new angle or specific example.
- **Match energy.** Short casual comment → short casual reply. Thoughtful paragraph → thoughtful reply.
- **Use names** when they're visible — "Good point, [Name]" feels more human than "Good point."
- **Disagree respectfully** when it's warranted — the user may have opinions and shouldn't dodge them.
- **Skip the gratitude performance.** Don't say "Thanks for sharing!" or "Great question!" — just answer.

---

## /hum samples

**What it does:**
Collects real writing samples from the user's social media profiles into `<data_dir>/content-samples/` using the browser.

**Steps:**
1. Read `<data_dir>/CHANNELS.md` to find the user's social media profile URLs
2. Open each profile URL in the browser
3. Scroll through recent posts — aim for 10–20 representative samples across platforms
4. For each post, extract:
   - Full text content (preserve exactly, do not edit)
   - Platform (X, LinkedIn, etc.)
   - Post type (tweet, thread, post, article)
   - Date posted (if visible)
   - Engagement metrics (likes, reposts, comments — if visible)
5. Save each sample as a separate file in `<data_dir>/content-samples/`:
   - Filename: `<platform> - <short-title-or-date>.md`
   - Format:
     ```
     ---
     platform: LinkedIn
     type: post
     date: 2026-03-15
     likes: 142
     comments: 23
     reposts: 8
     ---

     [exact post text]
     ```
6. Summarize: samples collected per platform, date range, top performers by engagement

**When to run:**
- First setup of a new profile
- User asks to refresh (`/hum samples`)
- Before drafting if `<data_dir>/content-samples/` is empty — prompt the user

**Rules:**
- Only collect public posts from the user's own accounts
- Preserve original text exactly
- Include engagement numbers to identify what resonates
- Overwrite stale samples on refresh — keep the folder current

---

## /hum feedback

**What it does:**
Log upvote/downvote on digest items and update feed preferences.

```bash
python3 skills/hum/scripts/feed/feedback.py log --item 3 --vote up
python3 skills/hum/scripts/feed/feedback.py log --item 1 --vote down
python3 skills/hum/scripts/feed/feedback.py show     # show current preferences
python3 skills/hum/scripts/feed/feedback.py history   # show recent votes
```

Preferences are stored in `feed/assets/preferences.json` and used by the ranker to score future feed items.

---

## Notes for the agent

- Always read VOICE.md, `<data_dir>/content-samples/`, and `<data_dir>/knowledge/` before drafting — do not rely on memory
- `ideas.json` is the source of truth for all idea state
- Never auto-post to any platform — drafts are always reviewed first
- When brainstorming, avoid ideas already in `ideas.json`
- IDs are permanent — never reuse a retired ID
- Drafts are named `[channel] - [title].md` in the `content/` folder
