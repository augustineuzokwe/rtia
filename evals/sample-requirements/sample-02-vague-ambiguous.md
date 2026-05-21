# Sample Requirement 02 — Vague and Ambiguous

**Type:** Vague, ambiguous, stakeholder-style request
**Purpose:** Stress test for the Requirements Analyst Agent — it must surface ambiguities before the User Story Writer Agent proceeds. The generated user story should reflect only what can be inferred; the agent should flag what is missing.

---

## Raw Requirement

We need something to track our defects better. Right now everything is in spreadsheets and it's hard to see what's going on. Managers want visibility and the team wants to be able to update things quickly.

---

## Ambiguities RTIA Should Flag

The Requirements Analyst Agent should identify and surface at least the following before generating a user story:

1. **Who are the users?** "Managers" and "the team" are mentioned — are these two different roles with different views?
2. **What does "visibility" mean for managers?** A summary report? A live list? Email digest?
3. **What does "update things quickly" mean for the team?** Change status? Add comments? Assign owners?
4. **What is a "defect" in this context?** Is this integrated with an existing tool (Jira, GitHub Issues) or a standalone tracker?
5. **What does "track better" mean as a success measure?** Fewer missed defects? Faster resolution times?

---

## Expected Output (ground truth for eval dataset)

> **Note:** Because this requirement is deliberately vague, the expected output is intentionally minimal. The ground truth only reflects what can be directly inferred — no invented fields, no assumed data structures. Anything beyond this is scope creep.

### User Story (inferred from available information)
As a QA team member, I want to view and update defect records in a central tool, so that the team has a shared, up-to-date view of open defects without relying on spreadsheets.

### Acceptance Criteria

**Given** I am a logged-in QA team member
**When** I open the defect tracker
**Then** I see a list of open defects

**Given** I am viewing a defect
**When** I make a change to it
**Then** the change is saved and visible to other team members

**Given** I am a manager
**When** I open the defect tracker
**Then** I see a summary view of open defects

---

## Expected Analyst Output (per-agent ground truth for the Requirements Analyst)

> Ground truth for the **Analyst agent only**. Phrasing is illustrative — eval metrics
> compare against this fuzzily (judge for intent, set semantics for actors, category
> coverage for ambiguities), not by exact string match.

### Intent
Replace spreadsheet-based defect tracking with a shared, central tool so the QA team can update defects quickly and managers can see what is going on.

### Actors (expected set)
- team member
- manager

> **Note (post-Gemini calibration, #102):** the raw requirement uses "QA team" but Gemini's Analyst tends to drop the domain qualifier and emit just "Team member". The judge correctly declines the synonym match because structurally the labels differ. Relaxed to the unqualified label here so the metric tracks the role identity (team member vs manager) rather than the domain qualifier — which the requirement text doesn't strictly demand the Analyst echo.

### Ambiguity Categories
The Analyst should surface at least one ambiguity per category below. Wording will vary; the eval checks that each *category* is represented.

- **actor scoping** — are "managers" and "the team" separate roles with different views, or one combined role?
- **manager visibility shape** — what does "visibility" concretely mean (summary report, live list, digest, etc.)?
- **team update scope** — what does "update things quickly" cover (status change, comments, assignment, etc.)?
- **defect domain** — what counts as a "defect" here; is this standalone or integrated with an existing tracker (Jira, GitHub Issues, etc.)?
- **success measure** — what does "track better" mean as an outcome (fewer missed defects, faster resolution, etc.)?

At least one of these should be marked `severity: "critical"` — actor scoping is the strongest candidate, since the Story Writer cannot pick a single "As a..." role without an answer.

### Implied Stories
- (none expected) — vague but single-capability; the Analyst should not invent multi-feature splits to compensate for vagueness.

---

## Expected Acceptance Criteria (per-agent ground truth for the AC Generator)

> Ground truth for the **AC Generator agent only**. Because the input is
> vague, the per-agent AC ground truth is correspondingly minimal — the AC
> Generator should match the vagueness, not compensate for it by inventing
> specifics the requirement does not name.

### Required AC Categories
- **view defects list** — a logged-in QA team member can open the tracker and see a list of open defects
- **update a defect** — a change to a defect is saved and visible to others
- **manager summary view** — a manager sees a summary view of open defects (shape unspecified by design)

### Expected AC Count
3 (±1). More than 4 ACs on this sample almost certainly indicates invented scope (specific fields, statuses, severities not named in the requirement).

### Out-of-Scope Behaviours
The AC Generator should NOT produce ACs for any of:
- specific defect fields (status, severity, assignee, priority) — not stated
- update mechanics (in-place edit vs modal, optimistic UI) — not stated
- notification/alerting behaviour — not stated
- specific manager-summary contents (charts, counts by severity) — "visibility" is undefined in the input
- defect-creation flow — only "view + update + manager visibility" are in scope
- integration boundaries (Jira sync, GitHub Issues) — flagged as Analyst ambiguity, not yet PO-answered

---

## Eval Notes
- The Requirements Analyst Agent MUST flag ambiguities — a user story generated without surfacing any is a failure
- The ground truth ACs are intentionally vague to match the vague input — the agent should NOT invent specific fields (e.g. "status", "severity", "assignee") or technical behaviour (e.g. "without a page refresh") not stated in the requirement
- The Reviewer Agent should flag that the manager summary AC is underspecified — "visibility" is undefined — and recommend the PO clarifies before proceeding
- A correct agent response on this sample is: surface ambiguities → generate a minimal story → flag that ACs cannot be fully specified without answers to the ambiguity questions
- This sample tests the agent's ability to handle real-world messy input without hallucinating specificity
