#!/usr/bin/env python3
"""
Structural validator for eval sample requirement files.

Checks:
  1. All required sections are present
  2. User story follows "As a... I want... so that..." format
  3. Every AC block has Given / When / Then
  4. Vague samples list at least 3 ambiguities

LLM faithfulness check (does expected output introduce scope not in the
raw requirement?) will be added in validate_samples_llm.py during the
Python scaffold phase, once the Anthropic SDK is available.

Usage:
  python3 evals/validate_samples.py                  # validate all samples
  python3 evals/validate_samples.py path/to/file.md  # validate one file
"""

import re
import sys
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent / "sample-requirements"

REQUIRED_SECTIONS = [
    "## Raw Requirement",
    "## Expected Output",
    "### User Story",
    "### Acceptance Criteria",
    "## Expected Analyst Output",
    "### Intent",
    "### Actors (expected set)",
    "### Ambiguity Categories",
    "### Implied Stories",
    "## Expected Acceptance Criteria",
    "### Required AC Categories",
    "### Expected AC Count",
    "### Out-of-Scope Behaviours",
    "## Eval Notes",
]

AC_COUNT_RE = re.compile(r"\b(\d+)\s*\(\s*±\s*(\d+)\s*\)")

BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
NONE_EXPECTED_RE = re.compile(r"\(none expected\)", re.IGNORECASE)

USER_STORY_RE = re.compile(
    r"As an? .+,\s+I want .+,\s+so that .+",
    re.IGNORECASE | re.DOTALL,
)
NUMBERED_ITEM_RE = re.compile(r"^\d+\.", re.MULTILINE)


def extract_section(content: str, start_header: str, stop_headers: list[str]) -> str | None:
    start_idx = content.find(start_header)
    if start_idx == -1:
        return None
    body_start = start_idx + len(start_header)
    earliest_stop = len(content)
    for stop in stop_headers:
        idx = content.find(stop, body_start)
        if idx != -1 and idx < earliest_stop:
            earliest_stop = idx
    return content[body_start:earliest_stop].strip()


def check_required_sections(content: str) -> list[str]:
    return [f"Missing required section: '{s}'" for s in REQUIRED_SECTIONS if s not in content]


def check_user_story_format(content: str) -> list[str]:
    section = extract_section(content, "### User Story", ["###", "## "])
    if section is None:
        return []
    if not USER_STORY_RE.search(section):
        return [
            "User story does not follow format: 'As a [role], I want [feature], so that [benefit]'"
        ]
    return []


def check_ac_format(content: str) -> list[str]:
    section = extract_section(content, "### Acceptance Criteria", ["## "])
    if section is None:
        return []
    return [
        f"Acceptance criteria missing {kw} block"
        for kw in ["**Given**", "**When**", "**Then**"]
        if kw not in section
    ]


def check_ambiguities(content: str) -> list[str]:
    if "## Ambiguities RTIA Should Flag" not in content:
        return []
    section = extract_section(content, "## Ambiguities RTIA Should Flag", ["## "])
    if section is None:
        return []
    count = len(NUMBERED_ITEM_RE.findall(section))
    if count < 3:
        return [f"Vague samples must list at least 3 ambiguities (found {count})"]
    return []


def check_analyst_intent(content: str) -> list[str]:
    section = extract_section(content, "### Intent", ["###", "## "])
    if section is None:
        return []
    # Strip optional quoted-block guidance that precedes the actual intent text.
    body = "\n".join(line for line in section.splitlines() if not line.lstrip().startswith(">"))
    if len(body.strip()) < 20:
        return ["Expected Analyst Output → Intent must be a non-trivial sentence (>=20 chars)"]
    return []


def check_analyst_actors(content: str) -> list[str]:
    section = extract_section(content, "### Actors (expected set)", ["###", "## "])
    if section is None:
        return []
    if not BULLET_RE.search(section):
        return ["Expected Analyst Output → Actors (expected set) must be a bulleted list"]
    return []


def check_analyst_ambiguity_categories(content: str) -> list[str]:
    section = extract_section(content, "### Ambiguity Categories", ["###", "## "])
    if section is None:
        return []
    has_bullets = bool(BULLET_RE.search(section))
    has_none_marker = bool(NONE_EXPECTED_RE.search(section))
    if not (has_bullets or has_none_marker):
        return [
            "Expected Analyst Output → Ambiguity Categories must be a bulleted list "
            "or explicitly state '(none expected)'"
        ]
    return []


def check_analyst_implied_stories(content: str) -> list[str]:
    section = extract_section(content, "### Implied Stories", ["###", "## "])
    if section is None:
        return []
    has_bullets = bool(BULLET_RE.search(section))
    has_none_marker = bool(NONE_EXPECTED_RE.search(section))
    if not (has_bullets or has_none_marker):
        return [
            "Expected Analyst Output → Implied Stories must be a bulleted list "
            "or explicitly state '(none expected)'"
        ]
    # Multi-feature samples are the ones that declare features; their Implied Stories
    # section must be a real list, not '(none expected)'.
    if "## Features Contained" in content and has_none_marker and not has_bullets:
        return [
            "Multi-feature samples must list implied stories under "
            "'### Implied Stories' (not '(none expected)')"
        ]
    return []


def check_ac_required_categories(content: str) -> list[str]:
    section = extract_section(content, "### Required AC Categories", ["###", "## "])
    if section is None:
        return []
    if not BULLET_RE.search(section):
        return ["Expected Acceptance Criteria → Required AC Categories must be a bulleted list"]
    return []


def check_ac_expected_count(content: str) -> list[str]:
    section = extract_section(content, "### Expected AC Count", ["###", "## "])
    if section is None:
        return []
    if not AC_COUNT_RE.search(section):
        return ["Expected Acceptance Criteria → Expected AC Count must include a 'N (±M)' pattern"]
    return []


def check_ac_out_of_scope(content: str) -> list[str]:
    section = extract_section(content, "### Out-of-Scope Behaviours", ["###", "## "])
    if section is None:
        return []
    if not BULLET_RE.search(section):
        return ["Expected Acceptance Criteria → Out-of-Scope Behaviours must be a bulleted list"]
    return []


CHECKS = [
    check_required_sections,
    check_user_story_format,
    check_ac_format,
    check_ambiguities,
    check_analyst_intent,
    check_analyst_actors,
    check_analyst_ambiguity_categories,
    check_analyst_implied_stories,
    check_ac_required_categories,
    check_ac_expected_count,
    check_ac_out_of_scope,
]


def validate_file(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    violations = []
    for check in CHECKS:
        violations.extend(check(content))
    return violations


def main() -> None:
    files = [Path(sys.argv[1])] if len(sys.argv) > 1 else sorted(SAMPLES_DIR.glob("sample-*.md"))

    if not files:
        print("No sample files found.")
        sys.exit(1)

    total_violations = 0
    for file_path in files:
        violations = validate_file(file_path)
        if violations:
            print(f"\n❌  {file_path.name}")
            for v in violations:
                print(f"    • {v}")
            total_violations += len(violations)
        else:
            print(f"✅  {file_path.name}")

    print()
    if total_violations:
        print(f"{total_violations} violation(s) found. Fix before committing.")
        sys.exit(1)
    else:
        print(f"All {len(files)} sample(s) passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
