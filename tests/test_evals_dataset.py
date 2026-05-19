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
    assert expected.ambiguity_categories == []
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
