"""Test Case Writer agent.

Fourth agent in the RTIA pipeline (Analyst → Story Writer → AC Generator →
Test Case Writer). Consumes the User Story + the generated Acceptance
Criteria and emits the concrete test cases that fill the fourth section
of the FinalUserStory artifact.

Schema-wise it produces ``list[TestCase]`` — the same ``TestCase`` model
already declared on the FinalUserStory contract, so the composer just
slots the list straight into the artifact.

Provider choice — this is the only agent in the pipeline running on
Google Gemini (``gemini-2.5-flash``); the other three agents run on
Anthropic Claude. Rationale: Gemini's free tier covers workshop spend
for this single agent without invalidating the Claude-calibrated eval
baselines in ``evals/baselines.md``. If a second agent ever needs to
flip provider, extract a small ``build_chat_llm()`` helper in
``agents/config.py`` at that point — premature now (one consumer).
Anthropic-style ``cache_control`` blocks are intentionally absent —
Gemini's context caching has a different shape (separate
``client.caches.create`` call referenced via the ``cached_content``
kwarg), and the prompt is small enough that the simple no-cache path
is correct for now.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    prompt_hash,
)
from agents.final_artifact import AcceptanceCriterion, TestCase
from agents.user_story_writer import UserStory
from prompts.test_case_writer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
"""Gemini model for this agent.

Stable, free-tier-eligible (≥10 RPM / 250 RPD on Google AI Studio as of
2026-05-21). Verified against the live ``models.list`` endpoint before
selection. Bump cautiously — newer Gemini IDs (3.x) are preview-only
and behavior can shift without notice.
"""

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


def _strip_json_fence(raw: str) -> str:
    """Strip an optional ```json … ``` fence from Gemini's response.

    Gemini sometimes wraps JSON output in a markdown code fence despite a
    "JSON only, no markdown fences" instruction. Trim it defensively
    rather than fight the prompt — the inner JSON is what we want, and
    a fence on the boundary is harmless to remove.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Drop the first line (``` or ```json) and the trailing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def write_test_cases(
    user_story: UserStory,
    acceptance_criteria: list[AcceptanceCriterion],
    *,
    model: str = DEFAULT_GEMINI_MODEL,
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

    ``max_output_tokens`` (not ``max_tokens``) is the Gemini kwarg name —
    verified against ``ChatGoogleGenerativeAI.model_fields`` on 2026-05-21.
    Other resilience knobs (``timeout``, ``max_retries``) keep the same
    names and semantics as the Anthropic agents.
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
    return TestCaseWriterOutput.model_validate(json.loads(_strip_json_fence(raw)))
