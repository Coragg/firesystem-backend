---
name: git
description: Use whenever working with git: branches, commits, and history
---
# Rules: Git

## Hard Restrictions
- __NEVER__ run `git push`.
- __NEVER__ run `git commit` without my explicit confirmation. Show me the proposed message and the staged files first.
- Never run destructive commands (`reset --hard`, `clean -fd`, `checkout .`, `branch -D`, force operations) unless I ask for them.
- Never rewrite history (`rebase`, `commit --amend`) unless I ask for it.
- Never skip hooks (`--no-verify`).

## Branches
- `main` is the stable branch. Don't work on it directly.
- `develop` is the integration branch for day-to-day work.
- For larger changes, branch from `develop` using `<type>/<short-description>`, e.g. `feat/hotspot-endpoint`.

## Commit Messages
Use Conventional Commits, in English:

```text
<type>: <short summary>
```

- **Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `build`.
- Summary in imperative mood, lowercase, 72 characters max, no trailing period.
- One logical change per commit. Don't mix unrelated changes.
- Add a body only when the *why* isn't obvious from the summary.

**Examples:**
- `feat: add hotspot calendar endpoint`
- `fix: handle empty area in query`
- `docs: update agent rules`

## Commit Proposals
- ALWAYS offer me three commit message options and wait for me to choose the best one for the case.
- Each option must differ meaningfully (e.g. scope, type, or level of detail), not just in wording.
- Only commit after I pick one and confirm.

## Attribution
- __NEVER__ add `Co-Authored-By` lines or any other trailer.
- __NEVER__ mention the model, Claude, or any AI tool in commit messages, PR descriptions, or branch names.
- The message only explains what was done.

## Before Committing
- Run `uv run pytest`; all tests must pass.
- Stage files explicitly by name; don't use `git add -A` or `git add .`.
- Never commit secrets, `.env` files, or real credentials.
- Keep `pyproject.toml` and `uv.lock` in sync when dependencies change.

## Style
- Don't invent changes the diff doesn't contain.
- Don't repeat in the body what the summary already says.
