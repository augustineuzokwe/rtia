# Changelog

All notable changes to RTIA are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - Unreleased

First public release. Everything below is what `main` ships today.

### Pipeline

- **Multi-agent LangGraph orchestration** with six agents: Requirements Analyst, User Story Writer, AC Generator, Test Case Writer, Composer, Reviewer.
- **Two human-in-the-loop checkpoints**: PO checkpoint (resolves critical ambiguities, can reshape into a split-path interrupt for multi-story input) and Story Review checkpoint (PO/QA edits the generated story before AC generation).
- **Split path for multi-story requirements**: a conditional LangGraph edge routes `implied_stories >= 2` to a non-LLM `split_node` that emits placeholder stories the PO can backlog and deep-dive individually later. Documented in [ADR-0010](docs/adr-0010-multi-story-split.md).
- **`FinalUserStory` artifact contract**: every agent contributes to one composed four-section output (Description / Objective / Acceptance Criteria / Test Cases) plus optional Reviewer notes. See [ADR-0004](docs/adr-0004-final-artifact.md).

### Evaluation + CI quality gates

- **DeepEval suite** with six per-sample metrics: intent_keyword_overlap, actor_alignment, ambiguity_coverage, ac_coverage, tc_coverage_breadth, tc_executability. Calibrated thresholds enforced by `evals/check_thresholds.py`.
- **CI eval gate** on every PR touching `agents/` / `prompts/` / `evals/`: live Gemini Flash run against three golden samples plus per-step cost + latency budgets enforced by `evals/check_budgets.py`. Skipped for doc-only and dependency-bump PRs (path filter + Dependabot carve-out).
- **Stochastic AC validation** via `--n-runs N` on the eval runner with pass-rate gating. Nightly safety regression at 02:00 UTC runs `N=10` on the four adversarial samples (`evals/sample-requirements/sample-04..07`) and posts results to a regression report. See [ADR-0014](docs/adr-0014-stochastic-ac-validation.md).
- **Nightly integration smoke** runs the live pipeline end-to-end against `sample-01` and enforces a token-usage budget. Manual-trigger only by default; off the PR critical path.
- **Three sample tiers**: `sample-01` (well-structured), `sample-02` (vague/ambiguous), `sample-03` (multi-feature → split path), `sample-04..07` (prompt-injection adversarial set).

### Operational discipline

- **Two-layer secret scanning**: pre-commit `detect-secrets` blocks new high-entropy strings at commit time; runtime `agents/_secret_scan.py` blocks secrets pasted into requirement inputs before any LLM call.
- **Production-tracing guard**: refuses to start with `RTIA_ENV=production` + `LANGSMITH_TRACING=true` so requirement text never leaks to LangSmith. See [ADR-0008](docs/adr-0008-pii-langsmith.md).
- **Durable LangGraph checkpointing**: SQLite saver at `~/.rtia/state.db`. Schema-versioned via `PIPELINE_STATE_VERSION` (currently 3). Paused threads survive process restarts.
- **Structured LLM-error handling**: failures surface a `LLMFailureDetail` (agent, error class, HTTP status, retries) and produce a rendered stub artifact instead of silent fallback. Forbidden pattern documented in [GUARDRAILS.md](GUARDRAILS.md).
- **LLM response cache** with 24h TTL, prompt-hash keyed (cache invalidates automatically on prompt edits), `~/.rtia/llm_cache.json`. Forcibly disabled in CI regression jobs to prevent false-green PRs from cached old responses. See [ADR-0013](docs/adr-0013-llm-response-cache.md).
- **Cost discipline**: paid Gemini Flash default with calibrated per-PR cost (≈$0.03/eval run, ≈$0.005/demo). Optional full-local Ollama path (`RTIA_LLM_PROVIDER=ollama` + `RTIA_OLLAMA_JUDGE=1`) for $0 API spend.

### Surfaces

- **REST API** (FastAPI) with bearer-token auth at `/pipeline/*` and `/uploads/*`. Token printed in startup banner or pinned via `RTIA_API_TOKEN`.
- **Gradio UI** mounted at `/`. PO inputs + Story Review edits + split row checkboxes + deferred-stories export all wired.
- **Backlog exporters**: Jira (REST v3, native ADF body) and GitHub Issues (REST + optional Projects v2 via GraphQL). Both support "update existing issue" so re-running RTIA on a placeholder title collapses the deep artifact onto the placeholder in place rather than creating a duplicate.
- **CLI demo** at `scripts/run_pipeline_demo.py` exercises any sample (or arbitrary local markdown file) end-to-end.

### Documentation

- 14 ADRs covering provider switch, durable state, LLM resilience, final artifact contract, multi-story split, PII handling, LLM fallback, single-user-local scope, response cache, stochastic AC validation.
- [USAGE.md](docs/USAGE.md) for the PO/QA workflow.
- [docs/ci-and-testing.md](docs/ci-and-testing.md) for the testing pyramid + trigger map.
- [docs/glossary.md](docs/glossary.md) for vocabulary reference (RTIA, agentic, HITL, schema versioning, etc.).
- [docs/UI_CONTRACT.md](docs/UI_CONTRACT.md) for the UI state machine + panel-visibility rules.
- [tests/README.md](tests/README.md) categorising the 40 test files.

### Provider history

- v0.x ran on Anthropic Claude (Opus 4.7 / Sonnet 4.6).
- Switched to Google Gemini 2.5 Flash via `langchain-google-genai` ([ADR-0006](docs/adr-0006-provider-switch.md)) for cost.
- Switched to Gemini 3.5 Flash ([ADR-0007](docs/adr-0007-gemini-3-5-flash-switch.md)) after 503s on GitHub-hosted runners.

[1.0.0]: https://github.com/augustineuzokwe/rtia/releases/tag/v1.0.0
