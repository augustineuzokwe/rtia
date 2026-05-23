# UI Contract — `ui/gradio_app.py`

**Audience:** anyone modifying the Gradio UI or adding a new
`ThreadStatus`. Read [docs/USAGE.md](USAGE.md) first if you're an
end-user; this file is the developer-facing rulebook.

**Why this exists:** between issues #170, #176, and #178 the UI took
three reactive patches in a week, each one revealing the next bug.
Issue #186 stopped the bleeding by writing down the state contract
([the audit](ui_audit_2026-05-24.md)) and PR #188 fixed the structural
drift the audit found. This file is the durable spec that replaces the
point-in-time audit; if a rule below is wrong, fix it in the same PR
that exposes the staleness.

---

## 1. The state machine

The single source of truth for pipeline status is
[`ThreadStatus`](../api/models.py). The UI never invents a status.

```
                                                            ┌────────────┐
                                                       ┌──> │   DONE     │  deep flow terminal
   ┌─────────┐    ┌─────────────┐    ┌──────────────┐ │    └────────────┘
   │ RUNNING │ ──>│ PAUSED_PO   │ ──>│ PAUSED_REVIEW│─┤
   └─────────┘    └─────────────┘    └──────────────┘ │    ┌────────────┐
        │              │                              └──> │  ERROR     │  any stage can fail
        │              ▼                                   └────────────┘
        │        ┌──────────────┐
        │        │ DONE_FANOUT  │  fan-out terminal (multi-story branch)
        │        └──────────────┘
        ▼
   ┌────────┐
   │ ERROR  │
   └────────┘
```

| Value | Trigger | Terminal? |
|---|---|---|
| `RUNNING` | A node is executing. Transient — never displayed long. | No |
| `PAUSED_PO` | Pipeline hit the PO checkpoint. Payload carries `critical_ambiguities` (deep) or `implied_stories` (fan-out). | No |
| `PAUSED_REVIEW` | Pipeline hit the story-review checkpoint (deep flow only). | No |
| `DONE` | Deep flow completed. Payload carries `rendered_artifact` + optional `deferred_stories`. | Yes |
| `DONE_FANOUT` | Fan-out flow completed. Payload carries `fan_out_stories`. | Yes |
| `ERROR` | A node raised. Payload may carry `rendered_artifact` (the error message). | Yes |

There is **no `IDLE` status.** The "no pipeline run yet" view is
rendered by `on_run` returning a hand-crafted tuple, not by routing
through `_state_to_panels`. See §4.3.

---

## 2. The panel × state matrix

This table is the contract. Code in
[`_state_to_panels`](../ui/gradio_app.py) must produce it; tests in
[`tests/test_ui_state_panels.py`](../tests/test_ui_state_panels.py)
pin it.

| Panel / control | (idle) | `RUNNING` | `PAUSED_PO` | `PAUSED_REVIEW` | `DONE` | `DONE_FANOUT` | `ERROR` |
|---|---|---|---|---|---|---|---|
| `input_panel` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `run_btn` interactive | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| `po_panel` | — | — | ✅ | — | — | — | — |
| `review_panel` | — | — | — | ✅ | — | — | — |
| `result_panel` | — | — | — | — | ✅ | ✅ | — |
| `backlog_target_panel` | — | — | — | — | ✅ | ✅ | — |
| `deep_export_panel` | — | — | — | — | ✅ | — | — |
| `deferred_panel` | — | — | — | — | ✅¹ | ✅¹ | — |
| `error_panel` | — | — | — | — | — | — | ✅ |

¹ Visible only when the matching story list (`deferred_stories` or
`fan_out_stories`) is non-empty.

### Invariants

- **`error_panel` and `result_panel` are siblings, not parent/child.**
  ERROR rendering through `result_panel` was the bug #186 fixed. Don't
  nest them.
- **`backlog_target_panel` is independent of `deep_export_panel`.**
  The shared form (backend / target / extra / dry-run) feeds both the
  deep export button AND the fan-out follow-up button, so its
  visibility is its own flag (`backlog_visible`).
- **`run_btn` is disabled — not hidden — when a thread is active.**
  Hiding would leave the input panel looking incomplete; disabling
  communicates "you can't act here until the pipeline finishes."

---

## 3. The `_state_to_panels` / `_SPREAD_KEYS` / `outputs` triplet

This is the single most-load-bearing pattern in the file. Skim
[`ui/gradio_app.py`](../ui/gradio_app.py) §`_SPREAD_KEYS` and the
`outputs` list before touching anything below.

### How it works

1. **`_state_to_panels(state)`** returns a `dict[str, Any]` — one key
   per Gradio update. Module-level, pure, easy to unit-test.
2. **`_SPREAD_KEYS`** is a tuple of those keys in a fixed positional
   order. Lives inside `build_blocks` because it's UI-shape data, but
   it's the schema both the dict producer and the tuple consumer
   agree on.
3. **`_spread(state)`** reads `_state_to_panels` and emits the
   matching tuple by indexing `_SPREAD_KEYS`.
4. **`outputs` (list)** is the parallel list of Gradio component
   references in the same positional order. This is what every
   `.click()` / `.change()` handler binds its return tuple to.

### The two guards

- **Build-time `assert`** at the bottom of `outputs` construction
  ensures the two parallel lists have matching length. Fires on
  `build_blocks(app)`.
- **`test_state_to_panels_returns_stable_key_set`** pins the exact
  key set `_state_to_panels` emits. Fires in CI.

Together they catch *length* and *key-rename* drift, but they
**cannot** catch a wrong positional pairing (`_SPREAD_KEYS[i]`
naming a different concept from `outputs[i]`). When you reorder or
insert, rebuild both lists from the same source-of-truth — and run
the live UI once to spot-check.

### Why three structures

You'd think one would do. The split exists because:

- The dict is testable without Gradio components.
- The tuple is what Gradio's handler-return shape demands.
- The list of components is what `.click(outputs=…)` needs at
  wiring time.

Collapsing them would require either making `_state_to_panels`
depend on Gradio components (untestable in isolation) or making
the test layer construct Blocks (slow, brittle).

---

## 4. Checklists

### 4.1 Adding a new panel

1. Decide on the visibility flag name. Convention: `<panel>_visible`
   (a `gr.update(visible=…)` value).
2. **`_state_to_panels`** — add the new key to `base` (default
   `gr.update(visible=False)`); set it to `visible=True` in whichever
   `if state.status == …` branch needs it.
3. **`_SPREAD_KEYS`** — append the key.
4. **`outputs`** — append the corresponding `gr.Group` (or whatever
   component you want toggled) at the same position. The build-time
   `assert` will catch a length mismatch.
5. **`build_blocks`** — define the `gr.Group(visible=False) as <name>`
   somewhere readable, ideally near the panel it relates to in flow
   order.
6. **`tests/test_ui_state_panels.py`** — pin the new key in
   `test_state_to_panels_returns_stable_key_set` *and* add at least
   one positive test (state X → panel visible) and one negative test
   (state Y → panel hidden).

### 4.2 Adding a new `ThreadStatus`

1. **`api/models.py`** — add the enum value. Bump
   `PIPELINE_STATE_VERSION` if the new status corresponds to a graph
   topology change (it usually does).
2. **`_state_to_panels`** — add an explicit `elif state.status ==
   ThreadStatus.NEW` branch. **Do not rely on the unknown-status
   fallback.** The fallback exists to fail loud, not to render new
   features.
3. **Tests** — add a test that the new status produces the expected
   visibility pattern.
4. **This file** — extend the §1 table and the §2 matrix.

### 4.3 Adding a new entry point (handler) to the UI

Today every handler calls into `PipelineRunner` directly:

```python
def on_run(text):
    state = _runner(app).start(text)
    return _spread(state)
```

This is the in-process equivalent of `POST /pipeline`. It works
because the UI is mounted on the same FastAPI app — the runner is
shared via `app.state.runner`.

**The rules:**

- Every handler that mutates pipeline state must produce a
  `_spread(state)` return so the panels re-render coherently.
- Handlers that only render auxiliary output (e.g. `export_btn` →
  `export_result_md`) can return a single value; they do **not** go
  through `_spread`.
- Empty-input or "no thread yet" cases that intentionally skip
  `_state_to_panels` must return a tuple positionally aligned with
  `outputs` (see `on_run` empty-text branch as the reference shape).
  This is the only place we hand-build a panel tuple; do not multiply
  it.

### 4.4 Adding a new export backend

1. Implement it in `exporters/<name>.py` against the `Exporter`
   protocol (`exporters/base.py`).
2. Register it in `make_exporter`.
3. **API side** — no UI change required if the backend uses the
   existing `ExportTarget` shape. If it needs a new field, add it to
   `ExportTarget` *and* extend the UI's `export_extra` parsing.
4. **UI side** — extend `export_backend.choices`. The button handler
   already dispatches on `backend` and re-shapes `target`/`extra`,
   so a new backend usually only needs the dropdown entry + a
   target-string parser branch.

---

## 5. Known drift: UI handlers duplicate API logic

Every handler in `build_blocks` is a parallel implementation of an
API endpoint. The shared bottom-of-stack helpers
(`PipelineRunner`, `make_exporter`, `extract_pdf`, `extract_markdown`)
are reused, but the *dispatch and shaping* are duplicated.

| UI handler | API endpoint | What's duplicated |
|---|---|---|
| `on_run` | `POST /pipeline` | Empty-input handling, error mapping |
| `on_po_submit` | `POST /pipeline/{id}/resume` | Resume-value construction (mode-dependent) |
| `on_review_accept` / `on_review_override` | `POST /pipeline/{id}/resume` | Same |
| `on_export` | `POST /pipeline/{id}/export` | `ExportTarget` construction, error → user-message |
| `on_export_deferred` | `POST /pipeline/{id}/export-deferred` | DONE / DONE_FANOUT dispatch, per-story loop |
| `on_upload_pdf` / `on_upload_md` | `POST /uploads/{pdf,markdown}` | Parser invocation, error mapping |

The drift is tracked separately (R2 in
[`docs/ui_audit_2026-05-24.md`](ui_audit_2026-05-24.md) §7). The
proposed fix is to extract the shared shaping helpers to
`api/_shared.py` so both `api/main.py` and `ui/gradio_app.py` import
them — the docstring on `_build_followup_markdown` calls out a
circular-import concern, which `api/_shared.py` resolves.

**Until that lands:** when you change an API endpoint's behavior,
grep `ui/gradio_app.py` for the matching handler and update it in
the same PR. The eval and lint gates don't catch this — it manifests
as a UI doing the wrong thing while the API tests still pass.

---

## 6. Things not to do

- **Don't reintroduce a `_state_to_panels` branch with no `elif`
  guard.** The trailing `else` branch is the "fail loud on unknown
  status" rail and must stay last.
- **Don't move `error_md` back inside `result_panel`.** That's the
  exact regression #186 was about.
- **Don't make `backlog_target_panel` a child of `result_panel`
  again.** Same reason.
- **Don't add a status whose only branch in `_state_to_panels` is
  `pass`** unless you also document why (the `RUNNING` branch does
  this intentionally; it's a placeholder for a future spinner).
- **Don't enable `run_btn` in a non-terminal state.** Clicking it
  starts a fresh thread and orphans the active one. The lock is
  intentional (#186 §6.4).
- **Don't hand-build a panel-update tuple anywhere except `on_run`'s
  empty-input branch.** Anything else should go through `_spread`.

---

## 7. Reference: where things live

| Concern | File |
|---|---|
| Status enum | [`api/models.py`](../api/models.py) (`ThreadStatus`) |
| Pipeline runner | [`api/runner.py`](../api/runner.py) (`PipelineRunner`) |
| API endpoints | [`api/main.py`](../api/main.py) (`_register_routes`) |
| UI panel mapping | [`ui/gradio_app.py`](../ui/gradio_app.py) (`_state_to_panels`) |
| UI wiring | [`ui/gradio_app.py`](../ui/gradio_app.py) (`build_blocks`) |
| Panel/visibility tests | [`tests/test_ui_state_panels.py`](../tests/test_ui_state_panels.py) |
| Follow-up dispatch tests | [`tests/test_ui_followup_dispatch.py`](../tests/test_ui_followup_dispatch.py) |
| Historical context | [`docs/ui_audit_2026-05-24.md`](ui_audit_2026-05-24.md) |
| End-user guide | [`docs/USAGE.md`](USAGE.md) |
