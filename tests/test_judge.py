"""Tests for the Faithfulness judge.

Mocks the LLM call — covers the judge's contract (prompt assembly, JSON
parsing, schema validation) without hitting the API.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from evals.judge import JudgeVerdict, judge_faithfulness

SAMPLE_INPUT = "Only authenticated users should be able to view the dashboard."
SAMPLE_OUTPUT = (
    '{"intent":"Let logged-in users see the dashboard.",'
    '"actors":["Authenticated user"],"ambiguities":[]}'
)

VALID_RESPONSE = {
    "score": 5,
    "reasoning": "Output makes no claims unsupported by the input.",
    "unsupported_claims": [],
}


def _mock_invoke(payload: dict | str):
    """Patch ChatAnthropic.invoke to return `payload` as the LLM response."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return patch(
        "evals.judge.ChatAnthropic.invoke",
        return_value=AIMessage(content=content),
    )


def test_returns_validated_judge_verdict():
    with _mock_invoke(VALID_RESPONSE):
        result = judge_faithfulness(SAMPLE_INPUT, SAMPLE_OUTPUT)

    assert isinstance(result, JudgeVerdict)
    assert result.score == 5
    assert result.unsupported_claims == []


def test_rejects_malformed_json():
    with _mock_invoke("not json at all"), pytest.raises(json.JSONDecodeError):
        judge_faithfulness(SAMPLE_INPUT, SAMPLE_OUTPUT)


def test_rejects_score_out_of_range():
    bad = {"score": 10, "reasoning": "x", "unsupported_claims": []}
    with _mock_invoke(bad), pytest.raises(ValidationError):
        judge_faithfulness(SAMPLE_INPUT, SAMPLE_OUTPUT)


def test_rejects_response_missing_required_field():
    bad = {"score": 4, "reasoning": "x"}  # missing 'unsupported_claims'
    with _mock_invoke(bad), pytest.raises(ValidationError):
        judge_faithfulness(SAMPLE_INPUT, SAMPLE_OUTPUT)
