# Eval Data Specification

Every file in `evals/sample-requirements/` is ground truth for DeepEval.
If the ground truth is wrong, all quality metrics are meaningless.
This spec defines what correct ground truth looks like and how to validate it.

---

## The Golden Rule

> The expected output must only contain what is **explicitly stated** or **minimally and directly implied** by the raw requirement.
> If you had to invent a detail, it does not belong in the expected output.

---

## Required File Structure

Every sample file must contain all of the following sections in this order:

```
# Sample Requirement [NN] — [Title]
**Type:** [one-line description]
**Purpose:** [what this sample tests]
## Raw Requirement
## [Optional: Ambiguities RTIA Should Flag]   ← required for vague samples
## [Optional: Features Contained]             ← required for multi-feature samples
## Expected Output (ground truth for eval dataset)
### User Story
### Acceptance Criteria
## Eval Notes
```

---

## Rules for Raw Requirement

- Must read like a real stakeholder request — no pre-formatted structure
- Must not already contain Given/When/Then or user story format
- Must not hint at the expected output (e.g. do not write "the system should have a status field" if status is not supposed to be in the ground truth)

---

## Rules for Expected Output

### User Story
- Must follow format: `As a [role], I want [feature], so that [benefit]`
- Role must be explicitly mentioned or directly inferable from the raw requirement
- Feature must not go beyond what the raw requirement states
- Benefit must reflect an actual problem stated in the raw requirement

### Acceptance Criteria
Each AC must pass all four of these checks before inclusion:

| Check | Question to ask | Fail example from sample-02 |
|---|---|---|
| **Stated** | Is this term or behaviour explicitly in the raw requirement? | "current status" — not stated |
| **Implied** | If not stated, is the inference direct and unavoidable? | "without a page refresh" — not implied by "update quickly" |
| **Non-invented** | Does this introduce a specific data field or behaviour the requirement never referenced? | "by severity, by assignee" — invented |
| **Scoped** | Is this within the agreed scope for this sample? | Persistent filter state in a "filtering only" scoped story — contradicts stated scope |

**If an AC fails any check — remove it or rewrite it.**

### What counts as "directly implied"

Implied is NOT the same as reasonable. Ask: *"Would every reader of this requirement independently arrive at this same specific detail?"*

- ✅ Acceptable: raw req says "managers want visibility" → AC says "managers see a summary view" (summary is a direct inference from visibility)
- ❌ Not acceptable: raw req says "managers want visibility" → AC says "managers see defects by severity and assignee" (severity and assignee are specific inventions)

---

## Rules for Eval Notes

- Must include at least one note about what a **correct** agent response looks like
- Must include at least one note about what a **failure** looks like
- Must not contradict the expected output (e.g. notes say "don't invent scope" but expected output invents scope — this was the bug in sample-02)
- Notes and expected output must be consistent with each other

---

## Pre-Commit Validation Checklist

Run through this before every commit that adds or modifies a sample file:

- [ ] File has all required sections in the correct order
- [ ] Raw requirement reads like a real stakeholder request
- [ ] Every term in the expected user story exists in or is directly implied by the raw requirement
- [ ] Every AC term/behaviour passes all four checks (Stated / Implied / Non-invented / Scoped)
- [ ] Eval Notes do not contradict the Expected Output
- [ ] For vague samples: ambiguities section lists at least 3 specific unanswered questions
- [ ] For multi-feature samples: feature count in the list matches the count stated in the text

---

## Automated Validation (coming in pre-commit setup)

A script `evals/validate_samples.py` will automate the checklist above.
It will also run an LLM faithfulness check via the Anthropic API:

> *"Given this raw requirement, does the expected output introduce any terms or concepts
> not explicitly stated or minimally implied? List any violations."*

The script will block commits to `evals/sample-requirements/` if violations are found.
This prevents the bug pattern where invented scope enters the ground truth undetected.
