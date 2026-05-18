"""LLM-as-judge primitives for grading agent outputs.

Walking-skeleton scope: one judge (faithfulness), one function. Built from
the same primitives as the Analyst (ChatAnthropic + Pydantic) so the
mechanics are visible — no framework abstraction. Anthropic-only; no
OpenAI key required (unlike DeepEval defaults).
"""

from __future__ import annotations

import json

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from prompts.judge_prompts import (
    FAITHFULNESS_SYSTEM_PROMPT,
    FAITHFULNESS_USER_PROMPT_TEMPLATE,
)

DEFAULT_JUDGE_MODEL = "claude-opus-4-7"
DEFAULT_JUDGE_TIMEOUT_SECONDS = 60.0
DEFAULT_JUDGE_MAX_RETRIES = 3


class JudgeVerdict(BaseModel):
    """Structured verdict from a faithfulness judge."""

    score: int = Field(
        ge=1,
        le=5,
        description="Integer 1-5 (5 = perfectly faithful, 1 = unfaithful).",
    )
    reasoning: str = Field(description="Short paragraph explaining the score.")
    unsupported_claims: list[str] = Field(
        description=(
            "Specific excerpts or paraphrases from the output that are not "
            "supported by the input. Empty list when score is 5."
        ),
    )


def judge_faithfulness(
    input_text: str,
    agent_output: str,
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    timeout: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_JUDGE_MAX_RETRIES,
) -> JudgeVerdict:
    """Grade whether `agent_output` makes only claims supported by `input_text`.

    Returns a JudgeVerdict with score (1-5), reasoning, and a list of
    unsupported claims if any.

    Reads ANTHROPIC_API_KEY from the environment (via python-dotenv in
    callers).

    Notes:
    - Uses the same ChatAnthropic primitives as the Analyst. No `temperature`
      forwarded (Opus 4.7 rejects it).
    - `agent_output` should be a string. For Pydantic models, pass
      `model.model_dump_json(indent=2)` so the judge sees readable JSON.
    """
    llm = ChatAnthropic(
        model=model,
        timeout=timeout,
        max_retries=max_retries,
    )
    messages = [
        SystemMessage(content=FAITHFULNESS_SYSTEM_PROMPT),
        HumanMessage(
            content=FAITHFULNESS_USER_PROMPT_TEMPLATE.format(
                input_text=input_text,
                agent_output=agent_output,
            )
        ),
    ]
    response = llm.invoke(messages)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return JudgeVerdict.model_validate(json.loads(raw))
