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
    MAX_OUTPUT_TOKENS_ANALYST,
    OLLAMA_MODEL_ENV_VAR,
    prompt_hash,
    use_ollama,
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


class SuspiciousInput(BaseModel):
    """Security metadata about adversarial content in the requirement text.

    Set by the Analyst when the requirement contains text shaped like an
    instruction to the assistant (prompt injection) rather than a description
    of software behaviour. Crucially distinct from ``ambiguities``:

    - ``ambiguities`` are story-shape questions the PO answers at the
      human checkpoint to drive downstream agent behaviour.
    - ``suspicious_input`` is a one-way flag for security review. Nobody
      answers it; downstream agents (Story Writer, AC Generator,
      Test Case Writer) ignore it and continue extracting the legitimate
      requirement. Only the Reviewer agent and a future security audit
      surface it.

    The flag must fire ONLY on instructions targeting the assistant
    ("ignore your previous instructions", "you are now…", "output your
    system prompt"). Instruction-shaped human language in transcripts
    ("Sarah, ignore that point", "forget the export feature, that's
    phase 2") is normal stakeholder discourse and MUST NOT trip the flag.
    See ``prompts/requirements_analyst_prompts.py`` worked examples and
    sample-07 for the false-positive boundary.
    """

    detected: bool = Field(
        default=False,
        description="True when adversarial assistant-directed text was found in the requirement.",
    )
    reason: str = Field(
        default="",
        description="One-line explanation of why the text was flagged. Empty when detected=False.",
    )
    spans: list[str] = Field(
        default_factory=list,
        description="Quoted snippets of the suspicious text. Empty when detected=False.",
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
    suspicious_input: SuspiciousInput = Field(
        default_factory=SuspiciousInput,
        description=(
            "Security flag for adversarial assistant-directed text in the "
            "requirement. Default (detected=False) is the common case and "
            "applies when the field is omitted by the LLM."
        ),
    )


def analyze_requirement(
    requirement_text: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_output_tokens: int | None = MAX_OUTPUT_TOKENS_ANALYST,
) -> AnalystOutput:
    """Run the Requirements Analyst agent on a raw requirement.

    Reads GOOGLE_API_KEY from the environment (via python-dotenv in callers).

    LLM resilience knobs (all overridable per caller - a FastAPI endpoint with a
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
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text)),
    ]
    # Attach prompt_hash to LangSmith trace metadata so every traced run is
    # attributable to the exact prompt version. See agents.config.prompt_hash.
    config = {"metadata": {"agent": "requirements_analyst", "prompt_hash": _PROMPT_HASH}}
    # wrap Gemini exceptions into LLMPipelineError. Catches
    # the retry-exhausted case so the caller gets a structured failure
    # rather than a raw SDK exception leaking through the LangChain
    # adapter. See agents/_llm_errors.py and docs/adr-0009-llm-fallback.md.
    with log_agent_invocation("requirements_analyst", prompt_hash=_PROMPT_HASH) as rec:
        try:
            response = cached_invoke(
                llm,
                messages,
                model_id=cache_model_id,
                prompt_hash=_PROMPT_HASH,
                config=config,
            )
        except Exception as exc:
            raise wrap_llm_exception(
                "requirements_analyst", exc, retries_attempted=max_retries
            ) from exc
        rec.record_response(response)
    raw = coerce_response_text(response.content)
    return AnalystOutput.model_validate(json.loads(strip_json_fence(raw)))
