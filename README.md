# RTIA — Requirements & Test Intelligence Assistant

A multi-agent AI assistant that takes raw software requirements — feature requests, business requirements, PRD snippets, or meeting notes — and produces a structured user story, acceptance criteria (Given/When/Then), and test cases through a supervised pipeline.

A human review checkpoint sits between story generation and AC generation, keeping a PO or QA Lead in control of quality before downstream output is produced.

## How It Works

```
Requirements input (free text or uploaded PDF/markdown)
      │
      ▼
Requirements Analyst Agent  →  extracts intent, flags ambiguities
      │
      ▼
User Story Writer Agent     →  "As a [role], I want [feature], so that [benefit]"
      │
      ▼
⏸ HUMAN CHECKPOINT          →  PO/QA reviews and edits the story
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

## Use Case

A PO or BA has raw requirements. Instead of manually writing user stories, ACs, and test cases from scratch, they paste the requirements into RTIA. The system generates a first draft at each stage. A QA Lead reviews, edits, and accepts at the human checkpoint before the pipeline continues.

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
