# ADR-0004: FinalUserStory as the v1 output contract

**Status:** Accepted (2026-05-19)
**Author:** augustineuzokwe
**Decision driver:** Phase 3 of the prod-readiness roadmap. Every agent in the pipeline contributes to ONE final artifact that a PO, PM, BA or QA pastes directly into Jira or GitHub Issue. The artifact contract must exist BEFORE the AC Generator (Phase 8) and Test Case Agent (Phase 9) land, so each new agent has a stable shape to populate.

## Context

The user defined RTIA's output (planning session 2026-05-19) as a backlog-ready user story with four minimum sections:

1. **Description** - what the role wants
2. **Objective** - the value/outcome
3. **Acceptance Criteria** - Given/When/Then
4. **Test Cases** - happy path + edge cases + negatives

Without an artifact contract, each agent would shape its own output and the pipeline's final step would be a brittle "stitch these together somehow" function in the demo. The Plan-agent's pre-implementation critique specifically called out this risk and recommended the artifact-contract-first design pattern.

## Decision

Define a single Pydantic model `FinalUserStory` in `agents/final_artifact.py` that holds all four sections plus assumptions and metadata. Every agent that produces backlog content writes INTO this artifact's appropriate section. The pipeline's final node is a `composer_node` that assembles the artifact from current pipeline state.

### Schema

```python
class FinalUserStory(BaseModel):
    description: str                                  # ← Story Writer (live)
    objective: str                                    # ← Story Writer (live)
    acceptance_criteria: list[AcceptanceCriterion]    # ← AC Generator (Phase 8)
    test_cases: list[TestCase]                        # ← Test Case (Phase 9)
    assumptions: list[str]                            # ← Story Writer (live)
    metadata: dict[str, str]                          # ← any agent (e.g. Reviewer)
```

`AcceptanceCriterion(given, when, then)` and `TestCase(scenario, type, steps, expected)` are sub-schemas. `TestCase.type` is `Literal["happy_path", "edge_case", "negative"]` for downstream coverage metrics in Phase 9.

### Section-author-per-agent

| Section | Authoring agent | Status |
|---|---|---|
| description, objective, assumptions | User Story Writer | live |
| acceptance_criteria | AC Generator | Phase 8 |
| test_cases | Test Case Agent | Phase 9 |
| metadata | any (review notes, model+prompt versions, …) | live (Story Writer adds nothing yet) |

Each downstream agent's PR will:
1. Build the new node.
2. Have that node write to its specific FinalUserStory section IN-PLACE (the composer node currently assembles a fresh artifact; Phase 8/9 will refactor to mutate in-place or have the composer use partial state).
3. Add per-agent eval metrics to the eval suite.

### Placeholder behavior for unbuilt agents

`FinalUserStory.as_markdown()` renders all four sections regardless of population:

- If a section has content, it renders normally.
- If empty, it renders a placeholder line:
  - AC: `_To be populated by the AC Generator agent (Phase 8)._`
  - Test Cases: `_To be populated by the Test Case agent (Phase 9)._`

This is deliberate. Engineering teams reading the v1 artifact see the full intended shape, not a half-rendered output that pretends to be complete. The placeholders disappear automatically as each agent lands.

### Renderers

- `as_markdown() -> str`: paste-ready Jira/GitHub Issue markdown. Section headers as `##`, AC as bullets with bold Given/When/Then keywords, Test Cases as `####` sub-sections with type tags. Maps to **US-10 (#15)**.
- `as_json(indent=2) -> str`: lossless JSON via `model_dump`. Round-trips via `FinalUserStory.model_validate_json(...)`. Maps to **US-11 (#16)**.

### Pipeline wiring

`agents/graph.py` adds a `composer_node` after the Story Writer:

```
Analyst → PO Checkpoint → Story Writer → composer → END
```

`PipelineState` gains `final_artifact: FinalUserStory`. The composer reads existing state (UserStory + assumptions) and writes the FinalUserStory. Future agents (AC Generator, Test Case Agent) attach BEFORE the composer; each writes to its FinalUserStory section. The composer remains the final-state sink that ensures the artifact is always populated at pipeline exit.

### Story Review Checkpoint placement (Phase 4 preview)

Phase 4 will insert a Story Review Checkpoint between Story Writer and the composer. The checkpoint reads the rendered artifact (now possible because we have a renderer) and lets the PO accept or override the Description/Objective before the artifact is finalized. This ADR notes the design intent so Phase 4's PR description doesn't have to re-justify it.

## Consequences

**Positive**
- Future agent PRs are smaller: each agent populates a known section instead of negotiating with the rest of the pipeline.
- The demo output IS the v1 production output - no separate "format for humans" step.
- `as_json()` is the natural API response shape if/when Phase 14's FastAPI surface lands.
- LangGraph state versioning (ADR-0002) extends naturally: `final_artifact` is a v1 field.

**Negative / risks**
- Placeholder text in the rendered output requires explanation to a first-time reader. Mitigation: the placeholders explicitly name the responsible agent and phase.
- Adding sections later (e.g. "Risks" or "Open Questions") means adding fields to `FinalUserStory` and updating the renderer. Schema-stability test catches accidental removal but doesn't prevent renderer drift.
- `composer_node` is a pure transformation today (no LLM call); some readers may question why it's a node at all. Justification: future Phase 8/9 agents WILL add LLM-backed nodes BEFORE the composer; the composer stays as a final-state assembler that doesn't need a special case if it's already a node from day one.

## Followups

- **Phase 4 (Story Review Checkpoint)**: insert between Story Writer and composer; renders artifact, accepts edits.
- **Phase 8 (AC Generator)**: new node before composer; populates `acceptance_criteria`.
- **Phase 9 (Test Case Agent)**: new node before composer; populates `test_cases`.
- **Phase 10 (Reviewer Agent)**: new node before/after composer; populates `metadata.review_notes` (or similar).
- **Phase 1.5 followup**: composer node could automatically populate `metadata['analyst_prompt_hash']` and `metadata['writer_prompt_hash']` from the existing `_PROMPT_HASH` module-level constants so the artifact carries the prompt versions that produced it. Not in this PR - keeps scope tight.
