# CLAUDE.md — RTIA repo context for Claude Code

This file is read on every Claude Code session opening this repo. It compiles project-specific rules and operational knowledge so any session — fresh install, different machine, different developer — works to the same standard.

If you're a fresh Claude Code session reading this for the first time: **read top to bottom before making changes**. The rules below are not suggestions; they're how this project is run.

---

## 1. What RTIA is

A multi-agent AI assistant that turns raw software requirements (feature requests, PRD snippets, meeting notes) into **one backlog-ready user-story artifact** with four sections:

1. **Description** — what the role wants ("As a/an X, I want Y")
2. **Objective** — the value/outcome the role gets
3. **Acceptance Criteria** — Given/When/Then format
4. **Test Cases** — happy path + edge cases + negative paths

The artifact is designed to paste directly into a Jira Epic or stand alone on a GitHub Project backlog. Every agent in the pipeline contributes to one or more sections of this single artifact — there are no standalone outputs.

Pipeline today: `Analyst → PO Checkpoint → Story Writer → END`. Future agents (Story Review Checkpoint, AC Generator, Test Case Agent, Reviewer Agent) attach as additional LangGraph nodes per the road-to-production plan.

---

## 2. Project layout

```
rtia/
├── agents/                # LangGraph agent definitions (Analyst, Story Writer, future…)
├── prompts/               # Prompt templates as Python modules (versioned with code)
├── tests/                 # Mocked unit tests
├── scripts/               # Live demo entry points (run_pipeline_demo.py)
├── evals/                 # Golden datasets + eval runner (Phase 6+)
│   ├── sample-requirements/  # 3 sample inputs (well-structured, vague, multi-feature)
│   ├── EVAL_DATA_SPEC.md     # Contract for ground-truth files
│   └── validate_samples.py   # Sample structural validator
├── .github/workflows/     # CI (lint + format + tests)
├── api/                   # FastAPI (empty placeholder; Phase 14)
├── ui/                    # Frontend (empty placeholder; Phase 14)
└── docs/                  # ADRs + USAGE.md (Phase 16)
```

`agents/` and `prompts/` mirror 1:1 — each agent owns one prompts module.

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
```

The demo requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). LangSmith tracing is optional — set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY=lsv2_pt_…` + `LANGSMITH_PROJECT=rtia` to enable.

---

## 4. Hard rules (non-negotiable)

These are how every change ships in this repo. Follow them or the PR doesn't merge.

### 4.1 Verify behavior end-to-end before commit

Unit tests with mocks validate the *contract*. They do not validate behavior. Before any commit:

1. `uv run pytest -q` — necessary, not sufficient
2. `uv run pre-commit run --all-files` — necessary, not sufficient
3. **Live exercise** of the change as a user would invoke it

For agent or prompt changes specifically: **run the live demo on all 3 samples** (sample-01, sample-02, sample-03) and eyeball the output for the expected behavior shift. Mocked tests cannot detect prompt-level regressions.

For external integrations (LangSmith, durable checkpointer, GH Actions): trigger the integration with real credentials and confirm the external system shows the expected effect.

If you don't have the API key needed for end-to-end verification: open the PR as **Draft**, run all local verifications, explicitly hand off the live verification step as a blocking item in the PR description. Never commit-and-defer-verification silently.

### 4.2 Branching

- **Cut every new branch from latest `origin/main`** — `git fetch origin main && git checkout -b feat/<name> origin/main`.
- **Never use `claude/*` worktree branches** for the actual work. Worktrees auto-create those; we cut a real `feat/<name>` or `fix/<name>` branch on top of them.
- Branch naming: `feat/<description>`, `fix/<description>`, `chore/<description>`, `docs/<description>`.

### 4.3 PR-only workflow

- **Never commit or push directly to `main`.** All changes go through PRs.
- **Squash-merge** PRs (not merge commits, not rebase-merge).
- One logical change per PR. If a PR grows multiple distinct concerns, split it.

### 4.4 Every PR links an issue

- Before opening a PR: find an existing GitHub issue (US-01..US-16 user stories or numbered issues) that the PR fulfills, or **create a new issue** describing the work.
- Add `Closes #N` to the PR body (so merge auto-closes the issue).
- Add the issue to **Project #5** (https://github.com/users/augustineuzokwe/projects/5/views/1) and set status:
  - **Backlog** → no work scheduled
  - **In Progress** → branch cut, work underway
  - **In Review** → PR opened
  - **Done** → PR merged (auto-set by native project workflow)

Native project workflows are enabled: "Item closed → Status: Done" and "Pull request merged → Status: Done". The `Closes #N` link triggers issue auto-close on merge, which fires the Done workflow.

**Project + status IDs** (verify with `gh project field-list 5 --owner augustineuzokwe --format json` if they drift):

- Project ID: `PVT_kwHOAJNgAc4BX0uG`
- Status field ID: `PVTSSF_lAHOAJNgAc4BX0uGzhS-8g4`
- Status option IDs: Backlog `77c213cc` | In Progress `f5a71b2c` | In Review `b2b2b3d6` | Done `8e946b9e`

### 4.5 Verify facts before recommending

Never recommend APIs, libraries, model parameters, or CLI commands from training-data memory alone. Always verify against:
- Currently installed package (`uv pip list`, then read source if needed)
- Live docs
- The repo's own code

Confidently wrong is worse than uncertainly right. Flag uncertainty inline ("verified against installed v0.8.5", "best-guess — verify before using").

### 4.6 Don't impose architecture

Follow this project's existing conventions. Do NOT introduce:
- Service layers, repository patterns, dependency injection frameworks, or other enterprise patterns the codebase doesn't already use.
- Abstractions that don't have at least 2 concrete consumers today.

When in doubt, surface the choice to the user. Don't quietly pick.

### 4.7 Mock per import-site, not per class

When tests need different LLM responses per agent, and multiple agents import the same class (e.g. `ChatGoogleGenerativeAI`):

```python
# WRONG — bleeds across modules because the class is the same object everywhere
patch("agents.requirements_analyst.ChatGoogleGenerativeAI.invoke", return_value=...)

# RIGHT — patches each module's import symbol independently
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

This complements 4.6 — review for these, not for "did I follow clean-architecture textbook patterns."

### 4.9 Default model is paid Gemini Flash; other paid models require justification

`agents.config.DEFAULT_MODEL = "gemini-2.5-flash"`. All four production agents and the eval-suite judge run on Gemini 2.5 Flash via `langchain-google-genai`. The default tier is **paid** Google AI Studio (a few cents per session) — the free tier exists but is capped at 20 requests per day per project per model and is too tight for routine demos + evals (see [ADR-0006](docs/adr-0006-provider-switch.md) for the cost analysis).

Cost expectations under the default stack:
- Full pipeline demo: ≈$0.005
- Full eval run (3 samples, judge included): ≈$0.03
- ~10× cheaper than the prior Claude Opus 4.7 baseline (≈$0.30–0.50 per demo, ≈$1–2 per eval).

Adding any *other* paid LLM call (Anthropic Claude, OpenAI, larger Gemini Pro) requires:
1. A named reason `gemini-2.5-flash` cannot do the task — usually a specific benchmarked failure mode.
2. PR-body justification of why the cost is worth paying.
3. Explicit user cost approval per `feedback_cost_approval`.

This rule exists because the workshop's cost target is "as close to $0 as quality allows" — not strict $0. The Gemini cutover proved RTIA's quality is fine on Flash; the paid tier removes the 20 RPD ceiling without meaningfully changing the cost target.

---

## 5. Final-artifact contract (drives every agent)

When designing or modifying any agent, start from: **which section of the final artifact does this populate?** Not "what does this agent emit?"

| Section | Source agent | Status |
|---|---|---|
| Description | Story Writer | Live |
| Objective | Story Writer | Live |
| Acceptance Criteria | AC Generator | Phase 8 |
| Test Cases | Test Case Agent | Phase 9 |
| Review notes (optional) | Reviewer Agent | Phase 10 |

`agents/user_story_writer.py` already produces `(description, objective, assumptions)` aligned with this contract. Future agents extend `FinalUserStory` (introduced in Phase 3) with their sections.

---

## 6. Where the roadmap lives

The 16-phase road-to-production plan is in `/Users/auzokwe/.claude/plans/prancy-floating-tarjan.md`. If that path doesn't resolve in your environment, ask the user for the current plan location.

The plan is ordered for the user's learning focus: **testing AI applications + integrating AI into QA processes**. Phases 4-6 (golden dataset → DeepEval suite → CI eval gate) come before remaining agent work so each new agent is built on calibrated test foundations, not retrofitted.

Don't start work on a phase without first reading the relevant section of the plan. If a question of priority or dependency arises mid-phase, the plan is the source of truth.

---

## 7. Things that have bitten us (read these before they bite again)

- **Gemini's caching API ≠ Anthropic's `cache_control`** — Gemini uses a separate `client.caches.create()` call referenced via the `cached_content` kwarg. Anthropic-style inline `cache_control: ephemeral` blocks do not exist on Gemini and were removed in the ADR-0006 cutover. We currently use no caching (prompts are small enough; free tier removes cost driver).
- **Gemini's max-tokens kwarg is `max_output_tokens`** — not `max_tokens`. Pre-cutover agent code used `max_tokens`; renamed across the pipeline in the ADR-0006 cutover.
- **Gemini's LangChain wrapper validates `GOOGLE_API_KEY` at construction time** — Anthropic's defers to `invoke`. Tests need a placeholder key via `tests/conftest.py`'s autouse fixture, or `ChatGoogleGenerativeAI(...)` raises `pydantic.ValidationError` before any mock can intercept.
- **Gemini sometimes wraps JSON output in ` ```json ` fences** despite a "no fences" instruction. `agents/_llm_utils.py:strip_json_fence()` trims them defensively. Apply to every Gemini agent.
- **Single-sample testing is overfitting** — always test prompt changes on all 3 sample types (well-structured, vague, multi-feature). The behavior on one sample is *not* the behavior on others.
- **Worked examples beat prose rules** — when the model isn't following a prompt rule, add a concrete worked example with correct output. Far stronger than rule iteration.
- **Worktrees can quietly switch** — Bash `cd` doesn't persist between tool calls in some Claude Code environments. Use absolute paths and `pwd && git status` at start of multi-step blocks.
- **msgpack deserialization warning** — `AnalystOutput` etc. need to be registered for checkpointing. Phase 2.2 fixes; until then, the warning is benign noise.
- **`gemini-2.5-flash` is an alias** — not pinned to a date. When Google publishes dated suffixes for the 2.5 line, bump `DEFAULT_MODEL` for reproducibility.

---

## 8. LEARNINGS.md is a continuous consideration

`LEARNINGS.md` at the repo root (gitignored) is the maintainer's personal learning log. RTIA is a workshop — the codebase is the artifact, but **the learning is the deliverable**.

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
gh project item-add 5 --owner augustineuzokwe --url <issue-url>
gh project item-edit --id <item-id> --project-id PVT_kwHOAJNgAc4BX0uG \
  --field-id PVTSSF_lAHOAJNgAc4BX0uGzhS-8g4 --single-select-option-id f5a71b2c

# Live demo with a specific sample
uv run python scripts/run_pipeline_demo.py sample-02-vague-ambiguous.md

# Open PR linked to an issue
gh pr create --title "..." --body "Closes #N…"

# Verify trace appeared after a LangSmith-traced run
# (no CLI — open https://smith.langchain.com → project 'rtia' → most recent run)
```

If anything in this file is wrong or stale, **update it in the same PR that exposes the staleness**. Don't leave the next session to discover it.
