"""Tests for the PipelineRunner — start, resume, get_state, status mapping."""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.graph import _allowlisted_serde, build_pipeline
from api.models import ThreadStatus
from api.runner import PipelineRunner

FAKE_STORY = {
    "description": "As a user, I want to do thing X.",
    "objective": "Outcome Y is achieved.",
    "assumptions": [],
}
FAKE_AC = {"criteria": [{"given": "g", "when": "w", "then": "t"}]}
FAKE_TC = {
    "cases": [
        {"scenario": "Happy", "type": "happy_path", "steps": ["s"], "expected": "e"},
        {"scenario": "Bad", "type": "negative", "steps": ["s"], "expected": "e"},
    ]
}
FAKE_REVIEW = {
    "coverage_gaps": [],
    "weak_acs": [],
    "untestable_criteria": [],
    "recommendations": [],
    "overall_quality": "strong",
}


def _llm_factory(payload):
    def _factory(**_kwargs):
        m = MagicMock()
        m.invoke.return_value = AIMessage(content=json.dumps(payload))
        return m

    return _factory


def _patched_pipeline(analyst_payload):
    """Build a compiled pipeline backed by in-memory SQLite + mocked LLMs."""
    stack = ExitStack()
    stack.enter_context(
        patch(
            "agents.requirements_analyst.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(analyst_payload),
        )
    )
    stack.enter_context(
        patch(
            "agents.user_story_writer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(FAKE_STORY),
        )
    )
    stack.enter_context(
        patch("agents.ac_generator.ChatGoogleGenerativeAI", side_effect=_llm_factory(FAKE_AC))
    )
    stack.enter_context(
        patch(
            "agents.test_case_writer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(FAKE_TC),
        )
    )
    stack.enter_context(
        patch("agents.reviewer.ChatGoogleGenerativeAI", side_effect=_llm_factory(FAKE_REVIEW))
    )
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    saver = SqliteSaver(conn, serde=_allowlisted_serde())
    pipeline = build_pipeline(checkpointer=saver)
    return pipeline, stack


def test_runner_start_pauses_review_when_no_critical():
    analyst = {
        "intent": "Goal",
        "actors": ["User"],
        "ambiguities": [{"question": "Style?", "severity": "normal"}],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        state = runner.start("some requirement")

    assert state.status == ThreadStatus.PAUSED_REVIEW
    assert "rendered_artifact" in state.payload
    assert state.thread_id


def test_runner_start_pauses_po_on_critical_ambiguity():
    analyst = {
        "intent": "Goal",
        "actors": ["User"],
        "ambiguities": [{"question": "Role?", "severity": "critical"}],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        state = runner.start("some requirement")

    assert state.status == ThreadStatus.PAUSED_PO
    assert state.payload["critical_ambiguities"] == ["Role?"]


def test_runner_resume_completes_pipeline():
    analyst = {
        "intent": "Goal",
        "actors": ["User"],
        "ambiguities": [],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("requirement")
        assert paused.status == ThreadStatus.PAUSED_REVIEW

        done = runner.resume(paused.thread_id, {"accepted": True})

    assert done.status == ThreadStatus.DONE
    assert "final_artifact" in done.payload
    assert "rendered_artifact" in done.payload
    assert done.payload["review_report"]["overall_quality"] == "strong"


def test_runner_get_state_reads_snapshot():
    analyst = {"intent": "Goal", "actors": ["User"], "ambiguities": []}
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("requirement")
        snapshot = runner.get_state(paused.thread_id)

    assert snapshot.status == ThreadStatus.PAUSED_REVIEW
    assert "rendered_artifact" in snapshot.payload


def test_runner_render_markdown_returns_none_before_composer():
    analyst = {
        "intent": "Goal",
        "actors": ["User"],
        "ambiguities": [{"question": "Role?", "severity": "critical"}],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("requirement")
        # Paused at PO checkpoint — no final_artifact yet.
        assert runner.render_markdown(paused.thread_id) is None


def test_runner_done_fanout_payload_includes_selected_stories():
    """Phase 15.4 — multi-story → DONE_FANOUT with fan_out_stories payload.

    Analyst returns 3 implied stories. Runner pauses with fan-out shape.
    PO resumes selecting 2 of 3 → fan_out_node filters → DONE_FANOUT
    terminal state with exactly those 2 stubs in the payload. No
    final_artifact / review_report is produced.
    """
    analyst = {
        "intent": "multi-story req",
        "actors": ["User"],
        "ambiguities": [
            {
                "question": (
                    "This requirement implies 3 stories: [Story A, Story B, "
                    "Story C]. Which single story should this issue cover?"
                ),
                "severity": "critical",
            }
        ],
        "implied_stories": [
            {"title": "Story A", "summary": "first"},
            {"title": "Story B", "summary": "second"},
            {"title": "Story C", "summary": "third"},
        ],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("requirement")
        assert paused.status == ThreadStatus.PAUSED_PO
        assert paused.payload["mode"] == "fan_out"
        assert len(paused.payload["implied_stories"]) == 3

        done = runner.resume(
            paused.thread_id,
            {
                "selected_story_titles": ["Story A", "Story C"],
                "answers": {},
            },
        )

    assert done.status == ThreadStatus.DONE_FANOUT
    titles = [s["title"] for s in done.payload["fan_out_stories"]]
    assert titles == ["Story A", "Story C"]
    # Deep-flow fields must NOT be present.
    assert "final_artifact" not in done.payload
    assert "review_report" not in done.payload


def test_runner_done_fanout_with_no_matching_selection_returns_empty_done_fanout():
    """Phase 15.4 regression — when the PO's selection doesn't match any
    implied-story title, fan_out_node writes ``fan_out_stories=[]`` and
    the runner must still return DONE_FANOUT (not fall through to the
    deep-state path and KeyError on the missing final_artifact).

    Earlier draft of the runner used ``if result.get(\"fan_out_stories\"):``
    which is falsy for an empty list — that bug was caught live.
    """
    analyst = {
        "intent": "multi-story req",
        "actors": ["User"],
        "ambiguities": [{"question": "Which single story?", "severity": "critical"}],
        "implied_stories": [
            {"title": "Story A", "summary": "a"},
            {"title": "Story B", "summary": "b"},
        ],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("req")
        done = runner.resume(
            paused.thread_id,
            {"selected_story_titles": ["Unrelated Title"], "answers": {}},
        )

    assert done.status == ThreadStatus.DONE_FANOUT
    assert done.payload["fan_out_stories"] == []


def test_runner_done_fanout_empty_selection_keeps_all_stories():
    """Phase 15.4 / Q2 — empty selected_story_titles ⇒ fan out everything."""
    analyst = {
        "intent": "multi-story req",
        "actors": ["User"],
        "ambiguities": [{"question": "Which single story?", "severity": "critical"}],
        "implied_stories": [
            {"title": "Story A", "summary": "a"},
            {"title": "Story B", "summary": "b"},
        ],
    }
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("req")
        done = runner.resume(
            paused.thread_id,
            {"selected_story_titles": [], "answers": {}},
        )

    assert done.status == ThreadStatus.DONE_FANOUT
    titles = [s["title"] for s in done.payload["fan_out_stories"]]
    assert titles == ["Story A", "Story B"]


def test_runner_render_markdown_returns_string_when_done():
    analyst = {"intent": "Goal", "actors": ["User"], "ambiguities": []}
    pipeline, stack = _patched_pipeline(analyst)
    with stack:
        runner = PipelineRunner(pipeline)
        paused = runner.start("requirement")
        runner.resume(paused.thread_id, {"accepted": True})
        md = runner.render_markdown(paused.thread_id)

    assert md is not None
    assert "## Description" in md
