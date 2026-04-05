Commit, push, and create a PR for the current changes. Follow ALL steps below in order.

## 1. Analyze changes

- Run `git status` and `git diff` (staged + unstaged) to understand what changed.
- Run `git log --oneline -5` to see recent commit style.

## 2. Run lint (if configured)

- If `package.json` exists with a `lint` script → `npm run lint`
- If `pyproject.toml` exists with a `[tool.ruff]` section → `ruff check .`
- If neither exists, skip lint.

If lint fails, fix the issues before continuing. Do NOT skip this step.

## 3. Commit

- Stage ALL changed files. The only exceptions are `.env` files, credentials, and large binaries — everything else must be committed so the working tree is clean after shipping.
- If changes span multiple concerns, you may split into multiple commits, but every changed file must be included.
- After committing, run `git status` and confirm the working tree is clean (no unstaged changes). If there are remaining changes, stage and commit them too.
- Write a commit message using Conventional Commits: `type(scope): description`
  - Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `perf`
  - Scopes: `feed`, `create`, `act`, `publish`, `engage`, `config`, `init`, `brainstorm`, `ideas`, `connectors`, `ship`, `skills`
  - Lowercase, imperative, no period, under 72 chars.

## 4. Create branch (if needed)

If on `main`, create a new branch before pushing:

```
type/short-kebab-description
```

The branch prefix should match the commit type.

## 5. Push

Push the branch to origin with `-u` flag.

## 6. Create PR

Create a PR using `gh pr create` with:

- **Title**: Same `type(scope): description` format as the commit.
- **Body** (use HEREDOC):

```
## What
- (2-4 bullet points describing the changes)

## Why
(Motivation or problem being solved)

## Test plan
- [ ] (How to verify it works)
```

- Target the `main` branch.

## 7. Confirm

Print the PR URL when done.
