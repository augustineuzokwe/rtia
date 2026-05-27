# PO answer fixtures

The eval runner is unattended - there is no human at the PO Checkpoint. When
the Analyst emits CRITICAL ambiguities, the runner needs to supply *some*
answer so the pipeline can proceed to the Story Writer.

For a long time the runner used a single fixed string ("pick the first
reasonable interpretation") regardless of the question. That worked for
sample-01 (which usually has no critical ambiguities) but coupled multi-feature
samples to whatever the Story Writer happened to infer from the vague
directive - re-runs of the *same* prompt hash produced different scopes,
and AC coverage scored against a moving target.

This directory replaces that string with a **per-sample directive** that pins
the scope decision to whatever the AC ground truth assumes. One YAML file per
sample, named exactly after the sample stem (e.g. `sample-02-vague-ambiguous.yaml`).

Format:

```yaml
po_directive: |
  One or more sentences telling the Story Writer exactly which capability
  this issue should cover, and what is out of scope. Phrased as an
  authoritative PO answer - not a question, not a hedge.
```

Behaviour:

- Runner loads `evals/ground-truth/po-answers/<sample_stem>.yaml` if present.
- For every CRITICAL ambiguity the Analyst emits on that sample, the runner
  hands the same `po_directive` to the Story Writer as the answer to that
  question.
- If no fixture file exists for a sample, the runner falls back to the legacy
  constant string. New samples without a fixture continue to work; only
  multi-feature samples benefit from pinning.
- The live demo (`scripts/run_pipeline_demo.py`) does NOT use these fixtures -
  it has a real human checkpoint.

## What this isolates

After this change, AC-layer metrics score the Story Writer + AC Generator
**given a known scope**. They do NOT score whether the Analyst + auto-resolver
picked the right scope - that is a different question and worth its own
metric. Each metric should measure one thing.
