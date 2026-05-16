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

### User Story (inferred from available information)
As a QA team member, I want to view and update defect records in a central tool, so that the team has a shared, up-to-date view of open defects without relying on spreadsheets.

### Acceptance Criteria

**Given** I am a logged-in QA team member  
**When** I open the defect tracker  
**Then** I see a list of all open defects with their current status

**Given** I am viewing a defect  
**When** I update its status or add a comment  
**Then** the change is saved immediately and visible to other users without a page refresh

**Given** I am a manager  
**When** I open the defect tracker  
**Then** I see a summary view showing total open defects, defects by severity, and defects by assignee

---

## Eval Notes
- The Requirements Analyst Agent MUST flag ambiguities — a user story generated without surfacing any is a failure
- The User Story Writer Agent should not invent scope (e.g. email notifications, SLA tracking) not implied by the requirement
- The Reviewer Agent should flag that the ACs for "manager visibility" are underspecified without knowing what "visibility" means
- This sample tests the agent's ability to handle real-world messy input gracefully
