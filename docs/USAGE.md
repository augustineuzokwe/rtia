# Using RTIA - a guide for POs, BAs, and QA leads

This is the end-user guide. If you're setting RTIA up or contributing code,
start with the [README](../README.md) instead.

RTIA turns a raw requirement - a feature request, a meeting note, a PRD
paragraph - into a backlog-ready user story. You stay in control: the
system pauses for your input at the two moments where its guesses
matter most, then produces the finished artifact you can paste into
Jira or push to GitHub Issues.

## 1. What RTIA gives you

A single user-story artifact with four sections:

| Section | What it tells you |
|---|---|
| **Description** | The user-story sentence: "As a [role], I want [feature], so that [benefit]." |
| **Objective** | The value the role gets - the *why* behind the feature. |
| **Acceptance Criteria** | Given / When / Then bullets that define done. |
| **Test Cases** | Concrete scenarios a QA engineer can run: happy path, edge cases, negative paths. |

This is the **deep flow**. When you paste a requirement that secretly
contains several distinct features, RTIA detects that and switches to a
different shape - see the [split path](#5-split-when-your-input-has-multiple-stories) below.

## 2. Starting a run

1. Open the UI (the maintainer will send you the URL - it includes a one-time token).
2. Either:
   - Paste your requirement into the **Requirement text** box, or
   - Drop a **PDF** or **Markdown** file into one of the upload areas.
3. Click **Run pipeline**.

You don't need to format the input. Bullets, prose, half-finished
sentences, copy-pasted Slack messages - all fine. RTIA's first agent
(the *Analyst*) reads it and figures out what matters.

## 3. The two checkpoints

RTIA pauses twice. Both pauses are deliberate.

### 3.1 PO checkpoint - resolving ambiguity

When the Analyst spots something **critical** that's missing - a scope
question that would change the resulting feature - RTIA stops and shows
you a list of questions. Each question has a free-text answer box.

You don't have to answer every "normal" ambiguity the Analyst found;
RTIA only pauses for the critical ones. Anything not asked here will
flow forward as a story *assumption* that you can override at the next
checkpoint.

Write your answers - one per line, in the same order as the questions -
and click **Submit**. The pipeline resumes.

### 3.2 Story Review checkpoint - verifying the output

After the Story Writer agent produces a draft, RTIA pauses again and
shows you the rendered Description + Objective + recorded assumptions.

Two options:

- **Accept** if the draft looks right. The pipeline continues to the AC
  Generator and Test Case Writer.
- **Override** if it doesn't. Edit the Description and/or Objective in
  the override box; your edits replace the agent's text and the
  downstream agents work from your version.

If you keep overriding the same thing across runs, that's a signal
worth telling the maintainer - the underlying prompts can be tuned.

## 4. Reading the final artifact

When the pipeline finishes, the result panel shows:

- The rendered Markdown of the four-section artifact.
- A **Download Markdown** button - file is ready to paste into Jira or commit anywhere.
- A **Push to backlog** section to send it straight to Jira or GitHub Issues (see [§6](#6-pushing-to-jira-or-github)).

The artifact is meant to be the *first draft* for a backlog story -
not the final word. Reviewing the ACs and Test Cases before grooming is
the expected workflow.

## 5. Split: when your input has multiple stories

Some requirements describe several distinct features that each deserve
their own backlog item. Example: *"We need a flaky-test quarantine
system: auto-detect, dashboard, audit log, Slack notifications."* That's
four stories, not one.

When the Analyst sees this pattern, RTIA pauses at the PO checkpoint
with a checkbox list of the stories it detected. You can:

- **Submit with all checked** - RTIA creates a lightweight placeholder story
  for each (title + one-line summary), then stops.
- **Uncheck the ones you don't want** - only the remaining placeholders are
  produced.

The split path **does not** produce the deep four-section artifact.
The reasoning: each placeholder deserves its own RTIA run later, with its own
PO checkpoint and Story Review checkpoint. Trying to deep-dive four
stories in one session would tangle the checkpoints.

To get the full artifact for one of the placeholders, just re-run RTIA on that
placeholder's title and a short description of its scope.

## 6. Pushing to Jira or GitHub

The result panel has two backlog-push controls - they look similar but do
different things.

| Control | Use it when |
|---|---|
| **Push to backlog** | Deep flow only. Pushes the single four-section artifact to one Jira issue or one GitHub issue. |
| **Create follow-up issues** | Split flow (or deep flow with leftover implied stories). Pushes one lightweight placeholder per story. |

Both have the same configuration:

- **Backend** - `jira` or `github`.
- **Target** - the Jira project key (e.g. `RTIA`) or the GitHub repo (`owner/name`).
- **Optional** - the Jira parent epic key or GitHub project number.
- **Dry run** - when on, RTIA builds the payload it *would* send and
  shows it back to you without making any API call. Use this before the
  first live push to confirm the issue text looks right.

A **dry run** is the safest way to preview an export. Turn it off only
when you're ready to create real backlog issues.

## 7. When the artifact isn't what you wanted

Things to try, in order:

1. **Re-run with sharper input.** A vague requirement gets a vague
   artifact. Adding a sentence on the user role and the success
   condition often does more than tweaking the system.
2. **Use the Story Review override.** If the Description is right but
   the Objective is off, edit just the Objective in the override box;
   the downstream agents will work from your text.
3. **For multi-feature requirements, use the split path** and then
   re-run RTIA on each placeholder individually. Trying to force a deep run on
   a four-feature input tends to mash the ACs and Test Cases together.
4. **Re-run on a single placeholder.** If the split produced a placeholder whose
   title is right but whose one-liner is wrong, re-run RTIA with the
   placeholder's title as the requirement plus a sentence of your own scope
   notes.

If a pattern repeats - same agent producing the same kind of
unwanted output across runs - that's worth flagging via a bug report;
see the issue templates in `.github/ISSUE_TEMPLATE`.

## 8. Caching and re-runs

By default RTIA caches LLM responses on disk so that a second run of
the same input doesn't re-pay for the same answer. The cache lives at
`~/.rtia/cache/` and entries expire after 24 hours.

You almost never need to think about this. The two times you do:

1. **You just edited a prompt** - no action needed. The cache key
   includes the prompt hash, so your edit auto-invalidates every
   relevant entry on the first re-run.
2. **You want a fresh measurement on purpose** - for a re-baseline,
   an adversarial probe, or a sanity check against model drift. Pass
   `--no-cache` to `evals/run_evals.py` or `scripts/run_pipeline_demo.py`,
   or export `RTIA_LLM_CACHE=disabled` for the session.

The CI regression job (`.github/workflows/ci.yml`) always disables the
cache so the eval gate measures live behaviour on every PR. See
[ADR-0013](adr-0013-llm-response-cache.md) for the design rationale,
including why the 24h TTL is deliberately shorter than Promptfoo's
14-day default.

## 9. Stochastic AC validation for adversarial samples

The four adversarial samples (`sample-04` through `sample-07`) test the
*tail* of the model's distribution - the rare 1-in-50 failure that's the
entire point of an adversarial sample existing. Single-pass measurement
misses it. Run them stochastically when you change anything that could
affect safety behaviour:

```bash
uv run python evals/run_evals.py sample-04 --n-runs 10 --no-cache
```

Sample passes when every metric's pass-rate (fraction of runs at-or-above
the metric's floor) meets the configured threshold - default 95 % for
adversarial samples. N > 1 forces the cache off automatically;
[ADR-0013](adr-0013-llm-response-cache.md) and
[ADR-0014](adr-0014-stochastic-ac-validation.md) explain why.

Run nightly: the `nightly-safety-regression` workflow runs N=10 on the
four adversarial samples every night at 02:00 UTC. If you suspect a
regression off-cycle, trigger it manually from the Actions tab.

## 10. Running RTIA with zero API spend (full-local mode)

RTIA's default config uses Gemini 3.5 Flash because it's already
near-free (~$0.005 per pipeline demo, ~$0.03 per eval gate). But if you
want to run the whole stack without making any external API call - for
an air-gapped demo, a privacy-sensitive deployment, or just curiosity
about how a local model handles your inputs - set two env vars:

```bash
export RTIA_LLM_PROVIDER=ollama
export RTIA_OLLAMA_JUDGE=1
uv run python scripts/run_pipeline_demo.py
```

Prerequisites: Ollama installed (`brew install ollama` then
`brew services start ollama`) and at least one model pulled
(`ollama pull llama3.1:8b` - the default).

The two switches are independent on purpose. Setting only
`RTIA_LLM_PROVIDER=ollama` swaps the **generator** (5 production agents)
but keeps the deepeval **judge** on Gemini - useful for the
apples-to-apples comparison documented in
[ollama-probe-2026-05-26.md](ollama-probe-2026-05-26.md). Setting both
gives you a strictly $0 stack but mixes two variables, so eval scores
are less reliable as a quality signal.

**Quality caveat.** On the 7-sample golden set,
[Llama 3.1 8B](ollama-probe-2026-05-26.md) regressed > 15 % vs Gemini
on three Analyst-side metrics (`ambiguity_discipline`,
`intent_keyword_overlap`, `requirement_fidelity`) while AC and Test Case
generation tolerated the swap within ±3 %. Treat full-local mode as
exploratory until you've run the probe on your own dataset.

## See also

- [README](../README.md) - setup, architecture, contributing.
- [CLAUDE.md](../CLAUDE.md) - repo-local rules for Claude Code sessions.
- [docs/adr-0004-final-artifact.md](adr-0004-final-artifact.md) - why the artifact has these four sections and not others.
- [docs/adr-0013-llm-response-cache.md](adr-0013-llm-response-cache.md) - the cache design that backs §8 above.
- [docs/adr-0014-stochastic-ac-validation.md](adr-0014-stochastic-ac-validation.md) - the N-run design that backs §9 above.
- [docs/ollama-probe-2026-05-26.md](ollama-probe-2026-05-26.md) - quality + cost + latency measurements behind the §10 caveats.
