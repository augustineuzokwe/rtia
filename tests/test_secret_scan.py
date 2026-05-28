"""Tests for the secret pattern scanner.

Per-pattern positive + negative tests, redaction safety, regression
guard on the committed sample files, and integration coverage of the
abort behaviour at the graph start node.

The pattern strings used as positive-test fixtures are syntactically
shaped to match each detector but are NOT real secrets - they are
random base62 / base64 noise hand-crafted to satisfy the regex anchors.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents._secret_scan import (
    SecretInInputError,
    _redact,
    raise_if_secrets_found,
    scan_for_secrets,
)

# Synthetic test fixtures - shaped like the real format but composed of
# random padding so no actual credential is exposed by the test files.
_FAKE_AWS = "AKIAEXAMPLEPADDINGAB"  # AKIA + 16 base32 chars
_FAKE_GH_PAT = "ghp_" + "Abc123def456gHi789JkL012mNo345pQr678"
_FAKE_GH_FINE = "github_pat_" + "A" * 82
_FAKE_ANTHROPIC = "sk-ant-" + "Abcdefghij1234567890_KlmnopQrst-uvwxYz9876"
_FAKE_OPENAI_LEGACY = "sk-" + "A" * 48
_FAKE_OPENAI_PROJECT = "sk-proj-" + "A" * 40
_FAKE_GOOGLE = "AIza" + "abcdef-1234567890_HIJKlmnop-qrstuvw"  # AIza + 35 chars
_FAKE_SLACK = "xoxb-1234567890-1234567890-AbCdEfGhIjKlMnOpQrStUvWx"
_FAKE_STRIPE_LIVE = "sk_live_" + "A" * 24
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0IiwiaWF0IjoxNzA1Nzg5MDAwfQ.abc_DEF-1234"
_FAKE_PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"


# ---------------------------------------------------------------------------
# Positive matches - each pattern must catch its canonical shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,expected_name",
    [
        (_FAKE_AWS, "aws_access_key_id"),
        (_FAKE_GH_PAT, "github_pat"),
        (_FAKE_GH_FINE, "github_pat"),
        (_FAKE_ANTHROPIC, "anthropic_api_key"),
        (_FAKE_OPENAI_LEGACY, "openai_api_key"),
        (_FAKE_OPENAI_PROJECT, "openai_api_key"),
        (_FAKE_GOOGLE, "google_api_key"),
        (_FAKE_SLACK, "slack_token"),
        (_FAKE_STRIPE_LIVE, "stripe_live_key"),
        (_FAKE_JWT, "jwt"),
        (_FAKE_PEM_HEADER, "pem_private_key"),
    ],
)
def test_pattern_matches_canonical_shape(fixture: str, expected_name: str) -> None:
    text = f"Pasted by mistake: {fixture} in the middle of a sentence."
    findings = scan_for_secrets(text)
    names = {f.pattern_name for f in findings}
    assert expected_name in names, f"missing {expected_name}; got {names}"


# ---------------------------------------------------------------------------
# Negative matches - legitimate-looking text must NOT trip detectors
# ---------------------------------------------------------------------------


def test_uuid_does_not_match() -> None:
    """UUIDs appear in legitimate requirements and must not fire any pattern."""
    text = "Order id is 550e8400-e29b-41d4-a716-446655440000."
    assert scan_for_secrets(text) == []


def test_sha256_hash_does_not_match() -> None:
    """64-char hex digests must not trip the entropy-style patterns we excluded."""
    text = "Hash: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855."
    assert scan_for_secrets(text) == []


def test_base64_blob_without_prefix_does_not_match() -> None:
    """Random base64 (e.g. an image data URI) lacks our anchored prefixes."""
    text = (
        "Image: iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        "DUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )
    assert scan_for_secrets(text) == []


def test_test_mode_stripe_keys_are_not_flagged() -> None:
    """sk_test_ / pk_test_ are non-production and routinely appear in docs."""
    text = "Stripe test key: sk_test_" + "A" * 24
    assert scan_for_secrets(text) == []


def test_short_capitalised_words_do_not_match_aws() -> None:
    """AWS pattern requires AKIA + exactly 16 base32 chars - must not over-match."""
    text = "The acronym AKIA is sometimes used in compliance documents."
    assert scan_for_secrets(text) == []


def test_plain_sentence_with_no_secrets_returns_empty() -> None:
    text = (
        "As a QA Lead, I want to view a real-time test run summary so that "
        "I can monitor the health of the latest build."
    )
    assert scan_for_secrets(text) == []


# ---------------------------------------------------------------------------
# Redaction safety - the secret's interior must never leak to output
# ---------------------------------------------------------------------------


def test_redact_short_string_fully_masked() -> None:
    assert _redact("abcd1234") == "…"
    assert _redact("short") == "…"


def test_redact_long_string_keeps_4_4() -> None:
    assert _redact("sk-ant-abcdefghijklmnopqrstuvwxyz") == "sk-a…wxyz"


def test_redact_never_exposes_middle_bytes() -> None:
    """Property test: redacted form must not contain any middle byte of the input."""
    secret = "AKIASECRETSECRETSECRETSECRET"
    redacted = _redact(secret)
    middle = secret[4:-4]
    for ch in middle:
        # The middle byte may incidentally also appear in the 4-char prefix
        # or suffix that we DO show. Only fail if it appears at a position
        # in the redacted string that is not part of the kept ends.
        kept = secret[:4] + secret[-4:]
        if ch in redacted and ch not in kept:
            pytest.fail(f"middle byte {ch!r} leaked into redacted output {redacted!r}")


def test_secret_finding_does_not_carry_raw_match() -> None:
    """SecretFinding dataclass exposes only the redacted form, never the raw."""
    text = "leak: " + _FAKE_ANTHROPIC
    findings = scan_for_secrets(text)
    assert findings
    finding = findings[0]
    # SecretFinding fields are frozen - there's no path to retrieve the raw
    # match from the finding itself.
    assert not hasattr(finding, "raw_match")
    assert not hasattr(finding, "matched_text")
    # The redacted form must not contain the secret's middle bytes.
    raw_middle = _FAKE_ANTHROPIC[4:-4]
    for ch_idx in range(len(raw_middle) - 3):
        seq = raw_middle[ch_idx : ch_idx + 4]
        # A 4-char window from the middle must not appear in the redacted form.
        assert seq not in finding.redacted_match


# ---------------------------------------------------------------------------
# raise_if_secrets_found / SecretInInputError
# ---------------------------------------------------------------------------


def test_raise_if_secrets_found_passes_clean_input() -> None:
    """No exception on input free of secret patterns."""
    raise_if_secrets_found("A normal requirement with nothing suspicious.")


def test_raise_if_secrets_found_raises_on_match() -> None:
    text = "Use this key: " + _FAKE_GOOGLE
    with pytest.raises(SecretInInputError) as excinfo:
        raise_if_secrets_found(text)
    assert excinfo.value.findings
    assert excinfo.value.findings[0].pattern_name == "google_api_key"


def test_secret_in_input_error_message_redacts() -> None:
    """The exception's str() must not contain the raw secret."""
    text = "Stash: " + _FAKE_ANTHROPIC
    with pytest.raises(SecretInInputError) as excinfo:
        raise_if_secrets_found(text)
    message = str(excinfo.value)
    raw_middle = _FAKE_ANTHROPIC[4:-4]
    assert raw_middle not in message
    assert "sk-a" in message  # prefix shown
    assert "anthropic_api_key" in message  # pattern name shown


def test_multiple_findings_returned_in_position_order() -> None:
    """Findings are sorted by their start offset so the user reads them in order."""
    text = f"first {_FAKE_GOOGLE} then later {_FAKE_ANTHROPIC} end"
    findings = scan_for_secrets(text)
    assert len(findings) == 2
    assert findings[0].pattern_name == "google_api_key"
    assert findings[1].pattern_name == "anthropic_api_key"
    assert findings[0].start < findings[1].start


# ---------------------------------------------------------------------------
# Regression guard - the committed sample files must scan clean
# ---------------------------------------------------------------------------


_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "evals" / "sample-requirements"


def test_all_committed_samples_scan_clean() -> None:
    """No sample file may contain a high-confidence secret pattern.

    Catches one specific failure mode: a contributor (human or agent)
    accidentally commits a real key into a sample file. The 7 samples
    were hand-curated to contain no credentials; this asserts that
    invariant in CI so a future regression fails the build instead of
    shipping a key to the public repo.
    """
    sample_files = sorted(_SAMPLES_DIR.glob("sample-*.md"))
    assert sample_files, "No sample files found - test fixture path drifted."
    for path in sample_files:
        findings = scan_for_secrets(path.read_text(encoding="utf-8"))
        assert not findings, (
            f"Sample file {path.name} contains a potential secret: "
            f"{[(f.pattern_name, f.redacted_match) for f in findings]}"
        )


# ---------------------------------------------------------------------------
# Graph-level integration - the analyst node aborts before any LLM call
# ---------------------------------------------------------------------------


def test_analyst_node_aborts_on_secret_in_input() -> None:
    """The graph's analyst_node raises before invoking analyze_requirement.

    Verified by passing a state containing a fake key. If the abort
    fires correctly, ``SecretInInputError`` propagates and the
    underlying LLM call is never reached. We do not mock the LLM - if
    the scanner failed and the call went through, the test would
    either hit a real Gemini error (with no API key set in the test
    env) or fabricate output, both of which would surface as a
    different failure than ``SecretInInputError``.
    """
    from agents.graph import analyst_node  # imported here to avoid cycles

    state = {"requirement_text": "Please process: " + _FAKE_OPENAI_LEGACY}
    with pytest.raises(SecretInInputError):
        analyst_node(state)


def test_analyst_node_passes_clean_input_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean input is forwarded to the LLM normally (scanner is no-op)."""
    from agents import graph as graph_module

    captured: dict[str, str] = {}

    def fake_analyze(text: str) -> object:
        captured["text"] = text

        # Return a minimal stub the node can wrap in {"analyst_output": ...}.
        class _Stub:
            intent = "stub"
            actors: list[str] = []
            ambiguities: list[str] = []
            implied_stories: list[str] = []
            suspicious_input = None

        return _Stub()

    monkeypatch.setattr(graph_module, "analyze_requirement", fake_analyze)
    result = graph_module.analyst_node({"requirement_text": "Normal requirement."})
    assert captured["text"] == "Normal requirement."
    assert "analyst_output" in result


# ---------------------------------------------------------------------------
# Redaction format documentation - locks in the visible shape
# ---------------------------------------------------------------------------


def test_redacted_form_matches_expected_shape() -> None:
    """Pinned format for downstream callers and PR documentation."""
    out = _redact("sk-ant-1234567890abcdefghijklmnop")
    assert re.fullmatch(r"sk-a…[a-z]{4}", out)
