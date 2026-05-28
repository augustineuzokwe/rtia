# `error` scenario fixtures

This directory is intentionally empty. The `error` scenario is implemented in
`agents/_fake_llm.py:FakeChatModel.invoke()`: when `RTIA_FAKE_SCENARIO=error`
is set, the analyst's first `.invoke()` raises `RuntimeError` before any
fixture file is read, so the rest of the pipeline never runs and the UI
terminates in the `ERROR` state.

This README exists only so the empty directory is preserved under git. Adding
agent fixtures here would never be loaded.

See [ADR-0015 §"Initial scenarios"](../../../../docs/adr-0015-fake-llm-provider.md).
