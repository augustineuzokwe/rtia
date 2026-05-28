# RTIA test suite - what's where

The test suite is single-flat (everything in `tests/*.py`) by design - keeps
imports simple, no per-category fixture overrides. This README is the
navigation layer: when you're hunting for "the test that covers X," start
here, not by `ls`-ing the directory.

40 test files, ~ 526 passing tests total (1 skipped - `reportlab` optional dep).
Run the whole suite with `uv run pytest -q`; run one category by listing the
files explicitly, e.g. `uv run pytest tests/test_ui_*.py -q`.

## Categories

### Agent contracts (mocked LLM, fast)

Tests each production agent's **JSON output schema + happy-path + rejection
behaviour**. Mocks `ChatGoogleGenerativeAI.invoke` per-module so a stale
mock from one agent can't bleed into another (see CLAUDE.md §4.7). No live
API calls; runs in milliseconds.

| File | Covers |
|---|---|
| [`test_requirements_analyst.py`](test_requirements_analyst.py) | Analyst output parsing, ambiguity severity validation, implied-stories handling |
| [`test_requirements_analyst_prompt.py`](test_requirements_analyst_prompt.py) | Analyst prompt structure + hash stability |
| [`test_user_story_writer.py`](test_user_story_writer.py) | Story Writer output parsing, legacy-schema rejection, scope-aware prompt block |
| [`test_ac_generator.py`](test_ac_generator.py) | AC Generator output, Given/When/Then shape |
| [`test_test_case_writer.py`](test_test_case_writer.py) | Test Case Writer output, type literals, malformed-JSON rejection |
| [`test_reviewer.py`](test_reviewer.py) | Reviewer scope-aware deferred-stories block |
| [`test_final_artifact.py`](test_final_artifact.py) | `FinalUserStory` Pydantic schema |

### Graph / orchestration

| File | Covers |
|---|---|
| [`test_graph.py`](test_graph.py) | LangGraph topology, `PipelineState` schema, split conditional edge, HITL interrupt shapes |

### API layer (FastAPI)

The API is the production entry point alongside the CLI demo. Tests cover
the bearer-token auth, the request/response contracts, and the exporters
bridge.

| File | Covers |
|---|---|
| [`test_api_auth.py`](test_api_auth.py) | `Authorization: Bearer <token>` gate; per-process token mint |
| [`test_api_endpoints.py`](test_api_endpoints.py) | `/pipeline*` and `/uploads/*` endpoint contracts |
| [`test_api_parsers.py`](test_api_parsers.py) | PDF / Markdown / text input parsing |
| [`test_api_runner.py`](test_api_runner.py) | Pipeline runner state transitions through the API |
| [`test_api_runner_title.py`](test_api_runner_title.py) | Title derivation for backlog placeholder stories (#222/#224) |
| [`test_api_export.py`](test_api_export.py) | `POST /pipeline/{id}/export` - full deep artifact to Jira/GitHub |
| [`test_api_export_deferred.py`](test_api_export_deferred.py) | `POST /pipeline/{id}/export-deferred` - split placeholder stories batch |

### UI layer (Gradio)

The UI mounts at `/` on the API server. These tests cover panel visibility
state machines + event dispatch - *not* visual rendering (that's a manual
verification step in `docs/USAGE.md`).

| File | Covers |
|---|---|
| [`test_ui_state_panels.py`](test_ui_state_panels.py) | Which panels show/hide on each `ThreadStatus` transition |
| [`test_ui_followup_dispatch.py`](test_ui_followup_dispatch.py) | Split checkbox group → resume payload routing |
| [`test_ui_export_target.py`](test_ui_export_target.py) | `_build_export_target` for Jira project key / GitHub `owner/repo` |

### Exporters (Jira + GitHub backends)

| File | Covers |
|---|---|
| [`test_exporters.py`](test_exporters.py) | `Exporter` Protocol + `make_exporter()` factory + dry-run payloads |
| [`test_adf_converter.py`](test_adf_converter.py) | Markdown → Atlassian Document Format for native Jira rendering (#223) |

### Eval suite (golden-dataset metrics + budgets)

The eval suite is RTIA's quality gate. These tests cover its **scaffolding**:
metric implementations, dataset loaders, threshold/budget gates. The
**live** eval run (which actually invokes Gemini against the 7 samples) is
not a unit test - it runs from `evals/run_evals.py` and is gated by the CI
`regression` job, not by pytest.

| File | Covers |
|---|---|
| [`test_evals_metrics.py`](test_evals_metrics.py) | Per-metric scoring functions (actor_set, ambiguity, intent_keyword, requirement_fidelity, injection_resistance) |
| [`test_evals_ac_metrics.py`](test_evals_ac_metrics.py) | AC-layer metrics (ac_coverage, ac_testability) |
| [`test_evals_tc_metrics.py`](test_evals_tc_metrics.py) | Test-case-layer metrics (tc_coverage_breadth, tc_executability) |
| [`test_evals_dataset.py`](test_evals_dataset.py) | Golden sample loader; ground-truth file validation |
| [`test_evals_po_directive.py`](test_evals_po_directive.py) | PO-answer fixtures for unattended eval runs |
| [`test_evals_per_agent_duration.py`](test_evals_per_agent_duration.py) | Per-agent telemetry capture (Phase 13.1) |
| [`test_evals_budgets.py`](test_evals_budgets.py) | `check_budgets.py` token + duration gate |
| [`test_eval_gate.py`](test_eval_gate.py) | `check_thresholds.py` per-metric floor gate |
| [`test_n_runs.py`](test_n_runs.py) | Stochastic AC validation: pass-rate aggregation, cache-disable invariant (#233 / ADR-0014) |

### Integration smoke (live LLM, end-to-end)

Different from the eval suite - this is the "the pipeline still strings
together correctly end-to-end" smoke test. Burns real Gemini tokens.

| File | Covers |
|---|---|
| [`test_integration_smoke.py`](test_integration_smoke.py) | 28 end-to-end invariants on the 7 samples; opt-in (not on CI by default) |

### Security

| File | Covers |
|---|---|
| [`test_secret_scan.py`](test_secret_scan.py) | Pre-LLM regex blocker - 8 credential patterns block before any LLM call (#124) |
| [`test_sanitize.py`](test_sanitize.py) | Input sanitisation helpers |

### Cross-cutting infrastructure

| File | Covers |
|---|---|
| [`test_config.py`](test_config.py) | Env-var helpers, `prompt_hash()`, max-output-tokens calibration |
| [`test_llm_utils.py`](test_llm_utils.py) | `coerce_response_text`, `strip_json_fence` (Gemini quirk defence) |
| [`test_llm_cache.py`](test_llm_cache.py) | LLM response cache invariants: prompt-hash invalidation, TTL expiry, disable env (#230 / ADR-0013) |
| [`test_llm_errors.py`](test_llm_errors.py) | `LLMPipelineError` envelope, `wrap_llm_exception` (ADR-0009) |
| [`test_logging.py`](test_logging.py) | `log_agent_invocation` context manager + telemetry record |
| [`test_observability.py`](test_observability.py) | `RTIA_ENV=production` + `LANGSMITH_TRACING=true` refuse-to-start guard (ADR-0008) |
| [`test_ollama_judge_optin.py`](test_ollama_judge_optin.py) | `RTIA_OLLAMA_JUDGE=1` opt-in routing; independence from `RTIA_LLM_PROVIDER` (#243 / PR #244) |

### CI workflow contract

| File | Covers |
|---|---|
| [`test_ci_cache_disable.py`](test_ci_cache_disable.py) | Parses `.github/workflows/ci.yml`; asserts regression job has both `RTIA_LLM_CACHE=disabled` env AND `--no-cache` flag (#230 belt-and-suspenders) |

### Shared scaffolding

| File | Covers |
|---|---|
| [`conftest.py`](conftest.py) | Autouse fixtures: dummy provider API keys (so `ChatGoogleGenerativeAI` construction doesn't fail), `RTIA_LLM_CACHE=disabled` (so mocked tests don't hit a warm cache) |
| [`__init__.py`](__init__.py) | Marks `tests/` as a package - empty by intent |

## Conventions to be aware of

- **No async tests** - RTIA's agent functions are synchronous; the few async surfaces (FastAPI handlers) are tested through their sync entry points.
- **Mock per import-site, not per class** - when two modules import the same LLM class, patch the symbol *at each import site*, not the class itself. See CLAUDE.md §4.7 + the working pattern in [`test_graph.py`](test_graph.py).
- **Tests assume cache disabled** - `conftest.py` sets `RTIA_LLM_CACHE=disabled` autouse. Cache-specific tests (`test_llm_cache.py`) re-enable it explicitly within the test.
- **Eval / regression jobs are not pytest** - they run from `evals/run_evals.py` and gate via `evals/check_thresholds.py` + `evals/check_budgets.py`. The pytest suite covers their *scaffolding*; the live run is the CI `regression` workflow job.
- **`test_integration_smoke.py` is offline** - it imports the smoke script as a module and unit-tests its non-LLM helpers (invariant checks, token-sum logic, budget enforcement). The live smoke run lives in `scripts/run_integration_smoke.py` and is invoked from the nightly integration workflow, not from pytest.

## Common navigation needs

- *Looking for the test that proves an env-var works?* → `test_config.py`, `test_llm_cache.py`, `test_ollama_judge_optin.py`
- *Looking for the test that asserts CI behaviour?* → `test_ci_cache_disable.py`
- *Looking for the test that covers an agent's output schema?* → `test_<agent>.py` under "Agent contracts" above
- *Looking for the metric / floor / budget logic?* → `test_evals_*.py` and `test_eval_gate.py` / `test_evals_budgets.py`
- *Looking for the UI panel state?* → `test_ui_state_panels.py`
- *Looking for the exporter payload shape?* → `test_exporters.py` (Jira/GitHub) + `test_adf_converter.py` (Jira ADF)
