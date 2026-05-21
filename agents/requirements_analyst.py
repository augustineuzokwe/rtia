"""Requirements Analyst agent.

First node in the RTIA pipeline. Takes raw requirement text and returns a
structured analysis (intent, actors, ambiguities) for the User Story Writer
to consume.

Provider: Gemini 2.5 Flash on Google AI Studio's free tier. See ADR-0006
for the cost-driven cutover from Claude Opus 4.7. All four agents share
this provider as of the cutover.
"""

from __future__ import annotations

import json
from typing import Literal

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
from prompts.requirements_analyst_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

_PROMPT_HASH = prompt_hash(SYSTEM_PROMPT, USER_PROMPT_TEMPLATE)
"""Stable identifier for this agent's prompt version (see agents.config.prompt_hash)."""

Severity = Literal["critical", "normal"]


class Ambiguity(BaseModel):
    """A clarifying question the Analyst surfaced about the requirement.

    The `severity` controls whether the PO checkpoint pauses for it:
    - "critical": the Story Writer cannot proceed without an answer; the
      PO checkpoint will pause the graph and wait for a response.
    - "normal": a reasonable default assumption will let the Story Writer
      continue; the question flows forward as a story assumption.
    """

    question: str = Field(description="The clarifying question itself.")
    severity: Severity = Field(
        description="'critical' pauses the PO checkpoint; 'normal' flows through."
    )


class ImpliedStory(BaseModel):
    """A single story implied by a multi-feature requirement.

    When the Analyst detects that one requirement encompasses multiple
    independent stories (e.g. "filter results AND export to CSV AND email
    on failure"), each is listed here and the Analyst additionally emits
    a CRITICAL ambiguity asking the PO to pick one. The pipeline then
    drafts only the chosen story; the rest are surfaced for the PO to
    process in separate runs.
    """

    title: str = Field(description="Short title for the implied story (e.g. 'Filter by date').")
    summary: str = Field(
        description="One sentence describing the story (action + role if implied)."
    )


class AnalystOutput(BaseModel):
    """Structured output of the Requirements Analyst agent."""

    intent: str = Field(description="One or two sentences capturing the underlying goal.")
    actors: list[str] = Field(description="Distinct user roles or systems mentioned or implied.")
    ambiguities: list[Ambiguity] = Field(
        description="Clarifying questions, each tagged with severity (critical or normal).",
    )
    implied_stories: list[ImpliedStory] = Field(
        default_factory=list,
        description=(
            "Populated when the requirement implies multiple independent stories. "
            "Empty list means single-story (the common case)."
        ),
    )


def analyze_requirement(
    requirement_text: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = None,
) -> AnalystOutput:
    """Run the Requirements Analyst agent on a raw requirement.

    Reads GOOGLE_API_KEY from the environment (via python-dotenv in callers).

    LLM resilience knobs (all overridable per caller — a FastAPI endpoint with a
    tight SLO will set different values than a batch eval job):

    - temperature: sampling temperature. None means use the model's default
      (Gemini's default is 1.0). Pass 0.0 if deterministic sampling is
      needed for a specific reproducibility scenario.
    - timeout: wall-clock seconds per call. Caps stuck network requests.
    - max_retries: retries on transient errors (429, 5xx, network blips) with
      exponential backoff. Silent at the SDK layer; surface retry counts in
      logs/traces so latency spikes remain debuggable.
    - max_output_tokens: hard cap on response length (the Gemini name; the
      Anthropic-era parameter was max_tokens). None means use the model's
      default. Set explicitly only for cost/latency protection; too low
      will truncate the JSON and trigger a parse error.
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
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text)),
    ]
    # Attach prompt_hash to LangSmith trace metadata so every traced run is
    # attributable to the exact prompt version. See agents.config.prompt_hash.
    config = {"metadata": {"agent": "requirements_analyst", "prompt_hash": _PROMPT_HASH}}
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return AnalystOutput.model_validate(json.loads(strip_json_fence(raw)))
