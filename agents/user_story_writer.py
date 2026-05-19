"""User Story Writer agent.

Second agent in the RTIA pipeline. Consumes the Requirements Analyst's
structured output plus any PO answers collected at the checkpoint, and
emits a user story formatted to land directly in a Jira or GitHub Issue
backlog: Description + Objective sections, with the writer's defaults
for normal-severity ambiguities recorded as Assumptions for downstream
review.

This is the shape that maps cleanly to the future `FinalUserStory`
artifact (Phase 3 of the prod-readiness plan): description and objective
become the corresponding sections of the final artifact, with
Acceptance Criteria and Test Cases populated by later agents.
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
from agents.requirements_analyst import AnalystOutput
from prompts.user_story_writer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""


class UserStory(BaseModel):
    """A user story formatted for paste-into-backlog (Description + Objective)."""

    description: str = Field(
        description=(
            "What the role wants — 1-2 sentences starting with "
            "'As a/an {role}, I want {action}'. Article agreement is the writer's"
            " responsibility (use 'an' before a vowel-sound role)."
        )
    )
    objective: str = Field(
        description=(
            "Value or outcome the role gets, one sentence, WITHOUT a 'so that' "
            "prefix (the renderer supplies the section header)."
        )
    )
    assumptions: list[str] = Field(
        description="Defaults the writer picked for normal-severity ambiguities.",
    )

    # NOTE: rendering moved to `agents.final_artifact.FinalUserStory.as_markdown()`
    # in Phase 3. The composer node in `agents.graph` maps UserStory →
    # FinalUserStory; downstream consumers should render via the final
    # artifact, not directly off UserStory.


def _format_ambiguities(analyst_output: AnalystOutput) -> str:
    if not analyst_output.ambiguities:
        return "(none)"
    return "\n".join(f"- [{a.severity}] {a.question}" for a in analyst_output.ambiguities)


def _format_po_answers(po_answers: dict[str, str]) -> str:
    if not po_answers:
        return "(none)"
    return "\n".join(f"- Q: {q}\n  A: {a}" for q, a in po_answers.items())


def write_user_story(
    analyst_output: AnalystOutput,
    po_answers: dict[str, str],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_tokens: int | None = None,
) -> UserStory:
    """Run the User Story Writer agent.

    Inputs come from prior pipeline state, not raw text — the writer never
    sees the original requirement, only the Analyst's structured read of it
    plus the PO's clarifications. This is intentional: it forces the Analyst
    to be the single source of truth about what was asked, and gives the
    Story Writer a clean, validated contract to work against.

    Resilience knobs mirror `analyze_requirement` so both agents share one
    operational model. See that function's docstring for per-parameter notes.
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
        intent=analyst_output.intent,
        actors="\n".join(f"- {actor}" for actor in analyst_output.actors) or "(none)",
        ambiguities=_format_ambiguities(analyst_output),
        po_answers=_format_po_answers(po_answers),
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    # Attach prompt_hash to LangSmith trace metadata so every traced run is
    # attributable to the exact prompt version. See agents.config.prompt_hash.
    config = {"metadata": {"agent": "user_story_writer", "prompt_hash": _PROMPT_HASH}}
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return UserStory.model_validate(json.loads(raw))
