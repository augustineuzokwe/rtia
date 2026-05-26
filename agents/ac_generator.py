"""AC Generator agent.

Third agent in the RTIA pipeline (after Analyst → Story Writer). Consumes
the Story Writer's user-story output plus the Analyst's context, and emits
the Given/When/Then acceptance criteria that fill the third section of
the FinalUserStory artifact.

Schema-wise it produces ``list[AcceptanceCriterion]`` — the same
``AcceptanceCriterion`` model already declared on the FinalUserStory
contract, so the composer just slots the list straight into the artifact.

Provider: Gemini 2.5 Flash. See ADR-0006.
"""

from __future__ import annotations

import json
import os

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
    MAX_OUTPUT_TOKENS_AC_GENERATOR,
    OLLAMA_MODEL_ENV_VAR,
    prompt_hash,
    use_ollama,
)
from agents.final_artifact import AcceptanceCriterion
from agents.requirements_analyst import AnalystOutput
from agents.user_story_writer import UserStory
from prompts.ac_generator_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""


class AcGeneratorOutput(BaseModel):
    """Structured output of the AC Generator agent."""

    criteria: list[AcceptanceCriterion] = Field(
        description="Given/When/Then ACs covering the user story's stated behaviours.",
    )


def _format_assumptions(user_story: UserStory) -> str:
    if not user_story.assumptions:
        return "(none)"
    return "\n".join(f"- {a}" for a in user_story.assumptions)


def _format_actors(analyst_output: AnalystOutput) -> str:
    if not analyst_output.actors:
        return "(none)"
    return "\n".join(f"- {a}" for a in analyst_output.actors)


def _format_po_answers(po_answers: dict[str, str]) -> str:
    if not po_answers:
        return "(none)"
    return "\n".join(f"- Q: {q}\n  A: {a}" for q, a in po_answers.items())


def generate_acceptance_criteria(
    user_story: UserStory,
    analyst_output: AnalystOutput,
    po_answers: dict[str, str],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = MAX_OUTPUT_TOKENS_AC_GENERATOR,
) -> AcGeneratorOutput:
    """Run the AC Generator on a Story Writer output + Analyst context.

    Inputs come from prior pipeline state, not raw text — the AC Generator
    never sees the original requirement, only the validated story shape
    and the Analyst's structured read. Same contract pattern as the Story
    Writer: each agent has one upstream source of truth.

    Resilience knobs mirror ``analyze_requirement`` and ``write_user_story``.
    See ``agents.requirements_analyst.analyze_requirement`` for per-parameter
    notes.
    """
    llm_kwargs: dict[str, object] = {
        "model": model,
        "timeout": timeout,
        "max_retries": max_retries,
        "max_output_tokens": max_output_tokens,
    }
    if temperature is not None:
        llm_kwargs["temperature"] = temperature

    if use_ollama():
        from langchain_ollama import ChatOllama

        actual_model = os.environ.get(OLLAMA_MODEL_ENV_VAR, DEFAULT_OLLAMA_MODEL)
        llm = ChatOllama(
            model=actual_model,
            temperature=0 if temperature is None else temperature,
            num_predict=max_output_tokens,
            format="json",
        )
        cache_model_id = f"ollama:{actual_model}"
    else:
        llm = ChatGoogleGenerativeAI(**llm_kwargs)
        cache_model_id = f"google:{model}"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        description=user_story.description,
        objective=user_story.objective,
        assumptions=_format_assumptions(user_story),
        intent=analyst_output.intent,
        actors=_format_actors(analyst_output),
        po_answers=_format_po_answers(po_answers),
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    config = {"metadata": {"agent": "ac_generator", "prompt_hash": _PROMPT_HASH}}
    # Phase 12.5 — see analyze_requirement for the rationale.
    with log_agent_invocation("ac_generator", prompt_hash=_PROMPT_HASH) as rec:
        try:
            response = cached_invoke(
                llm,
                messages,
                model_id=cache_model_id,
                prompt_hash=_PROMPT_HASH,
                config=config,
            )
        except Exception as exc:
            raise wrap_llm_exception("ac_generator", exc, retries_attempted=max_retries) from exc
        rec.record_response(response)
    raw = coerce_response_text(response.content)
    return AcGeneratorOutput.model_validate(json.loads(strip_json_fence(raw)))
