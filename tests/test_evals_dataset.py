"""Parser tests for evals.dataset.

Exercises the loader on the real sample files so any drift between the
golden-dataset markdown and what the eval runner sees is caught here
without needing a live API call.
"""

from __future__ import annotations

from evals.dataset import SAMPLES_DIR, load_all_samples, load_sample


def test_loads_all_samples() -> None:
    samples = load_all_samples()
    names = [s.name for s in samples]
    # Includes Phase 12.1 adversarial samples (04-06) and the negative
    # false-positive boundary sample (07).
    assert names == [
        "sample-01-well-structured",
        "sample-02-vague-ambiguous",
        "sample-03-multi-feature",
        "sample-04-injection-suffix",
        "sample-05-injection-inline",
        "sample-06-injection-data-extraction",
        "sample-07-transcript-human-imperatives",
    ]


def test_non_adversarial_samples_have_no_injection_test() -> None:
    """Samples 01-03 predate Phase 12.1 and must not parse an injection block."""
    for name in (
        "sample-01-well-structured",
        "sample-02-vague-ambiguous",
        "sample-03-multi-feature",
    ):
        sample = load_sample(SAMPLES_DIR / f"{name}.md")
        assert sample.injection_test is None, name


def test_adversarial_samples_expect_detected_true() -> None:
    """Samples 04-06 are adversarial; the Analyst must set detected=true."""
    for name, expected_pattern_count in (
        ("sample-04-injection-suffix", 4),
        ("sample-05-injection-inline", 5),
        ("sample-06-injection-data-extraction", 6),
    ):
        sample = load_sample(SAMPLES_DIR / f"{name}.md")
        assert sample.injection_test is not None, name
        assert sample.injection_test.expected_detected is True, name
        assert len(sample.injection_test.forbidden_patterns) == expected_pattern_count, name


def test_transcript_sample_expects_detected_false() -> None:
    """Sample-07 anchors the false-positive boundary: detected must be False."""
    sample = load_sample(SAMPLES_DIR / "sample-07-transcript-human-imperatives.md")
    assert sample.injection_test is not None
    assert sample.injection_test.expected_detected is False
    assert sample.injection_test.forbidden_patterns == []


def test_sample_01_well_structured_ground_truth() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-01-well-structured.md")
    assert "real-time test run summary" in sample.raw_requirement
    expected = sample.expected_analyst
    # Post-#102 calibration: relaxed from "QA Lead (authenticated user)" to
    # just "authenticated user" to match the raw requirement text. See the
    # sample file's Actors section note for rationale.
    assert expected.actors == ["authenticated user", "unauthenticated user"]
    # Post-Phase-9.3 calibration (#101): the "project selection mechanism"
    # ambiguity is now expected. The requirement is silent on how
    # selection happens - that's a legitimate scope-shape question and
    # forcing the Analyst to suppress it would penalise legitimate inquiry.
    assert expected.ambiguity_categories == ["project selection mechanism"]
    assert expected.implied_story_titles == []
    assert "authenticated QA user" in expected.intent
    # #103: intent_key_terms field is parsed from the new optional section.
    assert expected.intent_key_terms == [
        "authenticated",
        "test run",
        "dashboard",
        "project",
        "refresh",
    ]
    # #99: requirement_key_terms is parsed from the new top-level
    # `## Requirement Key Terms` section.
    assert sample.requirement_key_terms == [
        "passed",
        "failed",
        "skipped",
        "30 seconds",
        "full page reload",
        "authenticated",
    ]


def test_sample_02_vague_ambiguity_categories_extracted() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-02-vague-ambiguous.md")
    expected = sample.expected_analyst
    # Post-#102 calibration: relaxed from "QA team member" to just
    # "team member" - see the sample file's Actors section note.
    assert expected.actors == ["team member", "manager"]
    # Categories are derived from the bold prefix of each bullet.
    assert expected.ambiguity_categories == [
        "actor scoping",
        "manager visibility shape",
        "team update scope",
        "defect domain",
        "success measure",
    ]
    assert expected.implied_story_titles == []


def test_sample_03_multi_feature_implied_stories_extracted() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-03-multi-feature.md")
    expected = sample.expected_analyst
    assert expected.actors == ["tester", "QA Lead"]
    assert expected.ambiguity_categories == ["multi-feature pick-one"]
    assert expected.implied_story_titles == [
        "Filtering with persistence",
        "CSV export",
        "Email alerting on failure spike",
    ]


def test_sample_01_expected_ac_ground_truth() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-01-well-structured.md")
    acs = sample.expected_acs
    assert acs.required_categories == [
        "summary display",
        "auto-refresh cadence",
        "access boundary",
    ]
    assert acs.expected_count == 3
    assert acs.count_tolerance == 1
    # Out-of-scope should at minimum cover the UX-detail trap the prompt warns about.
    assert any("refresh-indicator" in label for label in acs.out_of_scope)


def test_sample_02_expected_ac_ground_truth_is_minimal() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-02-vague-ambiguous.md")
    acs = sample.expected_acs
    assert acs.required_categories == [
        "view defects list",
        "update a defect",
        "manager summary view",
    ]
    assert acs.expected_count == 3
    # The vague sample must explicitly warn against invented specific fields.
    assert any("specific defect fields" in label for label in acs.out_of_scope)


def test_sample_03_expected_ac_ground_truth_scoped_to_filtering() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-03-multi-feature.md")
    acs = sample.expected_acs
    assert acs.required_categories == [
        "filter by date range",
        "filter by environment",
        "filter by test-suite name",
        "filter persistence",
    ]
    assert acs.expected_count == 4
    # Email alerting and CSV export belong to other implied stories - they MUST
    # be listed as out-of-scope for this scoped story.
    out_text = " ".join(acs.out_of_scope).lower()
    assert "email alerting" in out_text
    assert "csv export" in out_text
