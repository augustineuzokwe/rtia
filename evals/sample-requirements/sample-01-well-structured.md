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

## Eval Notes
- RTIA output should contain all 3 ACs (auth redirect, data display, auto-refresh)
- A missing auto-refresh AC is a coverage gap the Reviewer Agent should catch
- Faithfulness: ACs should not introduce scope not present in the requirement (e.g. specific refresh UI indicator is not stated)
