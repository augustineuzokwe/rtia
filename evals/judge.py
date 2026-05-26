"""Gemini judge for deepeval metrics.

deepeval's default judge is OpenAI; this wrapper keeps the eval stack
on the same provider as the production agents (Gemini 2.5 Flash, see
ADR-0006). Subclasses ``DeepEvalBaseLLM`` so deepeval metrics can call
into it transparently.

Post-cutover the only judge calls left in the suite are classification
work (actor synonym tiebreak, ambiguity-category mapping, AC→category
classification). The GEval-style "did the model invent scope?" judges
were deleted along with their metrics — see ADR-0006 §"Dropped metrics".
"""

from __future__ import annotations

from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from agents.config import DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS


class GeminiJudge(DeepEvalBaseLLM):
    """Adapter so deepeval metrics can use Gemini as their judge model.

    deepeval's metric pipelines call ``generate(prompt, schema=...)`` when
    they want structured output; we forward that to LangChain's
    ``with_structured_output`` so verdict schemas are honoured.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model_id = model
        super().__init__(model=model)

    def load_model(self) -> ChatGoogleGenerativeAI:
        # Judge intentionally stays on Gemini even when
        # ``RTIA_LLM_PROVIDER=ollama`` swaps the production agents. The
        # local-model probe in §7.3 of the plan
        # ``~/.claude/plans/before-we-draft-adr-declarative-leaf.md``
        # compares Ollama-generated artifacts against the 2026-05-26
        # baseline ``docs/pipeline-baseline-2026-05-26.md`` — which was
        # Gemini-judged. Switching the judge too would move two
        # variables at once and the metric deltas could not be
        # attributed cleanly to the generator switch. Hold the judge
        # constant; vary only the generator.
        return ChatGoogleGenerativeAI(
            model=self._model_id,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
        )

    def get_model_name(self) -> str:
        return self._model_id

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        if schema is not None:
            structured = self.model.with_structured_output(schema)
            return structured.invoke(prompt)
        return self.model.invoke(prompt).content

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        if schema is not None:
            structured = self.model.with_structured_output(schema)
            return await structured.ainvoke(prompt)
        result = await self.model.ainvoke(prompt)
        return result.content
