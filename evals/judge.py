"""deepeval judge - Gemini by default, optionally Ollama for $0 stacks.

deepeval's default judge is OpenAI; this wrapper keeps the eval stack
on the same provider as the production agents (Gemini 3.5 Flash by
default, see ADR-0006/0007). Subclasses ``DeepEvalBaseLLM`` so deepeval
metrics can call into it transparently.

Post-cutover the only judge calls left in the suite are classification
work (actor synonym tiebreak, ambiguity-category mapping, AC→category
classification). The GEval-style "did the model invent scope?" judges
were deleted along with their metrics - see ADR-0006 §"Dropped metrics".

**Two modes:**

- *Default* (``RTIA_OLLAMA_JUDGE`` unset / falsy): judge runs on Gemini
  3.5 Flash. Apples-to-apples comparison against
  ``docs/pipeline-baseline-2026-05-26.md``. ~$0.01-0.02 per eval run.
- *Full-local* (``RTIA_OLLAMA_JUDGE=1``): judge runs through
  ``langchain-ollama``. Together with ``RTIA_LLM_PROVIDER=ollama`` this
  makes the eval suite cost zero API dollars. Useful when the question
  is "is RTIA usable end-to-end without any API spend?" rather than
  "how much does the generator alone degrade locally?"

The two switches are deliberately *independent* so a probe can vary
the generator without varying the judge - that was the §7.3 design
documented in ``docs/ollama-probe-2026-05-26.md``.
"""

from __future__ import annotations

import os
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OLLAMA_JUDGE_MODEL_ENV_VAR,
    use_ollama_judge,
)


class GeminiJudge(DeepEvalBaseLLM):
    """Adapter so deepeval metrics can use Gemini (or Ollama) as their judge.

    Class name is historical - the wrapper now handles both providers via
    :func:`load_model`. Kept ``GeminiJudge`` to avoid a churn-only rename
    across the call sites in ``evals/run_evals.py``.

    deepeval's metric pipelines call ``generate(prompt, schema=...)`` when
    they want structured output; we forward that to LangChain's
    ``with_structured_output`` so verdict schemas are honoured. Both
    ``ChatGoogleGenerativeAI`` and ``ChatOllama`` support that method;
    the Ollama implementation goes through JSON-mode constraints under
    the hood, which is the same defensive shape the ``strip_json_fence``
    helper already covers.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model_id = model
        super().__init__(model=model)

    def load_model(self) -> BaseChatModel:
        # By default the judge stays on Gemini even when
        # ``RTIA_LLM_PROVIDER=ollama`` swaps the production agents. The
        # §7.3 probe in ``docs/ollama-probe-2026-05-26.md`` compares
        # Ollama-generated artifacts against the Gemini-judged baseline,
        # so holding the judge constant is the methodological invariant.
        #
        # Opting into ``RTIA_OLLAMA_JUDGE=1`` flips this - used when the
        # operator wants a strictly $0 eval run and accepts that two
        # variables (generator + judge) are now varying together.
        if use_ollama_judge():
            from langchain_ollama import ChatOllama

            judge_model = os.environ.get(OLLAMA_JUDGE_MODEL_ENV_VAR) or DEFAULT_OLLAMA_MODEL
            return ChatOllama(
                model=judge_model,
                temperature=0,
                format="json",
            )
        return ChatGoogleGenerativeAI(
            model=self._model_id,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            max_retries=DEFAULT_MAX_RETRIES,
        )

    def get_model_name(self) -> str:
        if use_ollama_judge():
            judge_model = os.environ.get(OLLAMA_JUDGE_MODEL_ENV_VAR) or DEFAULT_OLLAMA_MODEL
            return f"ollama:{judge_model}"
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
