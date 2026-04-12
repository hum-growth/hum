# Hum

Content writing and feed intelligence skill for LinkedIn and X.

Hum handles the full content lifecycle: crawls your feed sources daily and sends a ranked digest, brainstorms ideas grounded in real research across YouTube, X, Reddit, HN, and the web, then drafts posts in your voice. Every draft goes through a research-outline-approval loop before writing begins. Once approved, Hum publishes directly to X and LinkedIn via API connectors, and manages engagement by drafting replies and suggesting accounts to follow.

## Commands

| Command | What it does |
|---------|-------------|
| `/hum init` | Set up data directory with templates |
| `/hum loop` | Run the full daily morning workflow |
| `/hum refresh-feed` | Crawl all sources, rank, send digest |
| `/hum crawl` | Crawl knowledge sources (blogs, podcasts, YouTube transcripts) |
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
   - `knowledge/index.md` — knowledge sources (blogs, YouTube transcripts, podcasts)
3. Set up credentials for publishing:
   - Create `~/.hum/credentials/x.json` and/or `linkedin.json`
   - See `COMMANDS.md` for credential format
4. Optionally collect writing samples: `/hum samples`

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HUM_DATA_DIR` | `~/Documents/hum` | User data directory |
| `CREDENTIALS_DIR` | `~/.hum/credentials/` | API credential files |
| `HUM_X_AUTH_TOKEN` | (from file) | X session AUTH_TOKEN for Bird API |
| `HUM_X_CT0` | (from file) | X session CT0 cookie for Bird API |
| `X_USER_ACCESS_TOKEN` | (from file) | X API token override (publishing) |
| `LINKEDIN_ACCESS_TOKEN` | (from file) | LinkedIn API token override |
| `LINKEDIN_AUTHOR_URN` | (from file) | LinkedIn author URN override |
| `HUM_IMAGE_MODEL` | `gemini` | Image generation provider |

## Architecture

- `scripts/feed/` — Feed crawling, ranking, digest formatting, source management
- `scripts/feed/source/` — Source-specific crawlers (X via Bird API, HN, YouTube digest)
- `scripts/feed/source/handlers/` — Knowledge base crawl handlers (RSS, sitemap, YouTube transcripts, podcasts)
- `scripts/feed/source/knowledge.py` — Knowledge crawler orchestrator (parses `knowledge/index.md`, dispatches handlers)
- `scripts/create/` — Brainstorming, post type schemas, draft creation
- `scripts/act/` — Publishing, engagement, analytics
- `scripts/act/connectors/` — Platform-specific API connectors (X, LinkedIn)
- `scripts/config.py` — Shared config loader
- `scripts/loop.py` — Daily automation orchestrator

## Source configuration

Two source lists serve different purposes:

- `feed/sources.json` — Social/ephemeral sources (X feed, X profiles, HN, YouTube channels). Managed via `/hum sources`.
- `knowledge/index.md` — Long-form knowledge sources (RSS blogs, sitemaps, YouTube transcripts, podcasts). Defined as markdown tables with Key, Handler, Feed URL columns. Full articles saved to `knowledge/<source_key>/` as markdown files with frontmatter.

## Development

Source skill lives in `~/.openclaw/workspace/skills/hum/`. Run `bash sync.sh` to export changes to this repo.
