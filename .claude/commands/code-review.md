Review all documentation and command definitions before publishing. Follow every step.

## 1. Read all docs

Read these files in full:
- `README.md`
- `LOOP.md`
- `SKILL.md`
- `COMMANDS.md`
- `.claude/CLAUDE.md`
- `scripts/create/CREATE.md` (if it exists)

## 2. Read key scripts for ground truth

Read enough of each script to verify what it actually does:
- `scripts/init.py` — what folders and templates are created
- `scripts/loop.py` — what steps run, in what order
- `scripts/feed/refresh.py` — what source types are supported, what flags exist
- `scripts/feed/source/x.py` — how X fetching works (Bird vs browser)
- `scripts/feed/source/knowledge.py` — knowledge crawl orchestration
- `scripts/feed/source/hn.py`, `youtube.py`, `producthunt.py` — other sources
- `scripts/feed/ranker.py`, `scripts/feed/digest.py` — ranking and formatting
- `scripts/feed/sources.py` — source management (types, add/remove)
- `scripts/feed/schema.py` — feed item schema
- `scripts/create/brainstorm.py` — brainstorm flow
- `scripts/act/publish.py` — publish flow
- `scripts/act/engage.py` — engage flow
- `scripts/config.py` — config keys, defaults, env vars

## 3. Cross-check for discrepancies

For each doc file, verify:

### Accuracy
- [ ] Script paths and CLI invocations match what scripts actually accept
- [ ] Argument names and flags match argparse definitions
- [ ] Described behavior matches code logic
- [ ] Default values match code defaults
- [ ] Environment variable names match what config.py reads
- [ ] Source types listed match what refresh.py/sources.py support
- [ ] Image generation provider list and defaults match config.py

### Completeness
- [ ] Every script in `scripts/` that a user might invoke is documented
- [ ] Every `/hum` command has a matching section in COMMANDS.md
- [ ] Every folder created by init.py is listed in the init docs
- [ ] Every template file created by init.py is listed
- [ ] Knowledge source handlers are documented
- [ ] Feed source types are all listed

### Consistency across files
- [ ] Command descriptions match between README.md, SKILL.md, COMMANDS.md, and CLAUDE.md
- [ ] Architecture descriptions don't contradict each other
- [ ] Source types described the same way everywhere
- [ ] No file references "browser automation" for sources that now use direct APIs
- [ ] Image provider defaults are consistent across all files
- [ ] Path prefixes are consistent (no mixing `workspace/skills/hum/scripts/` with `skills/hum/scripts/`)

### Dead references
- [ ] No references to deleted scripts (e.g. migrate_sources.py)
- [ ] No references to features that don't exist (e.g. YC Watch if no YC source)
- [ ] No Product Hunt or LinkedIn in feed table if not actually fetched by refresh pipeline
- [ ] Digest format example only shows sections that actually appear in output

## 4. Report findings

Present a structured report:

```
## Code Review — Docs & Commands

### Issues Found
(grouped by file, with line numbers and specific fix needed)

### Cross-file Inconsistencies
(same fact described differently in multiple places)

### Verified OK
(brief list of what checks passed)
```

## 5. Fix issues

After presenting the report, ask: "Want me to fix these?"

On confirmation, fix all issues. Keep changes minimal — only fix what's wrong, don't rewrite sections that are accurate.
