"""User Story Writer agent.

Second agent in the RTIA pipeline. Consumes the Requirements Analyst's
structured output plus any PO answers collected at the checkpoint, and
emits a single user story in "As a / I want / so that" form.

Normal-severity ambiguities are resolved by the writer with reasonable
defaults and recorded in `assumptions` for downstream review.
"""

from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.requirements_analyst import AnalystOutput
from prompts.user_story_writer_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3


class UserStory(BaseModel):
    """A single user story produced by the Story Writer."""

    role: str = Field(description="Primary human actor (chosen from analyst actors).")
    want: str = Field(description="Action or capability the role wants.")
    benefit: str = Field(description="Value the role gets from the capability.")
    assumptions: list[str] = Field(
        description="Defaults the writer picked for normal-severity ambiguities.",
    )

    def as_sentence(self) -> str:
        """Render the story in its canonical sentence form."""
        return f"As a {self.role}, I want {self.want}, so that {self.benefit}."


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
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return UserStory.model_validate(json.loads(raw))
