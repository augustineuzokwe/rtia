# Sample Requirement 03 — Multi-Feature

**Type:** Well-written but contains multiple distinct features in one block
**Purpose:** Tests whether the Requirements Analyst Agent correctly identifies that this is more than one user story and scopes the output accordingly — either by splitting it or by flagging it for the PO to decide.

---

## Raw Requirement

The dashboard needs to let testers filter test results by date range, environment (dev, staging, prod), and test suite name.

It should also send an email alert to the QA Lead when the failure rate for any test suite goes above 20% in a single run.

Test results should be exportable to CSV. The applied filter state should be saved per user account so their preferences are remembered the next time they log in.

---

## Features Contained (RTIA should identify these)

1. **Filtering with persistence** — filter by date range, environment, test suite name; filter state saved per user account across sessions
2. **Email alerting** — failure rate threshold trigger (>20%), recipient is QA Lead
3. **CSV export** — of filtered test results

These are 3 separable user stories. Persistent filter state is a sub-capability of filtering (it describes how filter preferences behave), not a standalone feature. The Requirements Analyst Agent should flag this to the PO at the human checkpoint.

---

## Expected Output (ground truth for eval dataset)

> Note: The expected output below represents the correctly scoped single user story for **filtering and filter persistence**, as if the PO decided to scope to one story per session. Email alerting and CSV export become separate backlog items.

### User Story (scoped to filtering + filter persistence)
As a tester, I want to filter test results by date range, environment, and test suite name, so that I can quickly find the results relevant to my current area of focus without scrolling through unrelated runs.

### Acceptance Criteria

**Given** I am a logged-in tester on the test results page
**When** I apply a date range filter
**Then** only test runs within that date range are displayed

**Given** I am filtering test results
**When** I select one or more environments (dev, staging, prod)
**Then** only results from the selected environments are shown

**Given** I am filtering test results
**When** I enter or select a test suite name
**Then** only results matching that suite are displayed

**Given** I have applied one or more filters
**When** I log out and log back in
**Then** my previously applied filter selections are restored automatically

---

## Expected Analyst Output (per-agent ground truth for the Requirements Analyst)

> Ground truth for the **Analyst agent only**. Phrasing is illustrative — eval metrics
> compare against this fuzzily (judge for intent, set semantics for actors, category
> coverage for ambiguities and implied stories), not by exact string match.

### Intent
Improve the test-results dashboard with filtering (with persisted preferences) and CSV export, and alert the QA Lead when a test suite's failure rate crosses a threshold.

### Actors (expected set)
- tester
- QA Lead

### Ambiguity Categories
- **multi-feature pick-one** — the requirement bundles independent capabilities; the Analyst must emit a CRITICAL ambiguity asking the PO to pick a single story for this issue (the others should be raised separately). This is the *only* ambiguity required for this sample; other story-shape questions are not expected because each feature is itself stated clearly enough at this level.

### Implied Stories
The Analyst should populate `implied_stories` with three entries (titles will vary; the eval checks the count and the category of each):

- **Filtering with persistence** — tester filters test results by date range, environment, and test suite name, with filter preferences saved across sessions. (Persistent filter state is a sub-capability of filtering, not a 4th standalone story.)
- **CSV export** — tester exports test results to CSV.
- **Email alerting on failure spike** — QA Lead is emailed when a test suite's failure rate exceeds 20% in a single run.

A 4-story output that splits filter persistence out as its own story is a known failure mode the eval should catch.

---

## Expected Acceptance Criteria (per-agent ground truth for the AC Generator)

> Ground truth for the **AC Generator agent only**, scoped to the **filtering
> + filter-persistence** story (per the multi-feature pick-one decision in
> the Expected Output above). Email alerting and CSV export are out of scope
> for this story; they would each get their own AC ground truth as separate
> backlog items.

### Required AC Categories
- **filter by date range** — applying a date range filter restricts results to runs within that range
- **filter by environment** — selecting one or more environments (dev/staging/prod) restricts results to those environments
- **filter by test-suite name** — selecting/entering a suite name restricts results to that suite
- **filter persistence** — applied filter selections are restored after logout/login

### Expected AC Count
4 (±1). Below 3 means missing filter dimensions; above 5 usually indicates atomicity violations or out-of-scope leakage (CSV export, email alerting).

### Out-of-Scope Behaviours
The AC Generator should NOT produce ACs for any of:
- **email alerting on failure-rate threshold** — this is its own implied story, deliberately deferred
- **CSV export of filtered results** — also its own implied story
- specific filter-UI controls (dropdown vs autocomplete, multi-select vs chips) — not stated
- filter-combination semantics edge cases (AND vs OR across types) — not stated
- result count or pagination behaviour — not stated
- per-user filter sharing or team defaults — not stated

---

## Eval Notes
- The Requirements Analyst Agent should flag that this contains 3 separable user stories, not 1
- Persistent filter state is a sub-capability of filtering — not a 4th standalone feature
- A user story that tries to cover all 3 features in one is a failure — too broad, untestable as a single unit
- The Reviewer Agent should flag if ACs are missing for any filter type (date range, environment, suite name)
- The persistent filter state AC is a common miss — the Reviewer Agent should catch it if absent
- Email alerting and CSV export should NOT appear in the ACs for this story — they are out of scope for this scoped version
