# ADR-0006: Switch all agents and the eval judge from Anthropic Claude to Google Gemini

**Status:** Accepted (2026-05-21)
**Supersedes:** [ADR-0001: Anthropic model pinning policy](adr-0001-model-pinning.md) (the model-ID provider stays the same — Anthropic — but no Anthropic model ID is used by RTIA agents anymore)
**Author:** augustineuzokwe

## Context

After Phase 9.1 (PR #90) put the Test Case Writer on Gemini 2.5 Flash to prove the provider swap was mechanically viable, the project lead asked the harder question: **why is the workshop project paying Anthropic at all?**

Audit findings:

- RTIA's actual task is *structured JSON output from short text input* (a few paragraphs in, a few paragraphs out). That's a Sonnet/Haiku-tier task, arguably a Flash-tier task.
- Claude Opus 4.7 is priced for hard multi-step reasoning (≈$15/MTok in, ≈$75/MTok out). We never use Opus's actual selling points.
- Each live demo cost ≈$0.30–0.50; each full eval run (with judge) cost ≈$1–2.
- PR #90's Test Case Writer output on `gemini-2.5-flash` was empirically indistinguishable in quality from the Claude AC Generator output produced by PR #85.

### Free tier — discovered limits

Initial planning assumed a generous free-tier quota. The actual live-API
error during cutover verification on 2026-05-21 revealed `gemini-2.5-flash`
free tier is capped at **20 requests per day per project per model**
(quota ID `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). That is
far below RTIA's eval workload (12 agent calls per sweep across 3 samples,
plus judge calls) — a single eval run typically exhausts the day's quota.

The cutover therefore lands on the **paid Gemini tier**, not free. Paid
pricing on `gemini-2.5-flash` (≈$0.075/MTok input, ≈$0.30/MTok output)
puts a full pipeline run at ≈$0.005 and a full eval run at ≈$0.03 —
still roughly an order of magnitude cheaper than the Claude Opus 4.7
baseline, with no daily quota ceiling.

## Decision

1. **All four production agents** (`requirements_analyst`, `user_story_writer`, `ac_generator`, `test_case_writer`) run on `gemini-2.5-flash` via `langchain_google_genai.ChatGoogleGenerativeAI`. `agents.config.DEFAULT_MODEL = "gemini-2.5-flash"`.
2. **The eval-suite LLM judge** (`evals/judge.py`) also runs on `gemini-2.5-flash`. Renamed `ClaudeJudge` → `GeminiJudge`.
3. **GEval-style metrics are deleted.** Specifically: `intent_faithfulness` (from `evals/metrics.py`) and `ac_faithfulness` (from `evals/ac_metrics.py`). The remaining 4 metrics (`actor_set_completeness`, `ambiguity_discipline`, `ac_coverage`, `ac_testability`) are all classification or programmatic — appropriate for free Gemini Flash.
4. **No provider abstraction added.** Per CLAUDE.md §4.6 — one provider, one consumer per import site. If we ever need a multi-provider strategy, extract `build_chat_llm()` then.
5. **`scripts/run_integration_smoke.py` is out of scope.** Cron disabled in PR #75; manual-trigger only. Leave on Claude until smoke is re-enabled (decision-deferred to that future).

## Why drop GEval metrics rather than keep them on Claude

Considered:

| Option | Cost | Quality | Verdict |
|---|---|---|---|
| Keep GEval on Claude Haiku | ~$0.20/run | Good (Claude does subtle semantic judgment well) | Workable but mixes providers in eval infra |
| Move GEval to Gemini Flash | $0 | Unreliable — Flash is weakest at the subtle "did this invent scope?" reasoning GEval needs | Rejected — misleading scores worse than no metric |
| Drop GEval, keep deterministic + classification | $0 | Eval becomes 4 metrics; lose semantic-faithfulness coverage | **Chosen** |

The two dropped metrics were already documented in `evals/baselines.md` as the weakest:
- `ac_faithfulness`: structurally pessimistic (scored ACs against Story Writer output only, ignoring Analyst context the AC Generator legitimately reads).
- `intent_faithfulness`: noisiest metric in the suite, swinging ±0.10 on Analyst stochasticity even with identical inputs.

Dropping them is also reversible — the metric code lives in git history, and a deterministic replacement for `intent_faithfulness` (keyword-overlap against ground-truth key noun phrases) is parked under [Epic #92 "Future improvements"](https://github.com/augustineuzokwe/rtia/issues/92).

## Verified facts (2026-05-21)

`langchain-google-genai==4.2.2` `ChatGoogleGenerativeAI` field check (`model_fields`):

| Field | Present? |
|---|---|
| `model` | ✅ |
| `temperature` | ✅ (default 1.0; no Opus-style rejection) |
| `timeout` | ✅ |
| `max_retries` | ✅ |
| `max_output_tokens` | ✅ (**replaces** Anthropic's `max_tokens`) |
| `cached_content` | ✅ (different shape from Anthropic's `cache_control` — see "Caching" below) |
| `google_api_key` | ✅ |
| `max_tokens` | ❌ — renamed |

Available Gemini IDs (live `models.list` endpoint, 2026-05-21) suitable for our task:
- `gemini-2.5-flash` — stable, free-tier-eligible (≥10 RPM / 250 RPD) — **chosen**.
- `gemini-2.5-pro` — stable, smaller free quota.
- `gemini-3.5-flash` — newer, behaviour less documented.
- `gemini-3.*-preview` — preview-only, can change without notice.

## Caching

Anthropic's `cache_control: ephemeral` message-block pattern does not exist on Gemini. Gemini's context caching uses a separate `client.caches.create(...)` call that returns a cache handle, which is then passed as `cached_content="cachedContents/..."` to the model. Different shape entirely.

For RTIA's small prompts (Analyst ≈ 2k tokens, others smaller) on a free tier with no cost driver, the simple **no-cache** path is correct. Revisit only if rate limits or latency become a problem.

## Defensive parsing

`agents/_llm_utils.py:strip_json_fence` strips an optional ` ```json ` fence Gemini occasionally wraps structured-output in despite a "no fences" instruction. Idempotent on un-fenced input.

## Risks accepted

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Prompts tuned on Claude may behave subtly differently on Gemini (especially Analyst with auto-resolver / implied-stories logic) | Live re-baseline; iterate prompts in this PR until parity. |
| R2 | Eval baselines reset — historical Claude-era numbers are non-comparable | New dated row in `evals/baselines.md` marks the cutover. Older rows pruned per Option 1 (aggressive cleanup); they remain in git history. |
| R3 | **CORRECTED 2026-05-21:** Free-tier quota is 20 RPD per model (verified from live API error during cutover), not the ~250 RPD initially assumed. Mitigation moved from "stay within free tier" to **switch to paid tier**. Free tier remains available as a fallback for occasional local probes; routine use of RTIA — demos, evals, CI — runs on paid pricing. |
| R4 | Google AI Studio T&Cs differ by tier. Free-tier permits prompt-use for model improvement; paid-tier has stricter data-use clauses. Acceptable for the workshop scope (synthetic sample data); tracked under [Issue #93](https://github.com/augustineuzokwe/rtia/issues/93) for re-evaluation when real client data ever enters the pipeline. |

## What would justify switching back

- A real prompt regression on Gemini we cannot iterate away (none observed at cutover).
- A workshop need for evals to score subtle semantic faithfulness that Gemini Flash judge cannot calibrate (would also drive re-adding GEval).
- A change in RTIA's input distribution to real client requirements where T&C concerns (R4) become operative.
- A different cost model where Anthropic becomes cheaper or where free-tier rate limits become binding.

## Notes for the future

- ADR-0001's model-pinning concern (immutable dated IDs for reproducibility) applies to Gemini too. `gemini-2.5-flash` is an alias-style ID, not pinned. When Google publishes a dated suffix, update `DEFAULT_MODEL` accordingly.
- The provider-abstraction question is deferred. If a second LLM provider ever enters this codebase, that's when to extract `agents.config.build_chat_llm()` — not before.
