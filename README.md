# RTIA — Requirements & Test Intelligence Assistant

A multi-agent AI assistant that takes raw software requirements — feature requests, business requirements, PRD snippets, or meeting notes — and produces a structured user story, acceptance criteria (Given/When/Then), and test cases through a supervised pipeline.

Two human-in-the-loop checkpoints keep a PO or QA Lead in control: one *before* story generation (to resolve critical ambiguities) and one *after* (to review the generated story before AC generation).

## How It Works

```
Requirements input (free text or uploaded PDF/markdown)
      │
      ▼
Requirements Analyst Agent  →  extracts intent, actors, and ambiguities
                            →  each ambiguity tagged "critical" or "normal"
      │
      ▼
⏸ PO CHECKPOINT             →  pauses ONLY if critical ambiguities exist
                            →  PO answers critical questions; normal ones
                            →  flow forward as story assumptions
      │
      ▼
User Story Writer Agent     →  "As a [role], I want [feature], so that [benefit]"
                            →  uses intent + actors + PO answers; records
                            →  defaults picked for normal ambiguities as
                            →  story assumptions for the next checkpoint
      │
      ▼
⏸ STORY REVIEW CHECKPOINT  →  PO/QA reviews and edits the generated story
      │
      ▼
AC Generator Agent          →  Given/When/Then acceptance criteria
      │
      ▼
Test Case Agent             →  test cases (happy path + edge cases)
      │
      ▼
Reviewer Agent              →  coverage gaps, weak ACs, untestable criteria
      │
      ▼
Structured output           →  JSON / markdown export
```

**Why two checkpoints?** They do different work that the other can't:

- The **PO checkpoint** resolves missing information *before* the system makes assumptions. The Analyst classifies each ambiguity by severity so the PO only pauses for genuinely blocking questions, not every detail.
- The **Story Review checkpoint** verifies the *output* — catching cases where the Story Writer's interpretation of the resolved inputs doesn't match what the PO actually meant.

## Use Case

A PO or BA has raw requirements. Instead of manually writing user stories, ACs, and test cases from scratch, they paste the requirements into RTIA. The system generates a first draft at each stage. The PO answers a small number of critical clarifying questions up front and reviews the generated story before the pipeline continues to AC generation.

**Input formats (v1):** Free text · PDF · Markdown
**Input formats (v2):** Jira Epic via API

> **End-user guide:** if you're a PO, BA, or QA lead using RTIA rather than building it, read [docs/USAGE.md](docs/USAGE.md) — it walks you from "I have a requirement" to "I have a backlog-ready artifact" without assuming any developer knowledge.

## Stack

| Layer | Tool |
|---|---|
| Agent orchestration | LangGraph (Python) |
| RAG / LLM abstraction | LangChain (Python) |
| LLM provider | Anthropic Claude |
| Vector store | Chroma |
| LLM evaluation | DeepEval |
| Tracing | LangSmith |
| API | FastAPI |
| UI | Streamlit |
| CI/CD | GitHub Actions |
| Prompt regression | Promptfoo |

## Project Structure

```
rtia/
├── agents/          # LangGraph agent definitions
├── api/             # FastAPI routes
├── ui/              # Streamlit frontend
├── evals/           # DeepEval evaluation datasets and tests
├── prompts/         # Prompt templates
├── tests/           # Unit and integration tests
├── .github/
│   └── workflows/   # GitHub Actions CI/CD
└── docs/            # ADRs and QA adoption roadmap
```

## Getting Started

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency + venv management
- An Anthropic API key ([get one](https://console.anthropic.com/))
- (Optional) A LangSmith API key for observability ([get one](https://smith.langchain.com))

### Setup

```bash
git clone https://github.com/augustineuzokwe/rtia.git
cd rtia
uv sync                          # install deps into a local .venv
cp .env.example .env             # then fill in your keys (see below)
uv run pre-commit install        # one-time: enable the pre-commit hooks
```

### Environment variables

`.env.example` documents every variable. The minimum to run the demo:

```
ANTHROPIC_API_KEY=sk-ant-...
```

To turn on **LangSmith tracing** (recommended — every LLM call is traced, with token counts, latency, and full input/output), add:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv_...
LANGSMITH_PROJECT=rtia
```

Tracing is purely opt-in. Leaving the vars unset (the CI default) runs the pipeline identically with no external calls beyond Anthropic.

**LLM response cache** (see [ADR-0013](docs/adr-0013-llm-response-cache.md) and Issue #230). Local iteration on the same input hits a disk cache by default, so a re-run of `scripts/run_pipeline_demo.py` against the same sample is ~free after the first warm-up. Knobs:

```
RTIA_LLM_CACHE=enabled         # default; set to "disabled" to bypass
RTIA_LLM_CACHE_TTL=86400       # seconds, default 24h (deliberately shorter than Promptfoo's 14d)
RTIA_LLM_CACHE_DIR=~/.rtia/cache  # default; override only when sharing across worktrees
```

The cache key includes the prompt hash, so a prompt edit auto-invalidates — you cannot accidentally measure stale prompt behaviour. The CI regression job and `scripts/run_integration_smoke.py` disable the cache by default; pass `--no-cache` to `evals/run_evals.py` locally when re-baselining or running adversarial regressions.

**Stochastic AC validation** (Issue #233, [ADR-0014](docs/adr-0014-stochastic-ac-validation.md)). Adversarial samples (`04–07`) test the *tail* of the model's distribution — single-pass measurement misses the rare-but-real failure that motivates the sample existing. Run them stochastically:

```bash
uv run python evals/run_evals.py sample-04 --n-runs 10 --no-cache
```

The N-run gate measures pass-rate per metric (fraction of runs at-or-above each metric's floor in `evals/thresholds.yaml`) against an adjustable threshold (default 95 % for adversarial samples, 100 % for non-adversarial). N > 1 forces `RTIA_LLM_CACHE=disabled` automatically — otherwise the N draws collapse to 1 cached measurement and the gate is dishonest.

The `nightly-safety-regression` workflow (`.github/workflows/nightly-safety-regression.yml`) runs N=10 on samples 04–07 every night at 02:00 UTC and gates the build on the pass-rate threshold; the per-PR regression job stays cheap at N=1.

**Cost tiers** — RTIA defaults to Gemini 3.5 Flash because the cost is already near-free (a full pipeline demo runs ~$0.005, a full eval gate ~$0.03), but a strictly **zero-API-spend** path exists for adopters who don't want any external dependency:

| Configuration | Generator | Judge | Cost per eval run | Quality vs default |
|---|---|---|---|---|
| Default (recommended) | Gemini 3.5 Flash | Gemini 3.5 Flash | ~$0.03 | baseline |
| Local generator, hosted judge | Ollama (`llama3.1:8b`) | Gemini 3.5 Flash | ~$0.01-0.02 | -45 % to +5 % per metric — see [ollama-probe-2026-05-26.md](docs/ollama-probe-2026-05-26.md) |
| **Full local** | Ollama (`llama3.1:8b`) | Ollama (`llama3.1:8b`) | **$0** | not directly measured — judge precision likely lower; treat as exploratory |

To opt into the zero-cost path, set **both** switches:

```bash
export RTIA_LLM_PROVIDER=ollama      # routes the 5 production agents to Ollama
export RTIA_OLLAMA_JUDGE=1           # routes the deepeval judge to Ollama too
# Optional: pick stronger local models if your RAM allows
# export RTIA_OLLAMA_MODEL=qwen2.5:14b
# export RTIA_OLLAMA_JUDGE_MODEL=qwen2.5:14b
uv run python evals/run_evals.py sample-01
```

The two switches are deliberately independent so you can mix them: keep Gemini for the judge when you want apples-to-apples eval signal (per the §7.3 methodology); flip both when the question is "can RTIA run end-to-end without external API spend?"

### Run the demo

```bash
uv run python scripts/run_pipeline_demo.py
```

The demo runs the pipeline against `evals/sample-requirements/sample-01-well-structured.md`, pauses for PO input if the Analyst flagged critical ambiguities, and prints the generated user story. If tracing is on, the script prints a link to the LangSmith dashboard at the start of the run.

### Run the API + UI (Phase 14)

```bash
uv run python scripts/run_api.py
```

Starts a FastAPI server on `127.0.0.1:8000` with a Gradio UI mounted at `/`. The startup banner prints a tokenized URL (`http://127.0.0.1:8000/?token=…`) — open it in a browser to paste a requirement or upload a PDF/Markdown file and step through the PO and review checkpoints. All API endpoints require `Authorization: Bearer <token>`; set `RTIA_API_TOKEN` in `.env` to pin a stable token across restarts.

### Run the tests

```bash
uv run pytest -q                 # unit tests (mocked, offline)
uv run pre-commit run --all-files
```

> **Note on AI testing:** the unit tests mock the LLM — they validate prompt assembly, JSON parsing, and pipeline wiring, not the model's behavior. Behavioral evaluation (faithfulness, ambiguity discipline, story quality) is the next track on the roadmap and will live under `evals/` with its own runner.

## Workshop Context

This project is a learning workshop for a QA Lead transitioning into AI-first quality engineering. It is used to:

- Practice agentic AI design (LangGraph multi-agent pipelines)
- Practice prompt engineering (requirements → stories → ACs → tests)
- Build and test an LLM evaluation pipeline (DeepEval + GitHub Actions)
- Document a QA team AI adoption roadmap using this app as the test subject
