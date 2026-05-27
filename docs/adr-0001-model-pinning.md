# ADR-0001: Anthropic model pinning policy

**Status:** Accepted (2026-05-19)
**Author:** augustineuzokwe
**Decision driver:** Phase 1.4 of the prod-readiness roadmap - reproducibility of eval baselines requires knowing exactly which model produced a given run.

## Context

RTIA's agents hard-code a model ID for every LLM invocation. For evaluation discipline (Phase 6: DeepEval suite tied to baselines) we need to know exactly which model produced any historical run. If Anthropic silently rolls out a new minor revision under an unchanged model name, our eval baselines become meaningless without our knowing.

The standard industry mitigation is **dated model IDs**: instead of `claude-opus-4-7` (which can shift), use `claude-opus-4-7-YYYYMMDD` (which is immutable).

## Verified facts (2026-05-19)

Direct query to the Anthropic models list endpoint (`client.models.list()`) returned:

| Model ID                           | Has dated suffix? |
|------------------------------------|-------------------|
| `claude-opus-4-7`                  | NO                |
| `claude-sonnet-4-6`                | NO                |
| `claude-opus-4-6`                  | NO                |
| `claude-opus-4-5-20251101`         | YES               |
| `claude-haiku-4-5-20251001`        | YES               |
| `claude-sonnet-4-5-20250929`       | YES               |
| `claude-opus-4-1-20250805`         | YES               |
| `claude-opus-4-20250514`           | YES               |
| `claude-sonnet-4-20250514`         | YES               |

Models from version **4.6 onwards** do not currently expose a dated suffix variant. Anthropic appears to have shifted policy mid-2026. The canonical name (`claude-opus-4-7`) is the only ID available for the model we use.

## Decision

RTIA's model-pinning policy:

1. **Prefer the dated suffix** (`-YYYYMMDD`) when Anthropic publishes one for the chosen model. Reproducibility is more important than naming aesthetics.
2. **Use the canonical name** when no dated suffix is available - there is no alternative for current-generation models (4.6+). Accept the risk of silent updates.
3. **Track the gap.** When Anthropic publishes a dated form for 4.6+, re-pin in a small follow-up PR and note it in the eval baselines so prior baselines are correctly attributed.
4. **Always log model + version metadata** via LangSmith traces (Phase 1.5 will add `prompt_hash`; the model ID is already in the LangChain trace). Future eval runs can attribute regressions correctly even when the model identifier didn't change.

## Status of v1

- `DEFAULT_MODEL = "claude-opus-4-7"` centralized in `agents/config.py`.
- Single source of truth: both `agents/requirements_analyst.py` and `agents/user_story_writer.py` import from there.
- Re-verify dated availability at the start of each major eval-baselining cycle (`uv run python -c "from anthropic import Anthropic; print([m.id for m in Anthropic().models.list().data])"`).

## Consequences

**Positive**
- Single place to change the model for both agents.
- Documentation of the verification finding so future readers don't repeat the investigation.

**Negative / risks**
- Without a dated pin, Anthropic could silently roll out a `claude-opus-4-7` revision and our eval baselines would shift without our knowing. Mitigation: when (not if) the Phase 6 eval suite lands, it'll catch the regression at the per-agent metric level.

## Followups

- Re-check Anthropic's models list quarterly; re-pin when dated forms appear.
- Phase 1.5 (prompt versioning): pair model ID with `prompt_hash` in trace metadata so an eval result is fully attributable to (model, prompt) version.
- Phase 6 (eval suite): record `(model, prompt_hash)` per baseline so a future model bump is visible as a baseline shift.
