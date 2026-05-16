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

1. **Filtering** — by date range, environment, test suite name
2. **Email alerting** — failure rate threshold trigger (>20%), recipient is QA Lead
3. **CSV export** — of filtered test results
4. **Persistent filter state** — saved per user account across sessions

These are 4 separable user stories. The Requirements Analyst Agent should flag this to the PO at the human checkpoint.

---

## Expected Output (ground truth for eval dataset)

> Note: The expected output below represents the correctly scoped single user story for **filtering only**, as if the PO decided to scope to one story per session. The other 3 features would become separate backlog items.

### User Story (scoped to filtering)
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

## Eval Notes
- The Requirements Analyst Agent should flag that this contains 4 features, not 1
- A user story that tries to cover all 4 features in one is a failure — too broad, untestable as a single unit
- The Reviewer Agent should flag if ACs are missing for any filter type (date range, environment, suite name)
- The persistent filter state AC is a common miss — the Reviewer Agent should catch it if absent
- Email alerting and CSV export should NOT appear in the ACs for this story — that is out of scope for the scoped version
