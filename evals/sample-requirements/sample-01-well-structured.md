# Sample Requirement 01 — Well-Structured

**Type:** Well-structured, clear, single feature
**Purpose:** Happy path test — RTIA should produce a clean user story and ACs with no ambiguity to resolve

---

## Raw Requirement

The QA Dashboard should show a real-time test run summary for a selected project.

It should display the total number of tests, how many passed, how many failed, and how many were skipped in the most recent run. The data should refresh automatically every 30 seconds without a full page reload.

Only authenticated users should be able to view the dashboard. Unauthenticated users should be redirected to the login page.

---

## Expected Output (ground truth for eval dataset)

### User Story
As a QA Lead, I want to see a real-time test run summary for my selected project on the dashboard, so that I can monitor the health of the latest test run without manually refreshing the page.

### Acceptance Criteria

**Given** I am an authenticated user on the QA Dashboard
**When** I select a project
**Then** I see the total number of tests, passed count, failed count, and skipped count for the most recent run

**Given** I am viewing the dashboard
**When** 30 seconds have elapsed since the last data load
**Then** the test run summary updates automatically without a full page reload

**Given** I am not authenticated
**When** I navigate to the dashboard URL
**Then** I am redirected to the login page and the dashboard content is not visible

---

## Expected Analyst Output (per-agent ground truth for the Requirements Analyst)

> Ground truth for the **Analyst agent only**. Phrasing is illustrative — eval metrics
> compare against this fuzzily (judge for intent, set semantics for actors, category
> coverage for ambiguities), not by exact string match.

### Intent
Let an authenticated QA user monitor the health of the most recent test run for a selected project on the dashboard, with the data refreshing on its own.

### Intent Key Terms
Load-bearing domain phrases (case-insensitive substring match). A good
paraphrase preserves these even when verbs and connectives change.

- authenticated
- test run
- dashboard
- project
- refresh

### Actors (expected set)
- authenticated user
- unauthenticated user

> **Note (post-Gemini calibration, #102):** the raw requirement says only "authenticated users" — the "QA Lead" qualifier was an inference from the dashboard's domain context. Demanding the Analyst echo that qualifier was over-fitting to Claude's tendency to over-qualify roles. The judge's synonym match correctly declined "authenticated user" ≈ "QA Lead (authenticated user)" because they're structurally different labels. Relaxed to match the requirement text.

### Ambiguity Categories
- **project selection mechanism** — the requirement says the dashboard shows a summary "for a selected project" but never specifies HOW selection happens (dropdown? URL parameter? remembered last-used? default to the user's only project?). This is a story-shape question — it changes the user's flow into the feature, not just the UI polish. A disciplined Analyst is expected to surface it as a critical ambiguity. This category was added in the post-Gemini calibration: the question is genuinely worth asking, and forcing the Analyst to suppress it would penalise legitimate scope-shape inquiry.

Items that would still indicate a discipline failure (implementation/UX detail the prompt's scope rule forbids — these are NOT categories, just illustrative anti-patterns): specific refresh-indicator UI (spinner, "last updated" timestamp), retry-on-network-error behaviour, redirect-target choice after successful login.

### Implied Stories
- (none expected) — single-story requirement.

---

## Expected Acceptance Criteria (per-agent ground truth for the AC Generator)

> Ground truth for the **AC Generator agent only**. Phrasing is illustrative —
> eval metrics check that each category below is covered by at least one
> produced AC (fuzzy match), not exact Given/When/Then wording.

### Required AC Categories
The AC Generator should produce at least one AC for each:

- **summary display** — selecting a project surfaces totals + pass/failed/skipped counts for the most recent run
- **auto-refresh cadence** — summary updates without a full page reload on the stated interval (30s)
- **access boundary** — unauthenticated users are redirected to the login page; the dashboard is not visible to them

### Expected AC Count
3 (±1). Below 3 means a missing category; above 4 usually means atomicity violations or invented scope.

### Out-of-Scope Behaviours
The AC Generator should NOT produce ACs for any of:
- specific refresh-indicator UI (spinner, "last updated" timestamp)
- network error or backend-unavailable handling
- auto-refresh pause when the tab is inactive
- redirect-target choice after successful login
- empty-state rendering when no test runs exist

---

## Eval Notes
- RTIA output should contain all 3 ACs (auth redirect, data display, auto-refresh)
- A missing auto-refresh AC is a coverage gap the Reviewer Agent should catch
- Faithfulness: ACs should not introduce scope not present in the requirement (e.g. specific refresh UI indicator is not stated)
- Analyst eval: empty `ambiguities` is the correct, expected output here; flagging UX detail is a discipline failure, not a thoroughness win.
