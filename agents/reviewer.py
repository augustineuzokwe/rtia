"""Reviewer agent.

Fifth agent in the RTIA pipeline (after the Composer assembles the final
artifact). Consumes the original requirement text and the assembled
FinalUserStory, and emits a ReviewReport flagging coverage gaps, weak ACs,
and untestable criteria — the AI-QA learning capstone.

The Reviewer never mutates the artifact; it writes a ReviewReport into
pipeline state. The reviewer_node in graph.py appends a summary string
to FinalUserStory.metadata so the rendered markdown surfaces review notes.

Provider: Gemini 3.5 Flash. See ADR-0006 / ADR-0007.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from agents._llm_utils import coerce_response_text, strip_json_fence
from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    prompt_hash,
)
from agents.final_artifact import FinalUserStory
from prompts.reviewer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""


class ReviewReport(BaseModel):
    """Structured output of the Reviewer agent.

    Each list is empty when the artifact is solid — empty lists are a
    valid, correct response and mean the Reviewer found nothing to flag.
    """

    coverage_gaps: list[str] = Field(
        description=(
            "Behaviours explicitly stated in the requirement that are not "
            "covered by any acceptance criterion."
        ),
    )
    weak_acs: list[str] = Field(
        description=(
            "ACs whose 'then' clause is vague, omits named counts/values, "
            "or lacks a concrete observable assertion."
        ),
    )
    untestable_criteria: list[str] = Field(
        description=(
            "ACs that cannot be verified by a tester because the expected "
            "outcome is subjective or unmeasurable."
        ),
    )
    recommendations: list[str] = Field(
        description="Specific, actionable improvement suggestions.",
    )
    overall_quality: Literal["strong", "acceptable", "needs_work"] = Field(
        description=(
            "'strong' = no gaps, all behaviours covered. "
            "'acceptable' = minor specificity gaps but all major behaviours covered. "
            "'needs_work' = at least one stated behaviour has no AC."
        ),
    )


def _format_assumptions(artifact: FinalUserStory) -> str:
    if not artifact.assumptions:
        return "(none)"
    return "\n".join(f"- {a}" for a in artifact.assumptions)


def _format_acs(artifact: FinalUserStory) -> str:
    if not artifact.acceptance_criteria:
        return "(none)"
    return "\n".join(
        f"- Given {ac.given}; When {ac.when}; Then {ac.then}" for ac in artifact.acceptance_criteria
    )


def _format_test_cases(artifact: FinalUserStory) -> str:
    if not artifact.test_cases:
        return "(none)"
    parts = []
    for tc in artifact.test_cases:
        steps = " → ".join(tc.steps)
        parts.append(f"- [{tc.type}] {tc.scenario}: {steps} → expected: {tc.expected}")
    return "\n".join(parts)


def review_artifact(
    requirement_text: str,
    final_artifact: FinalUserStory,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = None,
) -> ReviewReport:
    """Run the Reviewer on the assembled FinalUserStory.

    Inputs: the original requirement text (source of truth) and the
    assembled artifact produced by the composer. The Reviewer reads the
    full artifact including description, objective, assumptions, ACs, and
    test cases so it can detect gaps between the requirement and the
    artifact's coverage.

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
        requirement_text=requirement_text,
        description=final_artifact.description,
        objective=final_artifact.objective,
        assumptions=_format_assumptions(final_artifact),
        acceptance_criteria=_format_acs(final_artifact),
        test_cases=_format_test_cases(final_artifact),
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    config = {"metadata": {"agent": "reviewer", "prompt_hash": _PROMPT_HASH}}
    response = llm.invoke(messages, config=config)
    raw = coerce_response_text(response.content)
    return ReviewReport.model_validate(json.loads(strip_json_fence(raw)))
