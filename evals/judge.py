"""Claude judge for deepeval metrics.

deepeval's default judge is OpenAI; this wrapper keeps the eval stack
Claude-only (single-provider for v1, matching agent code). Subclasses
``DeepEvalBaseLLM`` so GEval and other deepeval metrics can call into it
transparently.
"""

from __future__ import annotations

from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from agents.config import DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS


class ClaudeJudge(DeepEvalBaseLLM):
    """Adapter so deepeval metrics can use Claude as their judge model.

    deepeval's metric pipelines call ``generate(prompt, schema=...)`` when
    they want structured output; we forward that to LangChain's
    ``with_structured_output`` so GEval's verdict schema is honoured.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model_id = model
        super().__init__(model=model)

    def load_model(self) -> ChatAnthropic:
        return ChatAnthropic(
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
