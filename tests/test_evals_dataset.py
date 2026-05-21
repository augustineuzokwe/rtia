"""Parser tests for evals.dataset.

Exercises the loader on the real sample files so any drift between the
golden-dataset markdown and what the eval runner sees is caught here
without needing a live API call.
"""

from __future__ import annotations

from evals.dataset import SAMPLES_DIR, load_all_samples, load_sample


def test_loads_all_three_samples() -> None:
    samples = load_all_samples()
    names = [s.name for s in samples]
    assert names == [
        "sample-01-well-structured",
        "sample-02-vague-ambiguous",
        "sample-03-multi-feature",
    ]


def test_sample_01_well_structured_ground_truth() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-01-well-structured.md")
    assert "real-time test run summary" in sample.raw_requirement
    expected = sample.expected_analyst
    assert expected.actors == ["QA Lead (authenticated user)", "unauthenticated user"]
    # Post-Phase-9.3 calibration (#101): the "project selection mechanism"
    # ambiguity is now expected. The requirement is silent on how
    # selection happens — that's a legitimate scope-shape question and
    # forcing the Analyst to suppress it would penalise legitimate inquiry.
    assert expected.ambiguity_categories == ["project selection mechanism"]
    assert expected.implied_story_titles == []
    assert "authenticated QA user" in expected.intent


def test_sample_02_vague_ambiguity_categories_extracted() -> None:
    sample = load_sample(SAMPLES_DIR / "sample-02-vague-ambiguous.md")
    expected = sample.expected_analyst
    assert expected.actors == ["QA team member", "manager"]
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
    # Email alerting and CSV export belong to other implied stories — they MUST
    # be listed as out-of-scope for this scoped story.
    out_text = " ".join(acs.out_of_scope).lower()
    assert "email alerting" in out_text
    assert "csv export" in out_text
