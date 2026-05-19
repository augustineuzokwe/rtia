"""AC Generator agent.

Third agent in the RTIA pipeline (after Analyst → Story Writer). Consumes
the Story Writer's user-story output plus the Analyst's context, and emits
the Given/When/Then acceptance criteria that fill the third section of
the FinalUserStory artifact.

Schema-wise it produces ``list[AcceptanceCriterion]`` — the same
``AcceptanceCriterion`` model already declared on the FinalUserStory
contract, so the composer just slots the list straight into the artifact.

Resilience knobs (timeout / retries / prompt caching) mirror the Analyst
and Story Writer so the three agents share one operational model.
"""

from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    prompt_hash,
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
    max_tokens: int | None = None,
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
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        llm_kwargs["temperature"] = temperature

    llm = ChatAnthropic(**llm_kwargs)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        description=user_story.description,
        objective=user_story.objective,
        assumptions=_format_assumptions(user_story),
        intent=analyst_output.intent,
        actors=_format_actors(analyst_output),
        po_answers=_format_po_answers(po_answers),
    )
    # Cache the static system prompt — same pattern as Analyst and Story Writer.
    messages = [
        SystemMessage(
            content=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ]
        ),
        HumanMessage(content=user_prompt),
    ]
    # Attach prompt_hash to LangSmith trace metadata so every traced run is
    # attributable to the exact prompt version.
    config = {"metadata": {"agent": "ac_generator", "prompt_hash": _PROMPT_HASH}}
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return AcGeneratorOutput.model_validate(json.loads(raw))
