# ADR-0007: Switch from gemini-2.5-flash to gemini-3.5-flash

**Status:** Accepted (2026-05-21)
**Supersedes:** Section 1 of [ADR-0006](adr-0006-provider-switch.md) — the `DEFAULT_MODEL = "gemini-2.5-flash"` line. The rest of ADR-0006 (paid-tier choice, dropped GEval metrics, no provider abstraction, judge architecture) still stands.
**Author:** augustineuzokwe

## Context

ADR-0006 settled on `gemini-2.5-flash` on the paid Google AI Studio
tier as the default for all four production agents and the eval judge.
That choice held for ~one workshop day before two CI failures forced
a re-look.

### What broke

**PR #107** (CI eval gate) — first live CI run of the new gate. The eval
step failed with:

```
google.genai.errors.ServerError: 503 UNAVAILABLE.
{'error': {'code': 503, 'message': 'This model is currently experiencing
high demand. Spikes in demand are usually temporary.'}}
```

The in-library tenacity retries (`DEFAULT_MAX_RETRIES = 5`) exhausted
before the spike cleared. A workflow-level retry was added
(`nick-fields/retry@v3`, 2 attempts × 30s wait) — both attempts hit the
same 503. The PR went green only on manual re-run after the spike
cleared (~30-60 min).

**PR #109** (intent_keyword_overlap metric) — same failure mode the
next time the CI gate ran. Manual re-run again required.

### What we proved with live probing

On 2026-05-21, with the *same* paid AI Studio API key, from a
maintainer laptop, we probed every Flash-family model the catalog
advertised:

| Model | Latency | Status |
|---|---|---|
| `gemini-2.5-flash` (current) | 0.93s | ✓ — works fine locally |
| `gemini-2.5-flash-lite` | 15.37s | ✓ — but slow |
| `gemini-2.0-flash{,-001,-lite}` | — | **404 NOT_FOUND** despite catalog listing |
| `gemini-3.5-flash` | 2.05s | ✓ — newer flagship |
| `gemini-3.1-flash-lite` | 0.70s | ✓ — smallest |

Two findings:

1. **`gemini-2.5-flash` works fine from a maintainer laptop right now.**
   The 503s are hitting **GitHub-hosted runners specifically.** Google
   routes runner IP ranges to a specific backend pool; that pool is
   congested in the geographic region serving us.
2. **`gemini-3.5-flash` (newer flagship) responds in 2.05s.** Different
   model name → different backend capacity pool. Probably immune to
   the specific congestion hitting 2.5-flash today.

### Why this is worth a model swap, not a workaround

We could keep 2.5-flash and just retry. That's what worked on #107 and
will likely work on #109 too. But:

- The blocker recurs **every time Google's routing hiccups**, which is
  outside our control. Workshop pace suffers.
- The newer model (3.5) is the natural upgrade path anyway. ADR-0006's
  closing paragraph already anticipated bumping to a dated 3.x suffix.
- Re-baselining is cheap on Gemini (≈$0.03 per eval run).

## Decision

1. **`agents.config.DEFAULT_MODEL = "gemini-3.5-flash"`.** All four
   production agents and the eval judge follow the constant — no
   per-agent overrides.
2. **No provider abstraction.** Same as ADR-0006 §4 — one provider,
   one model, one consumer per import site. If a fallback model
   strategy ever becomes necessary, extract then.
3. **Eval baselines re-run.** A new dated section in
   `evals/baselines.md` captures the 3.5-flash numbers side-by-side
   with the prior 2.5-flash numbers so quality drift is visible.
4. **Threshold floors stay where they are.** Gate floors were set
   based on empirical mean scores across multiple runs, not on the
   maximum any specific model achieved — they should hold for 3.5-flash
   if quality is preserved. If a floor false-fails, we tighten or relax
   it in a follow-up, not in this ADR.
5. **The 2.5-flash → 3.5-flash provider knob is the constant in
   `agents/config.py`, not a YAML/env-var lever.** Same reasoning as
   ADR-0006: one config surface, one source of truth.

## Consequences

### Cost

Gemini 3.5 Flash paid pricing is comparable to 2.5 Flash at the time
of writing (same flagship-Flash tier). The order-of-magnitude
improvement over Claude Opus 4.7 documented in ADR-0006 still holds —
a full eval run remains ≈$0.03.

If Google publishes materially different 3.5-flash pricing later, this
will surface in the GitHub Actions billing line for `GOOGLE_API_KEY_CI`.
Re-evaluate then.

### Quality

The bar from ADR-0006 was "indistinguishable in quality from Claude
output at the artifact level." 3.5-flash is a newer model, generally
expected to match or improve on 2.5-flash on the kind of structured-
JSON-from-text task RTIA runs. The post-switch `evals/baselines.md`
section is the verification of that expectation — if a metric mean
drops meaningfully (>0.1) below the prior 2.5-flash baseline, this ADR
needs revisiting.

### Reproducibility

`gemini-3.5-flash` is still an *alias*, not a dated suffix. ADR-0001's
model-pinning concern (and ADR-0006's restatement of it) applies
identically: when Google publishes a dated 3.5 suffix, bump the
constant for reproducibility.

### What stays from ADR-0006

Everything except the model name:

- Paid tier (free tier's 20-RPD ceiling still excludes routine eval work).
- Single Gemini judge for all classification metrics.
- No GEval metrics (`intent_faithfulness`, `ac_faithfulness` remain deleted).
- No provider abstraction.
- Smoke script on Claude (out of scope; re-enable when smoke is re-enabled).

## Open question deferred to a future ADR

If 3.5-flash hits the same 503 pattern on GitHub-hosted runners later
in the workshop, the right answer is **not** to ladder up to 4.x or
5.x — it's to land an explicit fallback (catch 503, retry on a sibling
model) inside the agent layer. That carries real engineering cost (new
tests, calibration drift between primary and fallback paths) and is
deferred until we actually see the failure mode on 3.5-flash.
