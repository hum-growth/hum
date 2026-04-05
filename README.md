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

Set the `HUM_DATA_DIR` environment variable (defaults to `~/Documents/hum` if omitted):

```bash
export HUM_DATA_DIR=~/Documents/hum
```

### 2. Initialize

Run `/hum init` to create all required directories and template files. Then edit the generated files in your data directory to set up your voice, audience, channels, and content pillars.

### 3. Credentials (for publishing)

Create credential files for the platforms you want to publish to:

```bash
mkdir -p ~/.hum/credentials
```

See `COMMANDS.md` for the credential file format for X and LinkedIn.

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HUM_DATA_DIR` | `~/Documents/hum` | User data directory |
| `CREDENTIALS_DIR` | `~/.hum/credentials/` | API credential files |
| `X_USER_ACCESS_TOKEN` | (from file) | X API token override |
| `LINKEDIN_ACCESS_TOKEN` | (from file) | LinkedIn API token override |
| `LINKEDIN_AUTHOR_URN` | (from file) | LinkedIn author URN override |

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

## Local Development

To develop Hum locally, symlink your OpenClaw workspace to this repo:

```bash
ln -sfn ~/Code/hum ~/.openclaw/workspace/skills/hum
```

This makes both paths point to the same files. Edits you make in `~/.openclaw/workspace/skills/hum` (e.g. via OpenClaw) are immediately reflected in `~/Code/hum`, and vice versa. There's no sync step — `git status` shows your changes right away.

To ship your changes, use the `/ship` command in Claude Code, which commits, pushes, and opens a PR following conventional commit conventions. See `.claude/commands/ship.md` for details.

Once pushed, anyone can install the updated skill via `claude /install`, `claw install hum`, etc.

