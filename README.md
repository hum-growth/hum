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
          "data_dir": "~/Documents/hum"
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

### 2. Initialize

Run `/hum init` to create all required directories and template files. Then edit the generated files in your data directory to set up your voice, audience, channels, and content pillars.

### 3. API Keys

Hum uses different APIs for feed crawling, publishing, and image generation. Many feed sources are **free with no API key**. The table below shows what's needed for each capability.

#### Feed Sources

| Source | Method | API Key | Cost |
|--------|--------|---------|------|
| **X home feed** | Browser scrolling | None (browser session) | Free |
| **X profiles** | [ScrapeCreators](https://scrapecreators.com) API | `SCRAPECREATORS_API_KEY` | 100 free credits, then PAYG |
| **LinkedIn profiles** | [ScrapeCreators](https://scrapecreators.com) API | `SCRAPECREATORS_API_KEY` | 100 free credits, then PAYG |
| **YouTube** | yt-dlp (local tool) | None | Free |
| **Hacker News** | Algolia public API | None | Free |
| **Product Hunt** | Browser scrolling | None (browser session) | Free |

One `SCRAPECREATORS_API_KEY` covers both X profile crawling and LinkedIn profile crawling. Sign up at [scrapecreators.com](https://scrapecreators.com) for 100 free API credits.

X home feed and Product Hunt use browser automation (the agent scrolls the page via browser tool). X/LinkedIn profile crawling uses the ScrapeCreators REST API to fetch posts without browser control.

**Set the key in `openclaw.json`:**
```json
{
  "env": {
    "vars": {
      "SCRAPECREATORS_API_KEY": "your_key_here"
    }
  }
}
```

Or as an environment variable:
```bash
export SCRAPECREATORS_API_KEY=your_key_here
```

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HUM_DATA_DIR` | `~/Documents/hum` | User data directory |
| `IMAGE_MODEL` | `gemini` | Image generation provider override |
| `SCRAPECREATORS_API_KEY` | (from `openclaw.json`) | X profile + LinkedIn profile crawling |
| `CREDENTIALS_DIR` | `~/.hum/credentials/` | Publishing credential files |
| `X_USER_ACCESS_TOKEN` | (from file) | X API token override |
| `LINKEDIN_ACCESS_TOKEN` | (from file) | LinkedIn API token override |
| `LINKEDIN_AUTHOR_URN` | (from file) | LinkedIn author URN override |

### Data that leaves your machine

| Destination | Data Sent | Key Required |
|-------------|-----------|--------------|
| `api.scrapecreators.com` | Profile handle/URL | `SCRAPECREATORS_API_KEY` |
| `api.x.com` | Post content (publishing) | `X_USER_ACCESS_TOKEN` |
| `api.linkedin.com` | Post content (publishing) | `LINKEDIN_ACCESS_TOKEN` |
| `generativelanguage.googleapis.com` | Image prompt (Gemini image gen) | `GEMINI_API_KEY` |
| `api.openai.com` | Image prompt (OpenAI image gen) | `OPENAI_API_KEY` |
| `api.x.ai` | Image prompt (Grok image gen) | `XAI_API_KEY` |
| `api.minimax.chat` | Image prompt (MiniMax image gen) | `MINIMAX_API_KEY` |
| `hn.algolia.com` | Search query | None (public API) |
| `youtube.com` (via yt-dlp) | Channel URL | None (public) |

Each API key is transmitted only to its respective endpoint. Your ScrapeCreators key is never sent to X or LinkedIn APIs, and vice versa.

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

## Image Generation

Hum can auto-generate post images using AI. Configure a provider and Hum will generate images during the `/hum create` workflow when an `image_prompt` is set.

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

Or override with the `IMAGE_MODEL` environment variable.

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

