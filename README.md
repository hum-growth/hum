# Hum

AI agent skill for content writing on X and LinkedIn.

**Your AI content writer**. Hum handles the full content lifecycle: it crawls your feed sources daily and sends a ranked digest, brainstorms ideas grounded in real research across YouTube, X, Reddit, HN, and the web, then drafts posts in your voice using proven writing styles — from technical storytelling to contrarian takes. Every draft goes through a research-outline-approval loop before writing begins. Once approved, Hum publishes directly to X and LinkedIn via API connectors, and can also manage engagement by drafting replies to comments and suggesting accounts to follow.

## Installation

### Claude Code

```bash
claude /install https://github.com/hum-growth/hum
```

Or clone and add to your skills directory:
```bash
git clone https://github.com/hum-growth/hum ~/.claude/skills/hum
```

### OpenClaw / ClawHub

```bash
claw install hum
```

### Codex

Copy `agents/openai.yaml` to your agents directory:
```bash
cp agents/openai.yaml ~/.codex/agents/hum.yaml
```

### Gemini CLI

```bash
gemini extensions install https://github.com/hum-growth/hum
```

## Setup

### 1. Configure data directory

Set the data directory via `openclaw.json` or environment variable (defaults to `~/Documents/hum` if neither is set):

**Option A — openclaw.json** (recommended for OpenClaw users):
```json
{
  "skills": {
    "entries": {
      "hum": {
        "config": {
          "hum_data_dir": "~/Documents/hum"
        }
      }
    }
  }
}
```

**Option B — environment variable:**
```bash
export HUM_DATA_DIR=~/Documents/hum
```

### 2. Configure digest delivery

Set where the morning digest is delivered. Supports any channel target recognised by your agent runtime (Telegram chat ID, WhatsApp number, etc.).

**Option A — openclaw.json** (recommended):
```json
{
  "skills": {
    "entries": {
      "hum": {
        "config": {
          "hum_digest_target": "telegram:123456789"
        }
      }
    }
  }
}
```

**Option B — environment variable:**
```bash
export HUM_DIGEST_TARGET=telegram:123456789
```

If `hum_digest_target` is not set, the daily loop will skip digest delivery and log a warning.

### 3. Setup workspace and content profile

Run `/hum init` to create all required directories and template files. Then edit the generated files in your data directory to set up your voice, audience, channels, and content pillars.

## Commands

| Command | Description |
|---------|-------------|
| `/hum refresh-feed` | Crawl sources, rank, send digest, save to feeds.json |
| `/hum sources` | List, add, or remove feed sources |
| `/hum config` | Show current data_dir configuration |
| `/hum brainstorm` | Research topics and generate content ideas |
| `/hum learn` | Make improvements to content strategy |
| `/hum ideas` | Show idea pipeline |
| `/hum content` | List current drafts |
| `/hum create` | Draft a post (platform, type, idea) |
| `/hum publish` | Publish an approved draft |
| `/hum engage` | Follow accounts, suggest replies, draft responses |
| `/hum samples` | Collect writing samples from social media |
| `/hum feedback` | Upvote/downvote digest items to train the ranker |

## Daily Loop

Runs at 6am SGT via `scripts/loop.py`. Sundays include an extra strategy refresh step.

```
6:00 am ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

  ┌─────────────────────┐
  │    1. Digest        │──── Fetch feed ---─────-┐
  │    /hum refresh-feed│──── Rank feed posts ───┐│
  └─────────┬───────────┘                        ││
            │                                    ││
            ▼                                    ▼▼
  ┌─────────────────────┐              ┌────────────────────┐
  │  Send Telegram      │              │  feeds.json        │
  │  morning digest     │              │  (aggregated feed) │
  └─────────────────────┘              └────────────────────┘
                                                 │
                                                 ▼
  ┌─────────────────────┐              ┌────────────────────┐
  │    2. Engage        │              │  VOICE.md          │
  │    /hum engage      │◄─────────────│  CHANNELS.md       │
  └─────────┬───────────┘              └────────────────────┘
            │
            ▼
  ┌─────────────────────────────────────┐
  │  Draft replies + follow suggestions │
  │  (presented for user approval)      │
  └─────────────────────────────────────┘

  ┌─────────────────────┐              ┌────────────────────┐
  │    3. Brainstorm    │              │  CONTENT.md        │
  │    /hum brainstorm  │◄─────────────│  (content pillars) │
  |    ideas.json       |              │  feeds.json        │
  └─────────┬───────────┘              └────────────────────┘
            │                          
            ▼
  ┌─────────────────────────────────────┐
  │  Top feed items + idea suggestions  │
  │  → "Any ideas to add?"              │
  │  → "Want to work on posts today?"   │
  └─────────────────────────────────────┘

  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
  |   4. Learn         |              ┌────────────────────┐
  │   /hum learn       │◄─────────────│  feeds.json        │
  |  (Sundays only)    │──web search──│  CHANNELS.md       │
  └ ─ ─ ─ ─ ┬ ─ ─ ─ ─ ─┘              └────────────────────┘
            │
            ▼
  ┌─────────────────────────────────────┐
  │  Analyze feed trends                │
  │  Research platform algorithms       │
  │  Update context files               │
  └─────────────────────────────────────┘

─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

  User wakes up → reviews digest, approves/edits suggestions
```

Run individual steps with `python3 scripts/loop.py --step digest|engage|brainstorm|learn`.

### Scheduling the Daily Loop

The daily loop needs a cron job or scheduler to run automatically. Setup varies by platform.

#### OpenClaw

OpenClaw has built-in scheduling. Add a `cron` entry to your `openclaw.json`:

```json
{
  "skills": {
    "entries": {
      "hum": {
        "cron": "0 6 * * *",
        "config": {
          "hum_data_dir": "~/Documents/hum"
        }
      }
    }
  }
}
```

This runs `/hum refresh-feed` → engage → brainstorm at 6am daily. OpenClaw handles process management, retries, and logging.

#### Claude Code

Claude Code does not have built-in scheduling. Use a system crontab to invoke the CLI in non-interactive mode:

```bash
# Edit your crontab
crontab -e

# Add this line (runs at 6am daily)
0 6 * * * cd /path/to/hum && claude -p "Run the daily hum loop: python3 scripts/loop.py" --allowedTools "Bash(command)" 2>&1 >> ~/.hum/loop.log
```

Alternatively, use the `/loop` skill if available in your Claude Code session:
```
/loop 24h /hum refresh-feed
```

> **Note:** The crontab approach requires Claude Code CLI (`claude`) to be installed and authenticated. The session runs headless — browser-based feed sources (X, LinkedIn, Product Hunt) will be skipped unless a browser session is available.

#### Codex

Use a system crontab to invoke the Codex CLI:

```bash
0 6 * * * cd /path/to/hum && codex -q "Run the daily hum loop: python3 scripts/loop.py" 2>&1 >> ~/.hum/loop.log
```

#### Gemini CLI

Use a system crontab to invoke the Gemini CLI:

```bash
0 6 * * * cd /path/to/hum && gemini -p "Run the daily hum loop: python3 scripts/loop.py" 2>&1 >> ~/.hum/loop.log
```

#### All platforms — manual run

You can always run the loop manually inside any agent session:
```
/hum refresh-feed
```
Or run the Python script directly:
```bash
python3 scripts/loop.py
```

## Feed

All feed sources use browser automation — the agent scrolls and extracts content via the browser tool. No API keys are required for feed crawling.

| Source | Method | API Key | Cost |
|--------|--------|---------|------|
| **X home feed** | Browser scrolling | None (browser session) | Free |
| **LinkedIn home feed** | Browser automation | None (browser session) | Free |
| **YouTube** | yt-dlp (local tool) | None | Free |
| **Hacker News** | Algolia public API | None | Free |
| **Product Hunt** | Browser scrolling | None (browser session) | Free |

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HUM_DATA_DIR` | `~/Documents/hum` | User data directory |
| `HUM_DIGEST_TARGET` | *(from openclaw.json)* | Delivery target for morning digest (e.g. `telegram:123456789`) |

### Data that leaves your machine

| Destination | Data Sent | Key Required |
|-------------|-----------|--------------|
| `hn.algolia.com` | Search query | None (public API) |
| `youtube.com` (via yt-dlp) | Channel URL | None (public) |

## Image Generation

Hum can auto-generate post images using AI. Configure a provider and Hum will generate images during the `/hum create` workflow when an `image_prompt` is set.

Set the active provider via the `HUM_IMAGE_MODEL` environment variable or `image_model` config key. Valid values: `gemini` (default), `openai`, `grok`, `minimax`.

### Providers

| Provider | Model | Env Var | Cost |
|----------|-------|---------|------|
| **gemini** (default) | gemini-2.5-flash-image | `GEMINI_API_KEY` | Free tier available |
| **openai** | gpt-image-1 | `OPENAI_API_KEY` | PAYG |
| **grok** | grok-2-image | `XAI_API_KEY` | Free with xAI tier |
| **minimax** | image-01 | `MINIMAX_API_KEY` | PAYG |

### Configuration

Set API keys as environment variables or in `openclaw.json` → `env.vars`:

```json
{
  "env": {
    "vars": {
      "GEMINI_API_KEY": "your_key_here"
    }
  }
}
```

Choose the active provider in `openclaw.json` → `skills.entries.hum.config.image_model`:

```json
{
  "skills": {
    "entries": {
      "hum": {
        "config": {
          "image_model": "gemini"
        }
      }
    }
  }
}
```

Or override with the `HUM_IMAGE_MODEL` environment variable.

### Visual Style

Add a `## Visual Style` section to your `VOICE.md` file to define your brand's visual identity. Hum appends this to every image generation prompt automatically.

### Test

```bash
python3 scripts/lib/image-gen/generate.py \
  --prompt "a clean professional image for a finance tech tweet" \
  --platform twitter --output /tmp/test.png
```

## Local Development

To develop Hum locally, symlink your OpenClaw workspace to this repo:

```bash
ln -sfn ~/Code/hum ~/.openclaw/workspace/skills/hum
```

This makes both paths point to the same files. Edits you make in `~/.openclaw/workspace/skills/hum` (e.g. via OpenClaw) are immediately reflected in `~/Code/hum`, and vice versa. There's no sync step — `git status` shows your changes right away.

To ship your changes, use the `/ship` command in Claude Code, which commits, pushes, and opens a PR following conventional commit conventions. See `.claude/commands/ship.md` for details.

Once pushed, anyone can install the updated skill via `claude /install`, `claw install hum`, etc.

