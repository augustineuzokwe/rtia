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
                            →  uses original intent + actors + PO answers
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

> Setup instructions will be added as the project is built.

## Workshop Context

This project is a learning workshop for a QA Lead transitioning into AI-first quality engineering. It is used to:

- Practice agentic AI design (LangGraph multi-agent pipelines)
- Practice prompt engineering (requirements → stories → ACs → tests)
- Build and test an LLM evaluation pipeline (DeepEval + GitHub Actions)
- Document a QA team AI adoption roadmap using this app as the test subject
