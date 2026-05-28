"""Fake-LLM provider for deterministic, zero-cost UI testing.

When ``RTIA_LLM_PROVIDER=fake`` is set, all five production agents
construct a :class:`FakeChatModel` instead of a real chat-LLM. The fake
reads canned JSON fixtures from
``tests/fixtures/llm/<scenario>/<agent_name>.json`` and returns them as
``AIMessage.content`` - the same surface the downstream Pydantic parsers
already handle (``coerce_response_text`` + ``strip_json_fence`` +
``model_validate``).

See [ADR-0015](../docs/adr-0015-fake-llm-provider.md) for the contract,
the initial scenarios, and the rationale for files-on-disk vs in-memory
dicts.

Underscore-prefixed module name marks it as project-private - nothing
outside ``agents/`` should import from it. Tests reach in via the
``RTIA_LLM_PROVIDER`` env var, not by importing this module directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

FAKE_SCENARIO_ENV_VAR = "RTIA_FAKE_SCENARIO"
"""Selects which fixture directory the fake reads from.

Valid values: see :data:`VALID_SCENARIOS`. Default: ``deep_clean``.
Validated at every ``.invoke()`` so a typo fails loudly the first time
an agent runs rather than silently returning the default-scenario fixture.
"""

DEFAULT_FAKE_SCENARIO = "deep_clean"

VALID_SCENARIOS = frozenset({"deep_clean", "deep_with_po", "split", "error"})
"""Scenarios shipped with v1.1.0 (see ADR-0015 §"Initial scenarios").

Adding a new scenario means: (a) drop fixtures under
``tests/fixtures/llm/<name>/``, (b) append the name here, (c) update
ADR-0015. The validator in :func:`current_scenario` enforces this set.
"""

# Repo-root-relative path to the fixture tree. The repo root is the
# parent of the ``agents/`` directory this file lives in.
_FIXTURE_ROOT_REL = Path("tests") / "fixtures" / "llm"


def _fixture_root() -> Path:
    return Path(__file__).resolve().parent.parent / _FIXTURE_ROOT_REL


def current_scenario() -> str:
    """Return the active scenario, validated against :data:`VALID_SCENARIOS`.

    Raises ``ValueError`` if ``RTIA_FAKE_SCENARIO`` holds an unknown value -
    silently falling back to the default would mask test-setup typos.
    """
    raw = os.environ.get(FAKE_SCENARIO_ENV_VAR, DEFAULT_FAKE_SCENARIO).strip().lower()
    if raw not in VALID_SCENARIOS:
        raise ValueError(
            f"Invalid {FAKE_SCENARIO_ENV_VAR}={raw!r}; valid values: {sorted(VALID_SCENARIOS)}"
        )
    return raw


class FakeChatModel:
    """LangChain-compatible fake chat model for deterministic testing.

    Implements just the surface :func:`agents._llm_utils.cached_invoke`
    and the downstream agent parsers need:

    - ``.invoke(messages, config=...)`` returns an ``AIMessage`` whose
      ``.content`` is the canned JSON string read from the fixture file.

    Bound to one ``agent_name`` at construction; the agent_name plus the
    process-wide ``RTIA_FAKE_SCENARIO`` env var picks the fixture file:

        tests/fixtures/llm/{scenario}/{agent_name}.json

    Stateless beyond the binding: every ``.invoke()`` re-reads the env
    var and the file, so tests can monkey-patch ``RTIA_FAKE_SCENARIO``
    between calls if they want to walk a thread through multiple states
    in one process. This is intentional - locking the scenario at
    construction time would have made it impossible for an E2E test to
    drive the same thread through (for example) ``deep_with_po`` and
    then ``error`` to verify the UI's resume-into-error path.

    Failure modes:

    - Invalid ``RTIA_FAKE_SCENARIO`` value → ``ValueError`` at invoke time.
    - Missing fixture file → ``FileNotFoundError`` naming both the
      scenario and the agent (silent fallback would mask test bugs).
    - ``error`` scenario for ``requirements_analyst`` → ``RuntimeError``
      to exercise the UI Error panel (ADR-0015 §"Initial scenarios").
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name

    def invoke(self, messages: Any, config: Any = None) -> AIMessage:
        """Return the canned fixture for ``(scenario, agent_name)``."""
        scenario = current_scenario()
        if scenario == "error" and self.agent_name == "requirements_analyst":
            # Controlled failure at the first agent so the rest of the
            # pipeline never runs and the UI lands on the Error panel.
            raise RuntimeError(
                f"FakeChatModel: controlled error for scenario={scenario!r}, "
                f"agent={self.agent_name!r} (ADR-0015 error scenario)."
            )
        path = _fixture_root() / scenario / f"{self.agent_name}.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"FakeChatModel fixture not found: scenario={scenario!r}, "
                f"agent={self.agent_name!r}, expected at {path}. "
                "Add the file or fix RTIA_FAKE_SCENARIO."
            )
        return AIMessage(content=path.read_text(encoding="utf-8"))
