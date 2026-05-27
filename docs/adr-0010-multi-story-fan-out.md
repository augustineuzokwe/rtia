# ADR-0010: Multi-story fan-out - branch to lightweight stubs instead of deep artifact

**Status:** Accepted (2026-05-23)
**Author:** augustineuzokwe
**Decision driver:** Phase 15.4 of the prod-readiness roadmap, shipped in [PR #162](https://github.com/augustineuzokwe/rtia/pull/162). Previous Phase 15 work ([PR #158](https://github.com/augustineuzokwe/rtia/pull/158)) surfaced the "mega-story" failure mode in live testing: when a requirement secretly carried multiple distinct features, the pipeline merged them into a single tangled artifact and the PO had to discard most of it.
**Complements:** [ADR-0004](adr-0004-final-artifact.md) (the deep four-section artifact contract - unchanged by this decision; fan-out runs simply don't produce one).

## Context

The Analyst returns `implied_stories` - a list of distinct user stories it inferred from the requirement. For a focused single-feature input the list has 0 or 1 entries and the deep flow works as designed: the downstream agents (Story Writer → AC Generator → Test Case Writer → Reviewer) produce the four-section [FinalUserStory](adr-0004-final-artifact.md).

When the input describes several features, `implied_stories` has ≥2 entries. Two possible reactions to this state:

1. **Merge** - push the deep flow forward, let the Story Writer combine the implied stories into one Description, and hope the downstream agents disentangle the ACs and Test Cases.
2. **Fan out** - recognise that each implied story deserves its own backlog item with its own future RTIA run, and produce lightweight stubs (title + one-line summary) instead of attempting a deep artifact across all of them.

Phase 15.3 confirmed that **merging fails consistently**. The Story Writer either picks one feature and silently drops the others, or fuses them into a sentence that misrepresents all of them. AC and Test Case quality degrades correspondingly because each downstream agent works from the muddled story. The PO ends up discarding the artifact and triaging the underlying features by hand.

## Decision

### 1. Branch on the Analyst's `implied_stories` count

Add a conditional edge after the PO checkpoint:

```
START → analyst → po_checkpoint → [conditional edge]
                  ├── fan_out_node → END       (implied_stories ≥ 2)
                  └── story_writer → … → reviewer → END  (otherwise - deep flow, unchanged)
```

The branch decision is made by `is_fanout_mode(state)` in `agents/graph.py` and lives at the LangGraph topology layer - not inside any agent. This means:

- The Analyst doesn't know whether its output will fan out or deep-dive.
- The Story Writer never sees a multi-story state; it only runs in deep mode.
- A future change to the fan-out threshold (e.g. "fan out at ≥3") is a one-line edit at the edge predicate.

### 2. Fan-out produces stubs, never a deep artifact

`fan_out_node` is pure-Python (no LLM call). It reads `state["implied_stories"]`, filters by the PO's `selected_story_titles`, and emits each surviving entry as `ImpliedStory(title, summary)` into `state["fan_out_stories"]`. The terminal state is `ThreadStatus.DONE_FANOUT` - distinct from `DONE`, which is reserved for deep runs.

Rationale for *never* producing a deep artifact in fan-out mode:

- Each stub deserves its own PO checkpoint and Story Review checkpoint. Trying to run four checkpoint pairs in one session would tangle the UI and confuse the audit trail.
- Each stub may need different scope decisions. Folding them into one run forces a single set of PO answers across mismatched scopes.
- The PO re-runs RTIA on any stub title for the full Description / Objective / ACs / Test Cases. The path back to the deep artifact is `Re-run with stub title + scope sentence` - not a buried branch in the multi-story output.

### 3. PO checkpoint reshapes for fan-out mode

Single PO panel covers both flows; the payload's `mode` field drives the UI:

- `mode == "deep"` → free-text answer textbox, one line per critical question. Resume value is `dict[question, answer]`.
- `mode == "fan_out"` → CheckboxGroup of the N implied story titles (default: all checked) plus the same free-text textbox for any non-story critical questions. Resume value is `{"selected_story_titles": [...], "answers": {question: answer, ...}}`.

Empty `selected_story_titles` means "fan out everything" - picked over the alternative ("fan out nothing") because PO inaction on a multi-story input shouldn't silently drop work.

### 4. Single-implied-story (== 1) stays in the deep flow

Threshold is ≥2, not ≥1. A requirement that produces exactly one implied story is conceptually single-feature and routes through the deep flow as before. This keeps the simple-input behaviour stable across versions and avoids a UX regression where every run pops a one-checkbox panel.

## What this does not change

- The Analyst prompt and output shape - unchanged.
- The four-section FinalUserStory contract from [ADR-0004](adr-0004-final-artifact.md) - unchanged; the deep flow produces it exactly as before.
- Single-story input behaviour - unchanged.
- The two-checkpoint design - both checkpoints still exist in deep mode; fan-out runs hit the PO checkpoint only (the Story Review checkpoint is downstream of `story_writer`, which fan-out runs skip).

## Consequences

**Positive:**
- Multi-feature inputs no longer produce mega-story artifacts the PO has to throw away.
- Backlog hygiene improves: one issue per feature, ready for individual RTIA deep-dives later.
- The deep flow stays simple - no defensive logic for "what if there are multiple stories".

**Trade-offs:**
- A two-step workflow for multi-feature inputs: fan out, then re-run on each stub. This is the right cost given the previous failure mode but adds one click compared to the (broken) single-shot path.
- `ThreadStatus.DONE_FANOUT` is a new terminal state that downstream code must handle (exporter dispatch, UI rendering). Both surfaces have been wired (see `api/main.py` and `ui/gradio_app.py`); a future surface needs to remember the dispatch. This is the kind of thing a structurally identical test pair (one for each terminal state) catches early - see `tests/test_api_export_deferred.py` and `tests/test_ui_followup_dispatch.py`.
- `PIPELINE_STATE_VERSION` bumped 1 → 2. Existing on-disk threads at `~/.rtia/state.db` must be cleared on upgrade. Documented in CLAUDE.md; no auto-migration in v1.

## Alternatives rejected

1. **Merge into single deep artifact** - what we had pre-Phase 15.4. Failed in live test (see PR #158 thread).
2. **Pause and ask the PO whether to fan out** - extra checkpoint with low information value; the PO would say "fan out" every time multi-feature input is detected. Better to default to fan-out with checkboxes that let the PO drop unwanted stories.
3. **Deep-dive the first story, defer the rest as stubs** - asymmetric: the "first" story is whichever the Analyst happened to list first. Stochastic ordering would make the deep artifact effectively random across reruns. Rejected.

## References

- [PR #162](https://github.com/augustineuzokwe/rtia/pull/162) - implementation, live verification across the four PO scenarios.
- [PR #170](https://github.com/augustineuzokwe/rtia/pull/170) - bug fix where the Gradio follow-up-export handler didn't dispatch on `DONE_FANOUT`; surfaced because the API endpoint had the dispatch but the UI handler didn't. Concrete instance of the "downstream surface must remember the dispatch" trade-off above.
- [ADR-0004](adr-0004-final-artifact.md) - the deep artifact contract this ADR leaves intact.
