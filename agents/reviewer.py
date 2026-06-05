"""Reviewer agent.

Fifth agent in the RTIA pipeline (after the Composer assembles the final
artifact). Consumes the original requirement text and the assembled
FinalUserStory, and emits a ReviewReport flagging coverage gaps, weak ACs,
and untestable criteria - the AI-QA learning capstone.

The Reviewer never mutates the artifact; it writes a ReviewReport into
pipeline state. The reviewer_node in graph.py appends a summary string
to FinalUserStory.metadata so the rendered markdown surfaces review notes.

Provider: Gemini 3.5 Flash. See ADR-0006 / ADR-0007.
"""

from __future__ import annotations

import json
import os
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

from agents._llm_errors import wrap_llm_exception
from agents._llm_utils import cached_invoke, coerce_response_text, strip_json_fence
from agents._logging import log_agent_invocation
from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_TOKENS_REVIEWER,
    OLLAMA_MODEL_ENV_VAR,
    ollama_remote_kwargs,
    prompt_hash,
    use_fake,
    use_ollama,
)
from agents.final_artifact import FinalUserStory
from agents.requirements_analyst import ImpliedStory
from prompts.reviewer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""


class ReviewReport(BaseModel):
    """Structured output of the Reviewer agent.

    Each list is empty when the artifact is solid - empty lists are a
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


def _format_deferred_stories(stories: list[ImpliedStory] | None) -> str:
    """Render the deferred-stories block for the Reviewer's user prompt.

    Empty list / None → ``(none)`` so the Reviewer treats the artifact as
    covering the full requirement (no story-level scoping happened, which
    is the common case for single-feature requirements).
    """
    if not stories:
        return "(none)"
    return "\n".join(f"- {s.title}: {s.summary}" for s in stories)


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
    deferred_stories: list[ImpliedStory] | None = None,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = MAX_OUTPUT_TOKENS_REVIEWER,
) -> ReviewReport:
    """Run the Reviewer on the assembled FinalUserStory.

    Inputs: the original requirement text (source of truth), the
    assembled artifact, and (optionally) any implied stories the PO
    deferred at the PO checkpoint. The Reviewer reads the full artifact
    including description, objective, assumptions, ACs, and test cases
    so it can detect gaps between the requirement and the artifact's
    coverage.

    ``deferred_stories`` is the scope-aware Reviewer's bug fix
    (Phase 15.1 / LEARNINGS #31). When the original requirement bundles
    several independent stories and the PO picks one at the PO
    checkpoint, the Reviewer would otherwise flag the deferred stories'
    behaviours as coverage gaps - a false-positive ``needs_work`` every
    time. Passing the deferred stories tells the Reviewer which
    behaviours were intentionally left out so it stops complaining
    about them. Callers that aren't routing through ``graph.py``'s
    ``reviewer_node`` can omit this - ``None`` and ``[]`` both render
    as ``(none)`` to the LLM.

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

    if use_fake():
        from agents._fake_llm import FakeChatModel, current_scenario

        llm = FakeChatModel(agent_name="reviewer")
        cache_model_id = f"fake:{current_scenario()}"
    elif use_ollama():
        from langchain_ollama import ChatOllama

        actual_model = os.environ.get(OLLAMA_MODEL_ENV_VAR, DEFAULT_OLLAMA_MODEL)
        llm = ChatOllama(
            model=actual_model,
            temperature=0 if temperature is None else temperature,
            num_predict=max_output_tokens,
            format="json",
            **ollama_remote_kwargs(),
        )
        cache_model_id = f"ollama:{actual_model}"
    else:
        llm = ChatGoogleGenerativeAI(**llm_kwargs)
        cache_model_id = f"google:{model}"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        requirement_text=requirement_text,
        deferred_stories=_format_deferred_stories(deferred_stories),
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
    # Phase 12.5 - see analyze_requirement for the rationale.
    with log_agent_invocation("reviewer", prompt_hash=_PROMPT_HASH) as rec:
        try:
            response = cached_invoke(
                llm,
                messages,
                model_id=cache_model_id,
                prompt_hash=_PROMPT_HASH,
                config=config,
            )
        except Exception as exc:
            raise wrap_llm_exception("reviewer", exc, retries_attempted=max_retries) from exc
        rec.record_response(response)
    raw = coerce_response_text(response.content)
    return ReviewReport.model_validate(json.loads(strip_json_fence(raw)))
