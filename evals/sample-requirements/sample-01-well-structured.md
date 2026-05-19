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

### Actors (expected set)
- QA Lead (authenticated user)
- unauthenticated user

### Ambiguity Categories
- (none expected) — the requirement is well-scoped, single-feature, and names its role, behaviour, refresh cadence, and auth boundary explicitly. The Analyst should return an empty `ambiguities` list. Any flagged item is most likely an implementation/UX detail the prompt's scope rule forbids (e.g. refresh indicator, retry on network error, redirect target after login).

### Implied Stories
- (none expected) — single-story requirement.

---

## Eval Notes
- RTIA output should contain all 3 ACs (auth redirect, data display, auto-refresh)
- A missing auto-refresh AC is a coverage gap the Reviewer Agent should catch
- Faithfulness: ACs should not introduce scope not present in the requirement (e.g. specific refresh UI indicator is not stated)
- Analyst eval: empty `ambiguities` is the correct, expected output here; flagging UX detail is a discipline failure, not a thoroughness win.
