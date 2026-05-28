"""FinalUserStory artifact: the v1 contract for what RTIA outputs.

Every agent in the pipeline contributes to ONE final artifact that a PO
or QA Lead pastes directly into a Jira ticket or GitHub Issue. The four
sections (Description, Objective, Acceptance Criteria, Test Cases) come
from different agents:

    Description         ← User Story Writer
    Objective           ← User Story Writer
    Acceptance Criteria ← AC Generator
    Test Cases          ← Test Case Writer

The Reviewer agent runs last and attaches notes (coverage gaps, weak
ACs) to the composed artifact without contributing a new section.

See `docs/adr-0004-final-artifact.md` for the contract-first design
rationale.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from agents._sanitize import DEFAULT_ALLOWED_LANGS, DEFAULT_MAX_CHARS, sanitize_artifact

TestCaseType = Literal["happy_path", "edge_case", "negative"]

_AC_PLACEHOLDER = (
    "_No acceptance criteria were produced for this story - check the run "
    "trace; the AC Generator agent should populate this section._"
)
_TEST_PLACEHOLDER = "_To be populated by the Test Case agent._"


class AcceptanceCriterion(BaseModel):
    """One Given/When/Then acceptance criterion.

    Populated by the AC Generator agent in Keep the shape
    minimal (three strings) - Cucumber/Gherkin compatibility comes
    naturally from the field names.
    """

    given: str = Field(description="Preconditions / context for this criterion.")
    when: str = Field(description="The action or trigger the criterion exercises.")
    then: str = Field(description="The observable outcome the criterion asserts.")

    def as_markdown_bullet(self) -> str:
        """Render as a single markdown list item with bold keywords."""
        return f"- **Given** {self.given}; **When** {self.when}; **Then** {self.then}"


class TestCase(BaseModel):
    """One concrete test case derived from an acceptance criterion.

    Populated by the Test Case agent. `type` lets the renderer
    group cases (happy path first, edge cases next, negatives last) and
    lets evals assert coverage breadth (metric).
    """

    # Tell pytest not to try to collect this as a test class - the name
    # "TestCase" looks like a pytest target. This is a Pydantic schema,
    # not a test fixture.
    __test__ = False

    scenario: str = Field(description="Short name for the scenario this test covers.")
    type: TestCaseType = Field(description="Coverage type - happy_path / edge_case / negative.")
    steps: list[str] = Field(description="Ordered concrete steps a tester executes.")
    expected: str = Field(description="The observable outcome the test asserts.")

    def as_markdown_section(self) -> str:
        """Render as a sub-section with the scenario name as a heading."""
        lines = [f"#### {self.scenario} _({self.type})_"]
        lines.append("Steps:")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(self.steps, start=1))
        lines.append(f"Expected: {self.expected}")
        return "\n".join(lines)


class FinalUserStory(BaseModel):
    """The v1 output artifact: one backlog-ready user story with 4 sections.

    Schema-frozen contract - every agent in the pipeline contributes to
    this shape, no agent produces a competing standalone output. Field
    additions are safe (Pydantic accepts them); field removals or renames
    break downstream consumers and must be coordinated with a version
    bump in `agents.graph.PIPELINE_STATE_VERSION`.
    """

    description: str = Field(description="What the role wants ('As a/an X, I want Y').")
    objective: str = Field(description="The value/outcome the role gets - no 'so that' prefix.")
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list,
        description="Given/When/Then criteria. Empty until AC Generator agent lands.",
    )
    test_cases: list[TestCase] = Field(
        default_factory=list,
        description="Concrete test cases. Empty until Test Case agent lands.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Defaults the Story Writer picked for normal-severity ambiguities.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form key/value pairs - model/prompt versions, review notes, etc.",
    )

    def as_markdown(
        self,
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        allowed_langs: frozenset[str] = DEFAULT_ALLOWED_LANGS,
    ) -> str:
        """Render as paste-ready Jira/GitHub Issue markdown.

         Sections with no content from their authoring agent show an
         explicit placeholder so the artifact's full shape is always
         visible - readers see what's coming, not just what's done.

         The rendered output is passed through ``sanitize_artifact``
        : strips ASCII control bytes and invisible / bidi-
         override Unicode, normalises fenced-code language tags against
         an allowlist, and caps the total length. Callers that need the
         SanitizeReport (LangGraph checkpoint observability, eval suite)
         should call ``sanitize_artifact`` directly instead - this method
         preserves its ``str`` return type so existing consumers don't
         break.
        """
        parts = [
            "## Description",
            self.description,
            "",
            "## Objective",
            self.objective,
            "",
            "## Acceptance Criteria",
        ]
        if self.acceptance_criteria:
            parts.extend(ac.as_markdown_bullet() for ac in self.acceptance_criteria)
        else:
            parts.append(_AC_PLACEHOLDER)
        parts.append("")
        parts.append("## Test Cases")
        if self.test_cases:
            for tc in self.test_cases:
                parts.append(tc.as_markdown_section())
                parts.append("")
        else:
            parts.append(_TEST_PLACEHOLDER)

        if self.assumptions:
            parts.append("")
            parts.append("## Assumptions")
            parts.extend(f"- {a}" for a in self.assumptions)

        if self.metadata:
            parts.append("")
            parts.append("## Metadata")
            parts.extend(f"- **{k}**: {v}" for k, v in self.metadata.items())

        rendered = "\n".join(parts)
        cleaned, _report = sanitize_artifact(
            rendered, max_chars=max_chars, allowed_langs=allowed_langs
        )
        return cleaned

    def as_json(self, *, indent: int | None = 2) -> str:
        """Render as lossless JSON. Round-trips via `model_validate_json`."""
        return json.dumps(self.model_dump(), indent=indent, ensure_ascii=False)
