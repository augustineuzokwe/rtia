"""Tests for the RTIA pipeline graph.

Mocks the underlying LLM call so the test runs offline and deterministically.
Verifies the graph wires the Analyst node and routes state correctly.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage

from agents.graph import build_pipeline

FAKE_ANALYST_RESPONSE = {
    "intent": "Goal X",
    "actors": ["User"],
    "ambiguities": ["?"],
}


def test_pipeline_invokes_analyst_and_populates_state():
    """Compiled pipeline runs the Analyst node and merges its output into state."""
    with patch(
        "agents.requirements_analyst.ChatAnthropic.invoke",
        return_value=AIMessage(content=json.dumps(FAKE_ANALYST_RESPONSE)),
    ):
        pipeline = build_pipeline()
        result = pipeline.invoke({"requirement_text": "some requirement"})

    assert result["requirement_text"] == "some requirement"
    assert result["analyst_output"].intent == "Goal X"
    assert result["analyst_output"].actors == ["User"]
    assert result["analyst_output"].ambiguities == ["?"]
