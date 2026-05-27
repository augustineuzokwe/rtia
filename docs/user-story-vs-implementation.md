# What belongs in the user story vs. what belongs to implementation

This guide draws the line between **detail that must reach the final user-story
artifact** and **detail the team owns at build time**. It exists because the
RTIA pipeline kept dropping the wrong things - see [LEARNINGS.md §28][learn28]
and Issue [#100][issue100] for the moment the gap was noticed.

This guide is referenced from the Story Writer and AC Generator system
prompts. Update both prompt files when this rule changes.

---

## The rule, in one line

> **A detail belongs in the story if a junior engineer who never saw the
> original requirement could miss it and ship something the
> requirement-writer would reject.** Everything else is implementation.

That's the whole bar. The rest of this document is the reasoning that makes
it usable in edge cases.

---

## What's always user value (must reach the artifact)

These types of detail are user-facing by definition. Dropping them = lost
user value, even if the artifact "looks complete."

1. **Named metrics or status outputs the user sees.**
   - "total tests, passed, failed, skipped" - the user sees these four
     specific numbers; collapsing to "summary of the run" loses the
     contract.
   - "open / closed / in-progress" status counts - the user expects exactly
     these labels.
2. **Named UX guarantees from the requirement.**
   - "without a full page reload" - that's a UX promise the user
     experiences. Dropping it lets the team build a flashing-and-jumping
     refresh that technically updates the data.
   - "no spinner / no loading indicator" - same shape.
3. **Named entry points or scoping the user controls.**
   - "for the selected project" - the story must say what gets shown.
     If selection is ambiguous, the ambiguity belongs to the PO, but the
     story still has to reflect the resolution.
4. **PO-confirmed specifics that resolved an ambiguity.**
   - If the Analyst surfaced `actor scoping` and the PO answered "team
     member, not manager" - the story MUST say "team member" explicitly.
     The Analyst-question-PO-answer round trip is wasted if the answer
     doesn't propagate.

---

## What's usually implementation (team owns it)

These types of detail are tools/mechanism choices. Specifying them in the
story is **over-reach** - it constrains the team unnecessarily.

1. **Storage / database / persistence choices.** "Use Postgres",
   "denormalise the table" - never in a story.
2. **UI framework or component library choices.** "Use Material UI dialogs",
   "build with React Server Components" - never in a story.
3. **Refresh / polling mechanism.** "Use Server-Sent Events" / "use a 30s
   polling loop" / "use WebSockets" - these are mechanisms. The *outcome*
   ("updates without a full page reload") is user value; the mechanism that
   delivers it is implementation.
4. **Specific error-retry behaviour.** "Retry 3 times with exponential
   backoff" - the *user experience* of failure (e.g. "I see a clear error
   message") may be user value; the retry policy isn't.
5. **Redirect targets after auth.** "Send to `/login?next=/dashboard`" -
   the team picks the URL.

---

## The borderline cases (these are where PO clarification matters)

The rule above settles most cases. A few categories are genuinely
ambiguous and SHOULD go to the PO:

| Category | Question to ask the PO |
|---|---|
| **Cadence numbers** (e.g. "every 30 seconds") | "Is 30 seconds the contract, or roughly real-time?" If the user expects 30s, it's user-facing. If "real-time enough" was the intent, the number is implementation. |
| **Payload sizes / limits** | "Does the user see a 'max 100 rows' warning, or does the team pick what fits?" |
| **Empty-state copy** | "When there are zero items, is the message specified, or do you trust the team?" |
| **Sort order / default filter** | "Newest-first by default - is that the contract, or is the team picking?" |

Default behaviour for the Analyst: when a borderline question's resolution
would change the user's perceived experience, flag it as a CRITICAL
ambiguity. When it wouldn't, default and move on (note in `assumptions`).

---

## Worked example: sample-01 ("real-time test run summary")

The raw requirement (excerpt):

> The QA Dashboard should show a real-time test run summary for a selected
> project. It should display the total number of tests, how many passed,
> how many failed, and how many were skipped in the most recent run. The
> data should refresh automatically every 30 seconds without a full page
> reload. Only authenticated users should be able to view the dashboard.

### What MUST be in the story

| Item | Why |
|---|---|
| The four named counts (total, passed, failed, skipped) | Named metrics the user sees - exactly the rule §1. |
| "without a full page reload" | Named UX guarantee - rule §2. The user experiences the smoothness. |
| "for a selected project" | Named scoping - rule §3. The user controls this. |
| Auth boundary (authenticated users only; unauthenticated redirect) | Named entry-point control - rule §3. The user's experience of being shut out is part of the contract. |
| PO's project-selection clarification (if surfaced) | Rule §4. The Analyst asked, the PO answered, the answer must propagate. |

### What is implementation (NOT in the story)

| Item | Why |
|---|---|
| WebSockets vs. polling vs. SSE for the refresh | Mechanism, not outcome. The story says "updates without full reload"; the team picks how. |
| Whether the redirect goes to `/login` or `/auth/sign-in` | URL is team's call. |
| Exact spinner / loading-indicator design | The story says nothing was specified, so the team designs it. |
| How the project is "selected" if the PO clarified "URL parameter" - the URL parameter name | The fact of URL-param selection is user-facing (it determines whether the user can bookmark a dashboard view); the parameter NAME is team's call. |

### What happens if you drop the wrong thing

This is the trap from [LEARNINGS.md §28][learn28]: an early RTIA artifact
collapsed "display total, passed, failed, skipped" to "summary of the most
recent test run." A junior engineer building from that summary alone could
ship a tile saying "47 tests" with no pass/fail split - and the
requirement-writer would reject it. That's how this guide got written.

---

## How to apply this when writing a story

1. **Start from the requirement text.** List every named noun, named
   number, named UX phrase. Each is a candidate for the story.
2. **For each, ask the one-line test:** could a junior engineer miss it
   and produce something the requirement-writer would reject?
3. **If yes → into the story.** Often verbatim, or as close to verbatim
   as fluent prose allows.
4. **If no → drop it.** It's implementation. The team will figure it out.
5. **For borderline cases → ask the PO.** Default conservatively when the
   answer is unclear: it's cheaper to include a detail that turns out to
   be implementation than to drop one that turns out to be user value.

---

## How to apply this when writing acceptance criteria

ACs are where user-facing details get tested. The same rule:

- Every named metric or status from the story should be asserted in
  some AC's `Then` clause (or coverable by a token-overlap eval, per
  `evals/tc_metrics.py:tc_coverage_breadth`).
- AC clauses about *how* something works (mechanism, timing precision,
  exact retry counts) are usually over-reach. Prefer "the test run
  summary updates without a full page reload" to "a WebSocket message
  arrives within 30s ± 200ms".

---

## When this guide is wrong

Two cases where the rule above bends:

1. **Compliance / regulatory requirements.** "Audit log all actions" can
   feel implementation-flavoured but is legally a user-facing contract.
   Treat compliance language as always user value.
2. **Performance budgets the user perceives.** "Loads in under 1
   second" - if the user *experiences* the speed, it's user value. If
   the number was an internal engineering target, it's implementation.

When in doubt: ask the PO. The cost of one extra clarifying question is
always less than the cost of a built feature the team has to redo.

---

[learn28]: ../LEARNINGS.md
[issue100]: https://github.com/augustineuzokwe/rtia/issues/100
