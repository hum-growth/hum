# Hum

Content writing and feed intelligence skill for LinkedIn and X.

Hum handles the full content lifecycle: crawls your feed sources daily and sends a ranked digest, brainstorms ideas grounded in real research across YouTube, X, Reddit, HN, and the web, then drafts posts in your voice. Every draft goes through a research-outline-approval loop before writing begins. Once approved, Hum publishes directly to X and LinkedIn via API connectors, and manages engagement by drafting replies and suggesting accounts to follow.

## Commands

| Command | What it does |
|---------|-------------|
| `/hum init` | Set up data directory with templates |
| `/hum refresh-feed` | Crawl sources, rank, send digest |
| `/hum sources` | Manage feed sources (X, YouTube, websites) |
| `/hum config` | Show current configuration |
| `/hum brainstorm` | Research topics and generate content ideas |
| `/hum learn` | Refresh content strategy |
| `/hum ideas` | Show idea pipeline |
| `/hum content` | List drafts |
| `/hum create [platform] [type] [idea]` | Draft a post |
| `/hum publish [draft]` | Publish to X or LinkedIn |
| `/hum engage [platform]` | Follow suggestions, replies, engagement |
| `/hum samples` | Collect writing samples from social media |
| `/hum feedback` | Upvote/downvote digest items |

## Setup

1. Run `/hum init` to create all directories and template files
2. Edit the generated files in your data directory:
   - `VOICE.md` — your writing style and tone
   - `AUDIENCE.md` — who you write for
   - `CHANNELS.md` — where you publish, account mappings
   - `CONTENT.md` — content pillars with keywords
3. Set up credentials for publishing:
   - Create `~/.hum/credentials/x.json` and/or `linkedin.json`
   - See `COMMANDS.md` for credential format
4. Optionally collect writing samples: `/hum samples`

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HUM_DATA_DIR` | `~/Documents/hum` | User data directory |
| `CREDENTIALS_DIR` | `~/.hum/credentials/` | API credential files |
| `X_USER_ACCESS_TOKEN` | (from file) | X API token override |
| `LINKEDIN_ACCESS_TOKEN` | (from file) | LinkedIn API token override |
| `LINKEDIN_AUTHOR_URN` | (from file) | LinkedIn author URN override |

## Architecture

- `scripts/feed/` — Feed crawling, ranking, digest formatting, source management
- `scripts/create/` — Brainstorming, post type schemas, draft creation
- `scripts/act/` — Publishing, engagement, analytics
- `scripts/act/connectors/` — Platform-specific API connectors (X, LinkedIn)
- `scripts/config.py` — Shared config loader
- `scripts/loop.py` — Daily automation orchestrator

## Development

Source skill lives in `~/.openclaw/workspace/skills/hum/`. Run `bash sync.sh` to export changes to this repo.
