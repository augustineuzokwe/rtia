"""Test Case Writer agent.

Fourth agent in the RTIA pipeline (Analyst → Story Writer → AC Generator →
Test Case Writer). Consumes the User Story + the generated Acceptance
Criteria and emits the concrete test cases that fill the fourth section
of the FinalUserStory artifact.

Schema-wise it produces ``list[TestCase]`` — the same ``TestCase`` model
already declared on the FinalUserStory contract, so the composer just
slots the list straight into the artifact.

Provider: Gemini 2.5 Flash. See ADR-0006.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from agents._llm_utils import strip_json_fence
from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    prompt_hash,
)
from agents.final_artifact import AcceptanceCriterion, TestCase
from agents.user_story_writer import UserStory
from prompts.test_case_writer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""


class TestCaseWriterOutput(BaseModel):
    """Structured output of the Test Case Writer agent."""

    # Tell pytest not to collect this as a test class — its name starts with
    # "Test" but it's a Pydantic schema, not a fixture.
    __test__ = False

    cases: list[TestCase] = Field(
        description="Concrete test cases exercising the story's ACs.",
    )


def _format_assumptions(user_story: UserStory) -> str:
    if not user_story.assumptions:
        return "(none)"
    return "\n".join(f"- {a}" for a in user_story.assumptions)


def _format_acceptance_criteria(criteria: list[AcceptanceCriterion]) -> str:
    if not criteria:
        return "(none)"
    return "\n".join(f"- Given {c.given}; When {c.when}; Then {c.then}" for c in criteria)


def write_test_cases(
    user_story: UserStory,
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = None,
) -> TestCaseWriterOutput:
    """Run the Test Case Writer on a user story + its acceptance criteria.

    Inputs come from prior pipeline state, not raw text — the Test Case
    Writer never sees the original requirement or the Analyst output. Its
    sole sources of truth are the validated user story and the ACs the
    AC Generator already produced. Same contract pattern as upstream agents.

    Resilience knobs mirror ``analyze_requirement``. See
    ``agents.requirements_analyst.analyze_requirement`` for per-parameter notes.
    """
    llm_kwargs: dict[str, object] = {
        "model": model,
        "timeout": timeout,
        "max_retries": max_retries,
        "max_output_tokens": max_output_tokens,
    }
    if temperature is not None:
        llm_kwargs["temperature"] = temperature

    llm = ChatGoogleGenerativeAI(**llm_kwargs)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        description=user_story.description,
        objective=user_story.objective,
        assumptions=_format_assumptions(user_story),
        acceptance_criteria=_format_acceptance_criteria(acceptance_criteria),
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    config = {"metadata": {"agent": "test_case_writer", "prompt_hash": _PROMPT_HASH}}
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return TestCaseWriterOutput.model_validate(json.loads(strip_json_fence(raw)))
