# RTIA test suite

Python tests are split by **scope**:

```
tests/
├── unit/          one module in isolation (pure fns, or a single agent/util with the LLM mocked)
├── integration/   the FastAPI app, the LangGraph pipeline, or multiple backend components together
├── eval/          the eval-suite machinery (metrics, dataset, gates, budgets, judge, N-runs)
├── conftest.py    shared autouse fixtures (apply to every subfolder)
└── fixtures/      shared fixture data
```

**Where does a new test go?** Touches the FastAPI app or the LangGraph graph or
several components → `integration/`. Tests the eval harness → `eval/`. Otherwise
(one module, mocked) → `unit/`. The browser **E2E** suite is its own top-level
`e2e/` Playwright project, not here.

Run: `uv run pytest -q` (whole suite) or `uv run pytest tests/unit -q` (one scope).

> **Local gotcha:** these mocked tests assume the default LLM provider. If your
> `.env`/shell pins `RTIA_LLM_PROVIDER=ollama`, the agent unit tests make live
> calls the Google-class mocks don't intercept and fail. Run with
> `RTIA_LLM_PROVIDER=google` (as CI does) for a clean local run.

See CLAUDE.md §4.7 for the mock-per-import-site rule.
