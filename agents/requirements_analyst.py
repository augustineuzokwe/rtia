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


class AnalystOutput(BaseModel):
    """Structured output of the Requirements Analyst agent."""

    intent: str = Field(description="One or two sentences capturing the underlying goal.")
    actors: list[str] = Field(description="Distinct user roles or systems mentioned or implied.")
    ambiguities: list[str] = Field(description="Concrete clarifying questions a PO could answer.")


def analyze_requirement(
    requirement_text: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
) -> AnalystOutput:
    """Run the Requirements Analyst agent on a raw requirement.

    Reads ANTHROPIC_API_KEY from the environment (via python-dotenv in callers).
    Temperature defaults to 0 because we want stable, reproducible structure for
    downstream agents and eval comparison.
    """
    llm = ChatAnthropic(model=model, temperature=temperature)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text)),
    ]
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return AnalystOutput.model_validate(json.loads(raw))
