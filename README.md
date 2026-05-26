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
GOOGLE_API_KEY=...               # Google AI Studio key — RTIA defaults to Gemini 3.5 Flash
```

Optional but recommended — **LangSmith tracing** (every LLM call surfaces with token counts, latency, and full input/output in the LangSmith UI):

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv_...
LANGSMITH_PROJECT=rtia
```

Tracing is purely opt-in. Production-trace safety (refuses to start under `RTIA_ENV=production` + tracing) is covered by [ADR-0008](docs/adr-0008-pii-langsmith.md).

### Deeper topics — pointers, not duplication

The README intentionally stops at "what you need to get running." For each topic below, the linked doc has the design rationale + the env-var contract + the trade-offs:

- **LLM response cache** — disk-backed, prompt-hash keyed, 24h TTL, disabled in CI. Env vars: `RTIA_LLM_CACHE`, `RTIA_LLM_CACHE_TTL`, `RTIA_LLM_CACHE_DIR`. See [ADR-0013](docs/adr-0013-llm-response-cache.md).
- **Stochastic AC validation (N-runs)** — `--n-runs N` on the eval runner, pass-rate gating, nightly cron at 02:00 UTC on adversarial samples. See [ADR-0014](docs/adr-0014-stochastic-ac-validation.md) and [USAGE §9](docs/USAGE.md#9-stochastic-ac-validation-for-adversarial-samples).
- **Full-local mode ($0 API spend)** — set `RTIA_LLM_PROVIDER=ollama` + `RTIA_OLLAMA_JUDGE=1`. Default uses Gemini at ~$0.005/demo, ~$0.03/eval; full local uses Ollama for both generator and judge. See [USAGE §10](docs/USAGE.md#10-running-rtia-with-zero-api-spend-full-local-mode).
- **When tests fire (CI + nightly cron)** — full trigger table, PR timeline, 24h timeline, and a mental-shortcut table at [docs/ci-and-testing.md](docs/ci-and-testing.md).

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
