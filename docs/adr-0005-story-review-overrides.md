# ADR-0005: Story Review Checkpoint - direct overrides, no re-run

**Status:** Accepted (2026-05-19)
**Author:** augustineuzokwe
**Decision driver:** Phase 4 of the prod-readiness roadmap. The Story Review Checkpoint is the second human-in-the-loop, sitting between the Story Writer and the Composer. The PO sees a rendered artifact preview and either accepts wholesale or wants to change it. The question is: when they reject, what happens next?

## Context

Earlier discussion (planning session 2026-05-19) explicitly chose **scan-and-override** semantics over per-line PO triage. The plan text for Phase 4 says:

> on `accepted=False` with overrides, mutates description/objective and re-runs Story Writer once

That phrasing left open two possible interpretations:

1. **Direct override** - the PO provides the new description / objective text directly; pipeline writes those values straight into state.
2. **Re-run with hints** - the PO provides a hint like "make it shorter" or "focus on the manager"; the Story Writer re-runs with that hint as additional input.

ADR-0004 (FinalUserStory artifact contract) deferred this choice; this ADR closes it.

## Decision

**Direct override.** When the PO rejects the rendered story, they provide replacement text for `description` and/or `objective`. The pipeline writes those values directly into `user_story` state. **The Story Writer does NOT re-run.**

Resume payload shape:

```python
# Accept (default; most common):
{"accepted": True}

# Override (PO writes the new text):
{
    "accepted": False,
    "description": "As a manager, I want overridden text.",
    "objective": "Overridden value statement.",
}

# Partial override (omitted fields keep the writer's values):
{
    "accepted": False,
    "description": "Only this changed.",
    # objective omitted → falls back to Story Writer's output
}
```

## Why direct override

1. **No iteration risk.** The original PO Checkpoint design (ADR-implicit in PR #40) deliberately rejected iterative Analyst re-runs because LLMs given more input tend to flag more ambiguities, not fewer. The same principle applies here: a "re-run with hint" path biases toward more invasive rewrites of fields the PO didn't want to touch. Direct override removes the model from the loop entirely for the override case.

2. **The PO knows what they want.** At review time, the PO is reading rendered output. If they're rejecting, they have a concrete edit in mind. Asking them to verbalize that as a hint to a model, when they could just type the text, adds friction and noise.

3. **Smaller surface area.** Direct override needs zero prompt engineering and zero new tests of Story Writer behavior under hint-style input. Re-run-with-hints would need both, plus tests for "what if the hint contradicts the original requirement."

4. **Single pass.** Without re-run, this is genuinely one round-trip per PO review. The plan text "single retry, not iterative" - direct override is the strongest form of that constraint.

## Trade-offs explicitly accepted

**Negative:**
- A PO who has a vague preference ("shorter") and doesn't want to write the full text can't ask the model to do it for them. They either write the text or accept the existing one. Acceptable for v1; revisit if PO friction surfaces.
- Partial overrides do require the PO to know the schema (description / objective field names). The demo prompts for each explicitly; a future UI (Phase 14) can render labeled text areas.
- Multi-iteration review ("I want to override AGAIN") isn't supported in a single pipeline run. The PO must re-invoke the pipeline with a new thread_id. Acceptable: most stories will accept on first review; rare cases that don't likely need a fresh look anyway.

## Architecture

`agents/graph.py` adds `story_review_checkpoint_node` between `story_writer` and `composer`:

```
Analyst → PO Checkpoint → Story Writer → Story Review Checkpoint → Composer → END
                                                  ↑
                                  always pauses; PO accepts or overrides
```

The node:

1. Builds a preview `FinalUserStory` from current state (using the same `as_markdown()` the Composer's output uses).
2. Calls `interrupt()` with payload `{"rendered_artifact": preview.as_markdown(), "description": ..., "objective": ..., "assumptions": [...]}`.
3. On resume:
   - `{"accepted": True}` → returns `{}`; no state change; Composer proceeds with current `user_story`.
   - Else → builds a new `UserStory` with the override values (falling back to current values for omitted fields) and returns `{"user_story": <new>}`.

Distinguishability from the PO Checkpoint at the demo/API layer: the interrupt payload's keys are disjoint. PO emits `critical_ambiguities`; Story Review emits `rendered_artifact`. The demo dispatches on key presence; tests verify both shapes appear at the expected steps.

## Out of scope (deferred to later phases)

- **PO can edit `assumptions`.** Not in Phase 4 - assumptions are the Story Writer's record of normal-ambiguity defaults. If the PO disagrees, the override path is to fix `description` / `objective`. Phase 4 doesn't expose `assumptions` editing; revisit if PO friction shows up.
- **Hint-style re-run.** If real PO usage suggests vague-preference overrides are common (and the typed-text-only friction is too high), introduce a third resume mode that DOES pass a hint to a re-run of Story Writer. Until evidence supports it, the simpler design wins.
- **Multi-round review.** A second override pass in the same pipeline run isn't supported. New thread_id required.

## Followups

- **Phase 14 (UI)**: render the rendered_artifact preview as readable markdown in the UI; offer text areas for description / objective with the current values pre-filled.
- **Phase 12 (security)**: when overrides are typed by an end user via a UI, the input must be sanitized (length cap, control characters stripped) before it lands in `user_story`. Phase 4's CLI demo doesn't enforce this; Phase 14's UI MUST.
- If PO friction with "no hint-style re-run" surfaces in practice, supersede this ADR with a new one documenting the hint-mode design.
