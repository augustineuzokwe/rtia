"""Requirements Analyst agent.

First node in the RTIA pipeline. Takes raw requirement text and returns a
structured analysis (intent, actors, ambiguities) for the User Story Writer
to consume.

Walking-skeleton scope: a single function, no LangGraph orchestration yet.
"""

from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from prompts.requirements_analyst_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3


class AnalystOutput(BaseModel):
    """Structured output of the Requirements Analyst agent."""

    intent: str = Field(description="One or two sentences capturing the underlying goal.")
    actors: list[str] = Field(description="Distinct user roles or systems mentioned or implied.")
    ambiguities: list[str] = Field(description="Concrete clarifying questions a PO could answer.")


def analyze_requirement(
    requirement_text: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_tokens: int | None = None,
) -> AnalystOutput:
    """Run the Requirements Analyst agent on a raw requirement.

    Reads ANTHROPIC_API_KEY from the environment (via python-dotenv in callers).

    LLM resilience knobs (all overridable per caller — a FastAPI endpoint with a
    tight SLO will set different values than a batch eval job):

    - temperature: sampling temperature. None means use the model's default.
      Some Anthropic models (e.g. Claude Opus 4.7) reject the temperature
      parameter entirely and only accept their built-in sampling, so we only
      forward it when explicitly set. Pass temperature=0 for older models
      where deterministic sampling is needed for reproducibility.
    - timeout: wall-clock seconds per Claude call. Caps stuck network requests.
    - max_retries: retries on transient errors (429, 5xx, network blips) with
      exponential backoff. NOTE: retries are silent — the caller (e.g. the API
      layer) should surface retry counts in logs/traces so latency spikes
      remain debuggable.
    - max_tokens: hard cap on response length. None means use the model's
      default. Set explicitly only if you need cost/latency protection;
      setting it too low will truncate the JSON and trigger a parse error.
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
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text)),
    ]
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return AnalystOutput.model_validate(json.loads(raw))
