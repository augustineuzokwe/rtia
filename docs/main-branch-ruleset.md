# `main` branch ruleset

The companion JSON file [`main-branch-ruleset.json`](main-branch-ruleset.json)
is a GitHub Ruleset configuration that protects `main` after the public
flip. The repo ships it as version-controlled config so the protection
posture is reviewable in git history, not buried in repo settings.

## What it enforces

| Rule | What it blocks | Why |
|---|---|---|
| **No deletion** | `git push --delete origin main` | `main` is the canonical history. |
| **No force-push** (`non_fast_forward`) | `git push --force` to `main` | Rewriting history breaks every clone. |
| **Linear history** | Merge commits | Matches the squash-merge convention used throughout the repo. |
| **PR required** | Direct commits to `main` | All changes ship via PR + CI (the project convention since day one). |
| **`Lint, format, test` must pass** | PRs whose CI lint/test job fails | Catches Python syntax errors, ruff violations, secret leaks, broken unit tests before merge. |

## What it deliberately does NOT enforce

- **Approving reviews required: 0.** Single-maintainer repo. The
  intent is that automation gates code (CI + CodeRabbit), not human
  review, until the project has co-maintainers.
- **The `Regression (eval gate)` job is NOT a required check.** It's
  conditional (only runs on PRs touching `agents/`, `prompts/`, or
  `evals/`). Marking it required would block doc-only PRs because
  GitHub treats a not-run check as "not passing". The path filter in
  `.github/workflows/ci.yml` is the real gate.
- **No bypass actors.** Even the repo owner has to go through a PR.

## How to apply

### Option A: via the UI (recommended)

1. Repo Settings → **Rules** → **Rulesets** → **New ruleset** → **Import a ruleset**.
2. Upload [`docs/main-branch-ruleset.json`](main-branch-ruleset.json).
3. Verify the `Lint, format, test` status-check `integration_id` resolved correctly
   (GitHub may prompt you to re-pick it from the dropdown - if it's already wired
   to a recent CI run, it'll be there).
4. Save.

### Option B: via `gh` API

```bash
gh api -X POST repos/augustineuzokwe/rtia/rulesets \
  --input docs/main-branch-ruleset.json
```

Returns the created ruleset including a numeric `id`. Re-apply with
`PUT repos/.../rulesets/<id>` if you tweak the JSON later.

## When to revisit

- **Adding co-maintainers** → bump `required_approving_review_count` to 1.
- **Adding a new always-on CI check** → add it to `required_status_checks`.
- **Adding a deploy step that needs `main` push access** → add a
  named bypass actor (a GitHub App or org-admin team), never a personal
  account.
