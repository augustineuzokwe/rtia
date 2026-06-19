# CLAUDE.md - RTIA repo context for Claude Code

This file is read on every Claude Code session opening this repo. It compiles project-specific rules and operational knowledge so any session - fresh install, different machine, different developer - works to the same standard.

If you're a fresh Claude Code session reading this for the first time: **read top to bottom before making changes**. The rules below are not suggestions; they're how this project is run.

---

## 1. What RTIA is

A multi-agent AI assistant that turns raw software requirements (feature requests, PRD snippets, meeting notes) into **one backlog-ready user-story artifact** with four sections:

1. **Description** - what the role wants ("As a/an X, I want Y")
2. **Objective** - the value/outcome the role gets
3. **Acceptance Criteria** - Given/When/Then format
4. **Test Cases** - happy path + edge cases + negative paths

The artifact is designed to paste directly into a Jira Epic or stand alone on a GitHub Project backlog. Every agent in the pipeline contributes to one or more sections of this single artifact - there are no standalone outputs.

Pipeline today has two paths chosen by a LangGraph conditional edge at the PO checkpoint:

```
Deep path (single-story requirements, implied_stories ≤ 1):
  START → Analyst → PO Checkpoint → Story Writer → Story Review Checkpoint
        → AC Generator → Test Case Writer → Composer → Reviewer → END

Split path (multi-story requirements, implied_stories ≥ 2):
  START → Analyst → PO Checkpoint (CheckboxGroup UI) → split_node → END
```

The deep path produces a full `FinalUserStory` (Description / Objective / ACs / Test Cases). The split path produces lightweight placeholder stories only - the PO re-runs RTIA on any individual placeholder title later to deep-dive that one. See `PR #162` for the topology shift rationale.

---

## 2. Project layout

```
rtia/
├── agents/                # LangGraph agent definitions (Analyst, Story Writer, future…)
├── prompts/               # Prompt templates as Python modules (versioned with code)
├── tests/                 # Mocked unit tests
├── scripts/               # Live demo entry points (run_pipeline_demo.py)
├── evals/                 # Golden datasets + eval runner
│   ├── sample-requirements/  # 7 sample inputs (3 baseline + 4 adversarial)
│   ├── EVAL_DATA_SPEC.md     # Contract for ground-truth files
│   └── validate_samples.py   # Sample structural validator
├── .github/workflows/     # CI (lint + format + tests)
├── api/                   # FastAPI app + bearer-token auth + exporters bridge
├── ui-react/              # React + Tailwind + shadcn/ui SPA mounted at /
├── exporters/             # Jira + GitHub backends behind one Exporter Protocol
└── docs/                  # ADRs + USAGE.md
```

`agents/` and `prompts/` mirror 1:1 - each agent owns one prompts module.

---

## 3. Tooling and commands

- **Python**: 3.13+ pinned in `.python-version`
- **Dependency + venv manager**: [uv](https://docs.astral.sh/uv/)
- **Linter + formatter**: ruff (via pre-commit)
- **Test runner**: pytest

```bash
uv sync                              # install deps into .venv
uv run pytest -q                     # unit tests (mocked, offline)
uv run pre-commit run --all-files    # format + lint everything
uv run python scripts/run_pipeline_demo.py            # default sample-01
uv run python scripts/run_pipeline_demo.py sample-02-vague-ambiguous.md
uv run python scripts/run_pipeline_demo.py sample-03-multi-feature.md
# Samples 04-07 are adversarial (injection / data-extraction / human-imperative)
# and are exercised by evals/run_evals.py rather than the interactive demo.
pnpm install && pnpm --filter ui-react build           # build the React SPA once (served at / by run_api.py)
uv run python scripts/run_api.py                       # FastAPI + React UI at http://127.0.0.1:8000/?token=…
```

**Frontend tooling is pnpm** (a workspace at the repo root, `pnpm-workspace.yaml`). `ui-react/` is the React SPA; the `e2e/` Playwright project joins the workspace under Epic 7. `run_api.py` serves the pre-built `ui-react/dist/` at `/`; if it's missing, `GET /` returns a 500 with the build hint - run `pnpm --filter ui-react build` first.

The demo requires `GOOGLE_API_KEY` in `.env` (see `.env.example`). LangSmith tracing is optional - set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY=lsv2_pt_…` + `LANGSMITH_PROJECT=rtia` to enable.

**Pre-commit secret scanner (`detect-secrets`, #126):** committed alongside `.pre-commit-config.yaml`. Runs on every commit and in CI. New high-entropy strings (AWS keys, JWTs, private keys, base64 blobs above the default-entropy threshold) fail the hook. Legitimate fixtures live in `.secrets.baseline` - the file lists every flagged string the project already knows about (today: the test fixtures in `agents/_secret_scan.py` and `tests/test_secret_scan.py`). When a new finding is real, redact it; when it's an intentional fixture, refresh the baseline:

```bash
uv run detect-secrets scan > .secrets.baseline   # rebuild from scratch
uv run pre-commit run detect-secrets --all-files # verify the hook is clean
```

This is the *commit-time* layer; the runtime layer (`agents/_secret_scan.py`, #124) catches secrets pasted into requirements at invocation time. Both are needed - they cover different threat surfaces.

**API token (`RTIA_API_TOKEN`):** the `run_api.py` entrypoint mints a fresh URL-safe bearer token per process unless `RTIA_API_TOKEN` is set in `.env`. The token gates all `/pipeline*` and `/uploads/*` endpoints (`Authorization: Bearer <token>`) and the React UI mount accepts it via `?token=…` so the printed startup URL is one-click. Set `RTIA_API_HOST` / `RTIA_API_PORT` to override the default `127.0.0.1:8000`.

**Multi-story split:** when the Analyst's output has `implied_stories ≥ 2`, the PO checkpoint emits a different interrupt payload (`{"mode": "split", "implied_stories": [...], "critical_ambiguities": [...]}`) and resume value (`{"selected_story_titles": [...], "answers": {...}}`); the conditional edge routes to `split_node` (pure Python, no LLM) which writes `state["split_stories"]` and ends. Terminal status is `ThreadStatus.DONE_SPLIT`. The Story Writer / AC Generator / Test Case Writer / Reviewer are SKIPPED entirely on this path. Single-story requirements (`implied_stories ≤ 1`) still go through the deep flow unchanged.

`agents.graph.picked_implied_story` (single picked story, used by Story Writer's scope-aware prompt block) and `agents.graph.deferred_implied_stories` (everything else, used by the Reviewer's scope-aware DEFERRED STORIES block) survive for the 1-implied-story deep case. Both degenerate cleanly when implied_stories is empty.

**Exporters:** `POST /pipeline/{thread_id}/export` ships the full deep artifact to Jira (REST v3, ADF codeBlock body) or GitHub (Issues + optional Projects v2 via GraphQL). `POST /pipeline/{thread_id}/export-deferred` batch-creates one lightweight placeholder per deferred/split story. Both backends use `make_exporter("jira" | "github")` in `exporters/base.py`. `dry_run=true` returns the would-be payload - safe without credentials. Credentials: `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN`, `GITHUB_TOKEN`.

**State schema version (`PIPELINE_STATE_VERSION`):** bumped on every change to the persisted state shape. **Clear `~/.rtia/state.db` after a version bump** if any paused threads existed locally - they're not auto-migrated.

**Environment mode (`RTIA_ENV`):** controls the production-tracing guard. Allowed values: `development` (default when unset), `ci`, `production`. When `RTIA_ENV=production` AND `LANGSMITH_TRACING=true`, the demo and any entry point calling `assert_safe_for_env()` refuse to start to prevent requirement text (potentially containing customer PII) from being persisted to LangSmith. See [docs/adr-0008-pii-langsmith.md](docs/adr-0008-pii-langsmith.md).

**LLM provider (`RTIA_LLM_PROVIDER`):** chooses which backend serves the four pipeline agents and the eval judge. Allowed values: `google` (default — paid Gemini Flash via `langchain-google-genai`, per §4.9), `ollama` (local model via a reachable Ollama server; set `OLLAMA_HOST` + an Ollama-pulled `OLLAMA_MODEL`), or `fake` (canned fixtures, zero LLM calls — for graph-level tests; shipped via #312–#316). An invalid value raises rather than silently falling back. The optional `RTIA_OLLAMA_JUDGE=1` flips just the eval judge to Ollama while leaving the pipeline on the primary provider (#243 / PR #244).

**CI gates:** the live regression eval gate is currently **disabled on CI** (`if: false` in `.github/workflows/ci.yml`, shipped via #348 after the brief #340 re-enable proved runner-pool 5xx weather wins). CI gates only on the free deterministic `quality` job (lint + ~570 mocked tests). Live eval is a manual/local step: `uv run python evals/run_evals.py --no-cache`. Full context, the why, and the flip-switch contract live in [docs/ci-and-testing.md](docs/ci-and-testing.md).

---

## 4. Hard rules (non-negotiable)

These are how every change ships in this repo. Follow them or the PR doesn't merge.

### 4.1 Verify behavior end-to-end before commit

Unit tests with mocks validate the *contract*. They do not validate behavior. Before any commit:

1. `uv run pytest -q` - necessary, not sufficient
2. `uv run pre-commit run --all-files` - necessary, not sufficient
3. **Live exercise** of the change as a user would invoke it

For agent or prompt changes specifically: **run the live demo on the 3 baseline samples** (`sample-01`, `sample-02`, `sample-03`) and eyeball the output for the expected behavior shift, *and* run `evals/run_evals.py` locally so the 4 adversarial samples (`sample-04`..`sample-07`) get exercised too. Mocked tests cannot detect prompt-level regressions, and a single sample is not the same shape as the other six.

For external integrations (LangSmith, durable checkpointer, GH Actions): trigger the integration with real credentials and confirm the external system shows the expected effect.

If you don't have the API key needed for end-to-end verification: open the PR as **Draft**, run all local verifications, explicitly hand off the live verification step as a blocking item in the PR description. Never commit-and-defer-verification silently.

### 4.2 Branching

- **Cut every new branch from latest `origin/main`** - `git fetch origin main && git checkout -b feat/<name> origin/main`.
- **Never use `claude/*` worktree branches** for the actual work. Worktrees auto-create those; we cut a real `feat/<name>` or `fix/<name>` branch on top of them.
- Branch naming: `feat/<description>`, `fix/<description>`, `chore/<description>`, `docs/<description>`.

### 4.3 PR-only workflow

- **Never commit or push directly to `main`.** All changes go through PRs.
- **Squash-merge** PRs (not merge commits, not rebase-merge).
- One logical change per PR. If a PR grows multiple distinct concerns, split it.

### 4.4 Every PR links an issue

- Before opening a PR: find an existing GitHub issue (US-01..US-16 user stories or numbered issues) that the PR fulfills, or **create a new issue** describing the work.
- Add `Closes #N` to the PR body (so merge auto-closes the issue).
- Add the issue to the maintainer's **GitHub Project #5** and set status:
  - **Backlog** → no work scheduled
  - **In Progress** → branch cut, work underway
  - **In Review** → PR opened
  - **Done** → PR merged (auto-set by native project workflow)

Native project workflows are enabled: "Item closed → Status: Done" and "Pull request merged → Status: Done". The `Closes #N` link triggers issue auto-close on merge, which fires the Done workflow.

Project #5's status field ID and per-status option IDs live in the maintainer's local notes. Look them up with `gh project field-list 5 --owner <maintainer> --format json`.

### 4.5 Verify facts before recommending

Never recommend APIs, libraries, model parameters, or CLI commands from training-data memory alone. Always verify against:
- Currently installed package (`uv pip list`, then read source if needed)
- Live docs
- The repo's own code

For any API method, library behaviour, or CLI flag named in a response: include a one-line proof inline (a paste from `--help`, the package source, or a live doc URL), or explicitly label the claim as unverified. No naked claims. Confidently wrong is worse than uncertainly right.

### 4.6 Don't impose architecture

Follow this project's existing conventions. Do NOT introduce:
- Service layers, repository patterns, dependency injection frameworks, or other enterprise patterns the codebase doesn't already use.
- Abstractions that don't have at least 2 concrete consumers today.

When in doubt, surface the choice to the user. Don't quietly pick.

### 4.7 Mock per import-site, not per class

When tests need different LLM responses per agent, and multiple agents import the same class (e.g. `ChatGoogleGenerativeAI`):

```python
# WRONG - bleeds across modules because the class is the same object everywhere
patch("agents.requirements_analyst.ChatGoogleGenerativeAI.invoke", return_value=...)

# RIGHT - patches each module's import symbol independently
patch("agents.requirements_analyst.ChatGoogleGenerativeAI", side_effect=_factory(payload_a))
patch("agents.user_story_writer.ChatGoogleGenerativeAI", side_effect=_factory(payload_b))
```

See `tests/test_graph.py` for the working pattern.

### 4.8 Self-review before pushing

Before committing, audit your change for:
- **Correctness**: does the code do what the prompt/test claims?
- **Cleanliness**: any dead code, debug prints, commented-out blocks?
- **Consistency**: does it match the patterns already used in adjacent files?
- **No surprise side effects**: are you touching files outside the stated scope without callout?

For anything publicly visible (PR descriptions, commit messages, docs, blog content): also ask "does this read well to a stranger following this project?" — the repo is public and people read it cold.

This complements 4.6 - review for these, not for "did I follow clean-architecture textbook patterns."

### 4.9 Default model is paid Gemini Flash; other paid models require justification

`agents.config.DEFAULT_MODEL = "gemini-3.5-flash"`. All four production agents and the eval-suite judge run on Gemini 3.5 Flash via `langchain-google-genai`. The default tier is **paid** Google AI Studio (a few cents per session) - the free tier exists but is capped at 20 requests per day per project per model and is too tight for routine demos + evals (see [ADR-0006](docs/adr-0006-provider-switch.md) for the original Anthropic-to-Gemini cost analysis, and [ADR-0007](docs/adr-0007-gemini-3-5-flash-switch.md) for the 2.5-flash → 3.5-flash switch driven by 503s on GitHub-hosted runners).

Cost expectations under the default stack:
- Full pipeline demo: ≈$0.005
- Full eval run (all 7 samples, judge included): a few cents
- ~10× cheaper than the prior Claude Opus 4.7 baseline (≈$0.30–0.50 per demo, ≈$1–2 per eval).

Adding any *other* paid LLM call (Anthropic Claude, OpenAI, larger Gemini Pro) requires:
1. A named reason `gemini-3.5-flash` cannot do the task - usually a specific benchmarked failure mode.
2. PR-body justification of why the cost is worth paying.
3. Explicit user cost approval per `feedback_cost_approval`.

This rule exists because the workshop's cost target is "as close to $0 as quality allows" - not strict $0. The Gemini cutover proved RTIA's quality is fine on Flash; the paid tier removes the 20 RPD ceiling without meaningfully changing the cost target.

### 4.10 Close the PR loop yourself

After opening a PR, don't park it waiting for the user to click merge. The flow is: wait for CI green → self-review the diff with critical eyes (treat it like someone else wrote it) → squash-merge with `--delete-branch`. Only stop short of merging when CI is red, the diff actually warrants discussion, or the user has explicitly asked to hold.

From inside a git worktree, `gh pr merge --squash --delete-branch` may fail with `'main' is already used by worktree at <path>` because the CLI tries to update the local main checkout. Fall back to the API call: `gh api -X PUT repos/<owner>/<repo>/pulls/<n>/merge -f merge_method=squash`. The remote branch is usually auto-deleted by the repo's "delete branch on merge" setting.

This pairs with 4.8. Self-review at commit time catches what you wrote; this self-review (post-CI, pre-merge) catches what the diff *as a whole* looks like to a reader.

### 4.11 Comments explain WHY, not WHAT

The code already shows what it does. Comments fill the intent gap a future reader can't infer from the code itself - trade-offs, non-obvious constraints, "we tried X first and it broke because Y." If a comment restates what the code does, delete it.

Specifically avoid:
- Ceremonial comments (`# constructor`, `# helper function`, `// loop through items`)
- Restating type signatures the code already has
- `TODO` without a linked issue

Specifically keep:
- Why this approach was picked over the obvious alternative
- External constraint refs (link to ADR, issue, or upstream bug)
- Non-obvious failure modes the code defends against

This complements 4.6 (don't over-engineer) and 4.8 (self-review) - same family. Readers shouldn't have to dig in the author's head to understand the WHY.

---

## 5. Final-artifact contract (drives every agent)

When designing or modifying any agent, start from: **which section of the final artifact does this populate?** Not "what does this agent emit?"

| Section | Source agent | Status |
|---|---|---|
| Description | Story Writer | Live |
| Objective | Story Writer | Live |
| Acceptance Criteria | AC Generator | Live |
| Test Cases | Test Case Agent | Live |
| Review notes (optional) | Reviewer Agent | Live |

`agents/user_story_writer.py` already produces `(description, objective, assumptions)` aligned with this contract. Future agents extend `FinalUserStory` with their sections.

---

## 6. Where the roadmap lives

The original 16-phase road-to-production plan is **complete** (v1.1.0 shipped). Ongoing work is now tracked as Epics + Issues on **GitHub Project #5**, not in a local plan file. The project's learning focus remains: **testing AI applications + integrating AI into QA processes**.

For any new piece of work: find the relevant Epic on Project #5, or create a new Epic + child Issues, before opening a PR. If priority or dependency is unclear, the Project board is the source of truth.

---

## 7. Things that have bitten us (read these before they bite again)

- **Gemini's caching API ≠ Anthropic's `cache_control`** - Gemini uses a separate `client.caches.create()` call referenced via the `cached_content` kwarg. Anthropic-style inline `cache_control: ephemeral` blocks do not exist on Gemini and were removed in the ADR-0006 cutover.
- **RTIA has its own LLM response cache** - keyed on `sha256(model_id + prompt_hash + canonicalised messages)` per `agents/_llm_utils.py:_make_cache_key`, used for local eval iteration so a re-run on unchanged inputs is free. **Disabled in the CI regression job** via both `RTIA_LLM_CACHE=disabled` *and* `--no-cache` (belt-and-suspenders so removing one still leaves the other in place). Any change to model id, prompt, or message content auto-invalidates the cache. See [ADR-0013](docs/adr-0013-llm-response-cache.md) for the false-green CI trap this prevents.
- **Gemini's max-tokens kwarg is `max_output_tokens`** - not `max_tokens`. Pre-cutover agent code used `max_tokens`; renamed across the pipeline in the ADR-0006 cutover.
- **Gemini's LangChain wrapper validates `GOOGLE_API_KEY` at construction time** - Anthropic's defers to `invoke`. Tests need a placeholder key via `tests/conftest.py`'s autouse fixture, or `ChatGoogleGenerativeAI(...)` raises `pydantic.ValidationError` before any mock can intercept.
- **Gemini sometimes wraps JSON output in ` ```json ` fences** despite a "no fences" instruction. `agents/_llm_utils.py:strip_json_fence()` trims them defensively. Apply to every Gemini agent.
- **Single-sample testing is overfitting** - always test prompt changes on all 7 sample types (3 baseline: well-structured, vague, multi-feature; 4 adversarial: injection-suffix, injection-inline, data-extraction, transcript-human-imperatives). The behavior on one sample is *not* the behavior on others.
- **Worked examples beat prose rules** - when the model isn't following a prompt rule, add a concrete worked example with correct output. Far stronger than rule iteration.
- **Worktrees can quietly switch** - Bash `cd` doesn't persist between tool calls in some Claude Code environments. Use absolute paths and `pwd && git status` at start of multi-step blocks.
- **msgpack deserialization warning** - `AnalystOutput` etc. need to be registered for checkpointing. The current msgpack allowlist in `agents/graph.py:_allowlisted_serde()` handles this; until anything new lands, the warning is benign noise.
- **`gemini-3.5-flash` is an alias** - not pinned to a date. When Google publishes dated suffixes for the 3.5 line, bump `DEFAULT_MODEL` for reproducibility. Same caveat applied to `gemini-2.5-flash` before the ADR-0007 switch.
- **Gemini 503s are backend-pool specific, not global.** A Gemini model alias that 503s on GitHub-hosted runners can simultaneously respond fine from a maintainer laptop - Google routes runner IP ranges to a specific backend pool. When a 503 storm hits, probe sibling models live (`client.models.list()` + a 1-token `invoke`) before assuming Google is globally down. See ADR-0007 §"What we proved with live probing".

---

## 8. LEARNINGS.md is a continuous consideration

`LEARNINGS.md` at the repo root (gitignored) is the maintainer's personal learning log. RTIA is a workshop - the codebase is the artifact, but **the learning is the deliverable**.

**Always consider** whether the work in progress has produced a durable lesson worth appending. Do not wait for session end. Trigger moments:

- After verifying a non-obvious choice worked
- After resolving a confusing-then-clarified moment ("I thought X, then learned Y")
- After surfacing a hidden constraint or quirk
- After a meta-decision that explains *why* code looks the way it does
- After deciding NOT to do something for a reason worth remembering

Append, don't replace. Lead with the lesson, not the task that produced it. One short paragraph or 2-3 bullets per entry. Specific over vague. Include enough context that the lesson reads cold.

---

## 9. Operational quick-reference

```bash
# Create issue + link to project board + set status
gh issue create --title "..." --body "..." --label "agent"
gh project item-add <project-number> --owner <maintainer> --url <issue-url>
gh project item-edit --id <item-id> --project-id <project-id> \
  --field-id <status-field-id> --single-select-option-id <in-progress-option-id>

# Live demo with a specific sample
uv run python scripts/run_pipeline_demo.py sample-02-vague-ambiguous.md

# Open PR linked to an issue
gh pr create --title "..." --body "Closes #N…"

# Verify trace appeared after a LangSmith-traced run
# (no CLI - open https://smith.langchain.com → project 'rtia' → most recent run)
```

If anything in this file is wrong or stale, **update it in the same PR that exposes the staleness**. Don't leave the next session to discover it.
