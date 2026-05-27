"""Tests for the RTIA pipeline graph.

Mocks the underlying LLM calls so tests run offline and deterministically.
Covers graph wiring: happy-path flow-through through the Story Writer,
pause at critical ambiguities, and resume with PO answers (also routing
through the Story Writer).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents.graph import (
    _CHECKPOINT_ALLOWLIST,
    PIPELINE_STATE_VERSION,
    PipelineState,
    _allowlisted_serde,
    build_pipeline,
)


def _test_checkpointer() -> SqliteSaver:
    """Build an in-memory SQLite checkpointer for unit tests.

    `check_same_thread=False` lets LangGraph use the saver from its worker
    threads. The allowlisted serde matches production so tests exercise
    the same serialization path that the demo runs in.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return SqliteSaver(conn, serde=_allowlisted_serde())


def _fake_analyst_response(ambiguities: list[dict]) -> dict:
    """Build a minimal Analyst response payload with the given ambiguities."""
    return {
        "intent": "Goal X",
        "actors": ["User"],
        "ambiguities": ambiguities,
    }


FAKE_STORY_RESPONSE = {
    "description": "As a user, I want to do thing X.",
    "objective": "Outcome Y is achieved.",
    "assumptions": [],
}

FAKE_AC_RESPONSE = {
    "criteria": [
        {"given": "the user is logged in", "when": "they do X", "then": "outcome Y appears"}
    ]
}

FAKE_TEST_CASE_RESPONSE = {
    "cases": [
        {
            "scenario": "Happy path - user does X",
            "type": "happy_path",
            "steps": ["Log in as a user.", "Do thing X."],
            "expected": "Outcome Y is observed.",
        },
        {
            "scenario": "Unauthenticated user blocked",
            "type": "negative",
            "steps": ["Open the page without logging in."],
            "expected": "The user is redirected to login before doing X.",
        },
    ]
}

FAKE_REVIEW_RESPONSE = {
    "coverage_gaps": [],
    "weak_acs": [],
    "untestable_criteria": [],
    "recommendations": [],
    "overall_quality": "strong",
}


def _llm_factory(payload: dict):
    """Build a fake ChatGoogleGenerativeAI class whose instances return `payload`."""

    def _factory(**_kwargs):
        instance = MagicMock()
        instance.invoke.return_value = AIMessage(content=json.dumps(payload))
        return instance

    return _factory


def _mock_pipeline_llms(
    analyst_payload: dict,
    story_payload: dict = FAKE_STORY_RESPONSE,
    ac_payload: dict = FAKE_AC_RESPONSE,
    test_case_payload: dict = FAKE_TEST_CASE_RESPONSE,
    review_payload: dict = FAKE_REVIEW_RESPONSE,
):
    """Patch each agent's `ChatGoogleGenerativeAI` symbol with its own fake.

    The LangChain wrapper is the same class object in every agent module, so
    patching `.invoke` on the class would bleed across agents. Patching the
    symbol at each import site keeps the mocks independent (see feedback
    memory `feedback_mock_class_per_module`).
    """
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
            side_effect=_llm_factory(story_payload),
        )
    )
    stack.enter_context(
        patch("agents.ac_generator.ChatGoogleGenerativeAI", side_effect=_llm_factory(ac_payload))
    )
    stack.enter_context(
        patch(
            "agents.test_case_writer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(test_case_payload),
        )
    )
    stack.enter_context(
        patch(
            "agents.reviewer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(review_payload),
        )
    )
    return stack


def test_pipeline_flows_through_when_no_critical_ambiguities():
    """No critical ambiguities → PO checkpoint flows through. Story Review still pauses;
    resuming with accept lands the full pipeline."""
    analyst = _fake_analyst_response([{"question": "Style preference?", "severity": "normal"}])

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-flow-through"}}
        first = pipeline.invoke({"requirement_text": "some requirement"}, config=config)
        # The Story Review Checkpoint always pauses for explicit accept.
        assert "rendered_artifact" in first["__interrupt__"][0].value
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    assert result["analyst_output"].intent == "Goal X"
    assert result["po_answers"] == {}
    assert result["user_story"].description.startswith("As a user, I want")
    assert "thing X" in result["user_story"].description
    # Composer runs after Story Review accept - final_artifact populated
    assert "final_artifact" in result
    assert result["final_artifact"].description == result["user_story"].description
    # AC Generator (Phase 8) populates this slot from FAKE_AC_RESPONSE.
    assert len(result["final_artifact"].acceptance_criteria) == 1
    assert result["final_artifact"].acceptance_criteria[0].then == "outcome Y appears"
    # Test Case Writer (Phase 9) populates this slot from FAKE_TEST_CASE_RESPONSE.
    assert len(result["final_artifact"].test_cases) == 2
    types = {tc.type for tc in result["final_artifact"].test_cases}
    assert {"happy_path", "negative"} <= types
    # Reviewer (Phase 10) populates this slot from FAKE_REVIEW_RESPONSE.
    assert "review_report" in result
    assert result["review_report"].overall_quality == "strong"
    assert result["review_report"].coverage_gaps == []
    # Reviewer appends a summary to the artifact's metadata.
    assert "review_summary" in result["final_artifact"].metadata
    assert "[strong]" in result["final_artifact"].metadata["review_summary"]


def test_reviewer_summary_includes_all_report_categories():
    """All four ReviewReport list categories must surface in metadata['review_summary'].

    Regression guard - an earlier draft skipped untestable_criteria from the
    summary string, which would silently hide a real review finding from
    anyone reading only the rendered markdown.
    """
    analyst = _fake_analyst_response([])
    review_payload = {
        "coverage_gaps": ["No AC covers feature Z."],
        "weak_acs": ["AC1 - vague then-clause"],
        "untestable_criteria": ["AC2 - subjective outcome"],
        "recommendations": ["Add concrete AC for Z."],
        "overall_quality": "needs_work",
    }

    with _mock_pipeline_llms(analyst, review_payload=review_payload):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-review-summary"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    summary = result["final_artifact"].metadata["review_summary"]
    assert "[needs_work]" in summary
    assert "gaps:" in summary
    assert "weak ACs:" in summary
    assert "untestable:" in summary
    assert "recommendations:" in summary


def test_pipeline_pauses_when_critical_ambiguity_present():
    """A critical ambiguity should pause the graph before the Story Writer."""
    analyst = _fake_analyst_response(
        [
            {"question": "What role is the actor?", "severity": "critical"},
            {"question": "Display style?", "severity": "normal"},
        ]
    )

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-pause"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" in result
    interrupt_value = result["__interrupt__"][0].value
    assert interrupt_value["critical_ambiguities"] == ["What role is the actor?"]
    # Story Writer must not have run yet.
    assert "user_story" not in result


def test_pipeline_resumes_into_story_writer_with_po_answers():
    """Resuming the paused graph should populate po_answers and produce a story."""
    analyst = _fake_analyst_response(
        [{"question": "What role is the actor?", "severity": "critical"}]
    )

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-resume"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)

        # First resume: provide PO answers → pipeline runs Story Writer
        # → pauses at Story Review Checkpoint.
        answers = {"What role is the actor?": "QA Lead"}
        intermediate = pipeline.invoke(Command(resume=answers), config=config)
        assert "rendered_artifact" in intermediate["__interrupt__"][0].value

        # Second resume: accept the rendered story → Composer runs → END.
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    assert result["po_answers"] == answers
    assert result["user_story"].description.startswith("As a user, I want")
    # Composer runs after Story Review accept
    assert "final_artifact" in result
    assert result["final_artifact"].description == result["user_story"].description


# ---------------------------------------------------------------------------
# Schema stability + checkpointer-injection contract
# ---------------------------------------------------------------------------


def test_pipeline_state_v1_schema_is_stable():
    """If a v1 field is removed or renamed, this test fails LOUDLY.

    Adding fields is safe (PipelineState is total=False). Removing or
    renaming requires bumping PIPELINE_STATE_VERSION and writing a
    migration ADR - silent state-shape drift after PR #40's checkpoint
    landed would corrupt every paused thread on disk.
    """
    expected_v1_fields = {
        "requirement_text",
        "analyst_output",
        "po_answers",
        "user_story",
        "test_cases",
        "final_artifact",
    }
    actual_fields = set(PipelineState.__annotations__.keys())
    missing = expected_v1_fields - actual_fields
    assert not missing, (
        f"Pipeline state v1 fields {missing} were removed. "
        f"Either restore them or bump PIPELINE_STATE_VERSION "
        f"(currently {PIPELINE_STATE_VERSION}) with a migration ADR."
    )


def test_checkpoint_allowlist_covers_all_pydantic_state_types():
    """The msgpack allowlist must include every Pydantic type we put in state.

    A missing allowlist entry resurfaces the noisy
    'Deserializing unregistered type ...' warning that PR #59 fixed.
    This test catches it the moment a new Pydantic type slips into state.
    """
    allowed = {(mod, cls) for mod, cls in _CHECKPOINT_ALLOWLIST}
    required = {
        ("agents.requirements_analyst", "AnalystOutput"),
        ("agents.requirements_analyst", "Ambiguity"),
        ("agents.requirements_analyst", "ImpliedStory"),
        ("agents.user_story_writer", "UserStory"),
        ("agents.test_case_writer", "TestCaseWriterOutput"),
        ("agents.final_artifact", "FinalUserStory"),
        ("agents.final_artifact", "AcceptanceCriterion"),
        ("agents.final_artifact", "TestCase"),
    }
    missing = required - allowed
    assert not missing, f"Pydantic types not in checkpoint allowlist: {missing}"


def test_build_pipeline_accepts_injected_checkpointer():
    """Tests + future API/UI use cases must be able to inject a checkpointer."""
    pipeline = build_pipeline(checkpointer=_test_checkpointer())
    # Smoke check: the compiled pipeline has nodes wired.
    assert pipeline is not None


# ---------------------------------------------------------------------------
# Story Review Checkpoint (Phase 4)
# ---------------------------------------------------------------------------


def test_pipeline_pauses_at_story_review_checkpoint():
    """After Story Writer, the graph pauses with a rendered-artifact preview.

    Distinguishable from the PO Checkpoint by interrupt payload keys -
    PO emits `critical_ambiguities`; Story Review emits
    `rendered_artifact` + `description` + `objective`.
    """
    analyst = _fake_analyst_response([])  # no critical ambiguities → goes straight through PO
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-pause"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert "rendered_artifact" in payload
    assert "description" in payload and "objective" in payload
    # PO Checkpoint should NOT have been the one that fired here.
    assert "critical_ambiguities" not in payload
    # Composer must not have run yet.
    assert "final_artifact" not in result


def test_story_review_accept_passes_through():
    """Resuming with `{'accepted': True}` uses Story Writer's output unchanged."""
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-accept"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    artifact = result["final_artifact"]
    # Story Writer's mock produced "As a user, I want to do thing X." -
    # accept path preserves it unchanged.
    assert artifact.description == "As a user, I want to do thing X."
    assert artifact.objective == "Outcome Y is achieved."


def test_story_review_override_replaces_description_and_objective():
    """Resuming with overrides replaces description/objective in the final artifact."""
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-override"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(
            Command(
                resume={
                    "accepted": False,
                    "description": "As a manager, I want overridden text.",
                    "objective": "Overridden value statement.",
                }
            ),
            config=config,
        )

    assert "__interrupt__" not in result
    artifact = result["final_artifact"]
    assert artifact.description == "As a manager, I want overridden text."
    assert artifact.objective == "Overridden value statement."


def test_story_review_override_partial_keeps_non_overridden_fields():
    """Empty / missing override fields fall back to the Story Writer's value.

    Pins the partial-override semantics: a PO who only wants to tweak
    the description shouldn't lose the writer's objective by omitting
    it.
    """
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-partial"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(
            Command(
                resume={
                    "accepted": False,
                    "description": "Only the description changed.",
                    # objective omitted → keeps writer's "Outcome Y is achieved."
                }
            ),
            config=config,
        )

    artifact = result["final_artifact"]
    assert artifact.description == "Only the description changed."
    assert artifact.objective == "Outcome Y is achieved."


def test_reviewer_node_passes_empty_deferred_for_single_implied_story():
    """Phase 15.4 - under split routing, the Reviewer only runs for
    single-implied-story (or zero) requirements. In those cases the
    deferred list is always empty - the 15.1 scope-aware Reviewer
    plumbing stays wired but degenerates to a no-op. Pin that contract.

    Multi-implied-story (≥ 2) requirements branch to split_node, so
    the Reviewer never runs at all on them - that property is covered
    by ``test_multi_story_branches_to_split_skipping_deep_nodes``.
    """
    from unittest.mock import patch

    captured_deferred_titles: list[str] = []

    def _fake_review_artifact(_req, _artifact, *, deferred_stories=None, **_kw):
        from agents.reviewer import ReviewReport

        captured_deferred_titles.extend(s.title for s in (deferred_stories or []))
        return ReviewReport(
            coverage_gaps=[],
            weak_acs=[],
            untestable_criteria=[],
            recommendations=[],
            overall_quality="strong",
        )

    analyst_payload = {
        "intent": "single-story req",
        "actors": ["User"],
        "ambiguities": [],
        "implied_stories": [
            {"title": "The only story", "summary": "the only story summary"},
        ],
    }

    with (
        _mock_pipeline_llms(analyst_payload),
        patch("agents.graph.review_artifact", side_effect=_fake_review_artifact),
    ):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-1-story-reviewer"}}
        pipeline.invoke({"requirement_text": "req"}, config=config)
        pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert captured_deferred_titles == []


def test_deferred_implied_stories_matches_bidirectionally():
    """Phase 15 hotfix: PO answer ⊂ title OR title ⊂ answer both count as picked.

    Earlier single-direction match silently misclassified the picked
    story when POs typed a shorter variant of the title (e.g. 'Slack
    notifications' when the title was 'Slack notifications for
    auto-quarantine changes'). Pin all four realistic answer shapes
    against the same stories so a regression to single-direction
    matching surfaces immediately.
    """
    from agents.graph import deferred_implied_stories
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    stories = [
        ImpliedStory(title="Slack notifications for auto-quarantine changes", summary="s"),
        ImpliedStory(title="Quarantined tests dashboard", summary="d"),
        ImpliedStory(title="Audit log for quarantine actions", summary="a"),
    ]
    base = {
        "analyst_output": AnalystOutput(
            intent="x", actors=["u"], ambiguities=[], implied_stories=stories
        ),
    }

    cases = {
        # answer verbatim → picks Slack story
        "Slack notifications for auto-quarantine changes": [
            "Quarantined tests dashboard",
            "Audit log for quarantine actions",
        ],
        # answer SHORTER than title → still picks Slack (answer ⊂ title)
        "Slack notifications": [
            "Quarantined tests dashboard",
            "Audit log for quarantine actions",
        ],
        # answer LONGER than title with extra context → picks Dashboard (title ⊂ answer)
        "Pick Quarantined tests dashboard for now": [
            "Slack notifications for auto-quarantine changes",
            "Audit log for quarantine actions",
        ],
        # answer matches nothing → ALL stories stay deferred
        "completely unrelated text": [
            "Slack notifications for auto-quarantine changes",
            "Quarantined tests dashboard",
            "Audit log for quarantine actions",
        ],
    }
    for answer, expected_deferred_titles in cases.items():
        state = {**base, "po_answers": {"Q": answer}}
        actual = [s.title for s in deferred_implied_stories(state)]
        assert actual == expected_deferred_titles, (
            f"answer={answer!r}: expected {expected_deferred_titles}, got {actual}"
        )


def test_deferred_implied_stories_ignores_empty_or_whitespace_answers():
    """Empty/whitespace-only PO answers shouldn't behave like a 'no answer' (which
    would defer nothing) - they should be skipped like the answers dict was empty."""
    from agents.graph import deferred_implied_stories
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    stories = [ImpliedStory(title="Story A", summary="a")]
    state = {
        "analyst_output": AnalystOutput(
            intent="x", actors=["u"], ambiguities=[], implied_stories=stories
        ),
        "po_answers": {"Q1": "   ", "Q2": ""},
    }
    # No usable answers → treat as no story-level scoping → empty deferred.
    assert deferred_implied_stories(state) == []


def test_picked_implied_story_returns_single_match_or_none():
    """Phase 15.4 - picked_implied_story is single-pick by design.

    Pin the four cases that determine downstream behaviour:
    - clean single pick → that story
    - multiple matches (ambiguous "pick 2") → None
    - "all" / unrelated text → None
    - empty po_answers / no implied_stories → None
    """
    from agents.graph import picked_implied_story
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    stories = [
        ImpliedStory(title="Quarantined tests dashboard", summary="dash"),
        ImpliedStory(title="Audit log for quarantine actions", summary="audit"),
        ImpliedStory(title="Slack notifications for auto-quarantine changes", summary="slack"),
    ]
    base = {
        "analyst_output": AnalystOutput(
            intent="x", actors=["u"], ambiguities=[], implied_stories=stories
        ),
    }

    # Clean pick - short variant matches exactly one title.
    s = picked_implied_story({**base, "po_answers": {"Q": "dashboard"}})
    assert s is not None and s.title == "Quarantined tests dashboard"

    # Multiple matches → None.
    s = picked_implied_story({**base, "po_answers": {"Q": "dashboard and audit log"}})
    assert s is None

    # "all" doesn't match any title → None.
    s = picked_implied_story({**base, "po_answers": {"Q": "all"}})
    assert s is None

    # No po_answers → None.
    s = picked_implied_story({**base, "po_answers": {}})
    assert s is None

    # No implied_stories → None.
    s = picked_implied_story(
        {
            "analyst_output": AnalystOutput(
                intent="x", actors=["u"], ambiguities=[], implied_stories=[]
            ),
            "po_answers": {"Q": "anything"},
        }
    )
    assert s is None


def test_story_writer_node_passes_picked_story_for_single_implied_story():
    """Phase 15.4 - single-implied-story deep path still wires the picked story.

    Multi-story (≥ 2) cases route to split and never call the Story
    Writer. The picked-story narrowing is now only meaningful for the
    1-implied-story deep case (15.1 Reviewer scope-awareness similarly
    becomes a no-op for 0-implied - empty deferred list).
    """
    from unittest.mock import patch

    from agents.user_story_writer import UserStory

    captured_picks: list[object] = []

    def _fake_write(_analyst, _po, *, picked_story=None, **_kw):
        captured_picks.append(picked_story)
        return UserStory(description="d", objective="o", assumptions=[])

    analyst_payload = {
        "intent": "single-story req",
        "actors": ["User"],
        "ambiguities": [],
        "implied_stories": [
            {"title": "The only story", "summary": "the only story summary"},
        ],
    }

    with (
        _mock_pipeline_llms(analyst_payload),
        patch("agents.graph.write_user_story", side_effect=_fake_write),
    ):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-picked-1-story"}}
        # No critical ambiguity → PO checkpoint passes through →
        # Story Writer runs immediately → Story Review pauses.
        pipeline.invoke({"requirement_text": "req"}, config=config)
        pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert len(captured_picks) == 1
    assert captured_picks[0] is not None
    assert captured_picks[0].title == "The only story"


def test_multi_story_branches_to_split_skipping_deep_nodes():
    """Phase 15.4 - implied_stories ≥ 2 routes to split_node, skipping
    Story Writer / AC Generator / Test Case Writer / Reviewer entirely.

    The split_node populates ``split_stories`` filtered by the PO's
    ``selected_story_titles`` selection. None of the deep-path agent
    library functions are called.
    """
    from unittest.mock import patch

    from agents.user_story_writer import UserStory

    deep_calls: dict[str, int] = {
        "story": 0,
        "ac": 0,
        "test": 0,
        "reviewer": 0,
    }

    def _spy_story(_a, _p, **_kw):
        deep_calls["story"] += 1
        return UserStory(description="d", objective="o", assumptions=[])

    analyst_payload = {
        "intent": "multi-story req",
        "actors": ["User"],
        "ambiguities": [
            {
                "question": "This requirement implies 3 stories. Which single story?",
                "severity": "critical",
            }
        ],
        "implied_stories": [
            {"title": "Story A", "summary": "a"},
            {"title": "Story B", "summary": "b"},
            {"title": "Story C", "summary": "c"},
        ],
    }

    with (
        _mock_pipeline_llms(analyst_payload),
        patch("agents.graph.write_user_story", side_effect=_spy_story),
    ):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-split-branch"}}

        # First invoke → analyst runs, po_checkpoint pauses for split.
        first = pipeline.invoke({"requirement_text": "req"}, config=config)
        payload = first["__interrupt__"][0].value
        assert payload["mode"] == "split"
        assert len(payload["implied_stories"]) == 3
        # The "which single story?" critical question is hidden from the
        # text-input list because the CheckboxGroup replaces it.
        assert payload["critical_ambiguities"] == []

        # PO keeps 2 of 3 → split_node filters.
        result = pipeline.invoke(
            Command(
                resume={
                    "selected_story_titles": ["Story A", "Story C"],
                    "answers": {},
                }
            ),
            config=config,
        )

    # Story Writer was never called - proves deep nodes are skipped.
    assert deep_calls["story"] == 0
    assert "user_story" not in result
    assert "final_artifact" not in result
    assert "review_report" not in result
    out_stories = result["split_stories"]
    assert [s.title for s in out_stories] == ["Story A", "Story C"]


def test_split_empty_selection_keeps_all_stories():
    """Phase 15.4 / Q2 default - empty selected_story_titles ⇒ fan out
    every implied story rather than producing nothing."""
    from agents.graph import split_node
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    stories = [
        ImpliedStory(title="Story A", summary="a"),
        ImpliedStory(title="Story B", summary="b"),
    ]
    state = {
        "analyst_output": AnalystOutput(
            intent="x", actors=["u"], ambiguities=[], implied_stories=stories
        ),
        "selected_story_titles": [],
    }
    out = split_node(state)
    assert [s.title for s in out["split_stories"]] == ["Story A", "Story B"]


def test_is_split_mode_branch_criterion():
    """Phase 15.4 - pin the branch criterion explicitly.

    The condition is purely on the Analyst's output (count ≥ 2). Even
    if the PO eventually unchecks all but one story, the routing was
    decided when the checkpoint fired.
    """
    from agents.graph import is_split_mode
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    def _state(n: int) -> dict:
        return {
            "analyst_output": AnalystOutput(
                intent="x",
                actors=["u"],
                ambiguities=[],
                implied_stories=[ImpliedStory(title=f"S{i}", summary="s") for i in range(n)],
            )
        }

    assert is_split_mode(_state(0)) is False
    assert is_split_mode(_state(1)) is False
    assert is_split_mode(_state(2)) is True
    assert is_split_mode(_state(5)) is True
    assert is_split_mode({}) is False  # no analyst output yet


def test_split_node_passes_through_edited_stories():
    """Issue #207 - when ``selected_split_stories`` is set on state
    (the PO renamed at least one row at the editable PO checkpoint),
    ``split_node`` ships those stories through verbatim, bypassing
    the legacy title-filter on the Analyst's implied list."""
    from agents.graph import split_node
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    analyst_stories = [
        ImpliedStory(title="Story A", summary="a"),
        ImpliedStory(title="Story B", summary="b"),
    ]
    edited = [ImpliedStory(title="Story A - renamed", summary="a")]
    state = {
        "analyst_output": AnalystOutput(
            intent="x", actors=["u"], ambiguities=[], implied_stories=analyst_stories
        ),
        "selected_split_stories": edited,
        # selected_story_titles deliberately stale - the new field wins.
        "selected_story_titles": ["Story A"],
    }
    out = split_node(state)
    assert [s.title for s in out["split_stories"]] == ["Story A - renamed"]
    assert [s.summary for s in out["split_stories"]] == ["a"]


def test_po_checkpoint_node_builds_edited_stories_from_resume():
    """Issue #207 - ``po_checkpoint_node`` reads the new
    ``selected_stories`` shape from the interrupt resume and converts
    it into ``ImpliedStory`` objects on state, populating
    ``selected_split_stories`` and a back-compat title list."""
    from unittest.mock import patch

    from agents.graph import po_checkpoint_node
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    state = {
        "analyst_output": AnalystOutput(
            intent="x",
            actors=["u"],
            ambiguities=[],
            implied_stories=[
                ImpliedStory(title="Story A", summary="summary-A"),
                ImpliedStory(title="Story B", summary="summary-B"),
            ],
        ),
    }
    fake_resume = {
        "selected_stories": [
            {
                "title": "Story A - renamed",
                # No summary supplied → graph backfills from
                # ``original_title`` → Analyst's matching implied story.
                "original_title": "Story A",
            },
        ],
        "answers": {},
    }
    with patch("agents.graph.interrupt", return_value=fake_resume):
        result = po_checkpoint_node(state)
    assert "selected_split_stories" in result
    assert [s.title for s in result["selected_split_stories"]] == ["Story A - renamed"]
    assert [s.summary for s in result["selected_split_stories"]] == ["summary-A"]
    assert result["selected_story_titles"] == ["Story A - renamed"]


def test_po_checkpoint_node_legacy_title_list_still_works():
    """Issue #207 - legacy resume value (``selected_story_titles`` only)
    still routes through the old code path; no edited state field set."""
    from unittest.mock import patch

    from agents.graph import po_checkpoint_node
    from agents.requirements_analyst import AnalystOutput, ImpliedStory

    state = {
        "analyst_output": AnalystOutput(
            intent="x",
            actors=["u"],
            ambiguities=[],
            implied_stories=[
                ImpliedStory(title="Story A", summary="a"),
                ImpliedStory(title="Story B", summary="b"),
            ],
        ),
    }
    fake_resume = {"selected_story_titles": ["Story A"], "answers": {}}
    with patch("agents.graph.interrupt", return_value=fake_resume):
        result = po_checkpoint_node(state)
    assert "selected_split_stories" not in result
    assert result["selected_story_titles"] == ["Story A"]
