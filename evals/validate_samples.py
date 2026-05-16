#!/usr/bin/env python3
"""
Structural validator for eval sample requirement files.

Checks:
  1. All required sections are present
  2. Sections appear in the correct order
  3. User story follows "As a... I want... so that..." format
  4. Every AC block has Given / When / Then
  5. Vague samples list at least 3 ambiguities
  6. Multi-feature samples: numbered feature list count matches
     the count stated in prose ("X separable user stories")
  7. Eval notes feature count matches the feature list count

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

# ── Constants ────────────────────────────────────────────────────────────────

SAMPLES_DIR = Path(__file__).parent / "sample-requirements"

# Sections every sample must contain, in this order
REQUIRED_SECTIONS_ORDERED = [
    "## Raw Requirement",
    "## Expected Output",
    "### User Story",
    "### Acceptance Criteria",
    "## Eval Notes",
]

USER_STORY_RE = re.compile(
    r"As an? .+,\s+I want .+,\s+so that .+",
    re.IGNORECASE | re.DOTALL,
)
NUMBERED_ITEM_RE = re.compile(r"^\d+\.", re.MULTILINE)
SEPARABLE_COUNT_RE = re.compile(r"(\d+)\s+separable\s+user\s+stor(?:y|ies)", re.IGNORECASE)
NOTES_FEATURE_COUNT_RE = re.compile(r"contains\s+(\d+)\s+(?:separable\s+)?(?:features?|user\s+stor(?:y|ies))", re.IGNORECASE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_section(content: str, start_header: str, stop_headers: list[str]) -> str | None:
    """
    Return the text between start_header and the first occurrence of any
    stop_header that appears AFTER start_header. Returns None if start_header
    is not found.
    """
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


# ── Validation rules ──────────────────────────────────────────────────────────

def check_required_sections(content: str) -> list[str]:
    violations = []
    for section in REQUIRED_SECTIONS_ORDERED:
        if section not in content:
            violations.append(f"Missing required section: '{section}'")
    return violations


def check_section_order(content: str) -> list[str]:
    violations = []
    positions = []
    for section in REQUIRED_SECTIONS_ORDERED:
        idx = content.find(section)
        if idx != -1:
            positions.append((idx, section))

    sorted_positions = sorted(positions, key=lambda x: x[0])
    found_sections = [s for _, s in sorted_positions]
    expected_sections = [s for s in REQUIRED_SECTIONS_ORDERED if s in content]

    if found_sections != expected_sections:
        violations.append(
            f"Sections out of order.\n"
            f"     Found:    {found_sections}\n"
            f"     Expected: {expected_sections}"
        )
    return violations


def check_user_story_format(content: str) -> list[str]:
    violations = []
    section = extract_section(content, "### User Story", ["###", "## "])
    if section is None:
        return violations  # already caught by required sections check
    if not USER_STORY_RE.search(section):
        violations.append(
            "User story does not follow required format: "
            "'As a [role], I want [feature], so that [benefit]'"
        )
    return violations


def check_ac_format(content: str) -> list[str]:
    violations = []
    section = extract_section(content, "### Acceptance Criteria", ["## "])
    if section is None:
        return violations
    for keyword in ["**Given**", "**When**", "**Then**"]:
        if keyword not in section:
            violations.append(f"Acceptance criteria missing {keyword} block")
    return violations


def check_ambiguities(content: str) -> list[str]:
    violations = []
    if "## Ambiguities RTIA Should Flag" not in content:
        return violations  # not a vague sample — skip
    section = extract_section(
        content,
        "## Ambiguities RTIA Should Flag",
        ["## "]
    )
    if section is None:
        return violations
    items = NUMBERED_ITEM_RE.findall(section)
    if len(items) < 3:
        violations.append(
            f"Vague samples must list at least 3 ambiguities (found {len(items)})"
        )
    return violations


def check_feature_count_consistency(content: str) -> list[str]:
    """
    For multi-feature samples, the numbered list count must match the count
    stated in prose ("X separable user stories") in both the Features
    Contained section and the Eval Notes section.
    """
    violations = []
    if "## Features Contained" not in content:
        return violations  # not a multi-feature sample — skip

    features_section = extract_section(content, "## Features Contained", ["## "])
    if features_section is None:
        return violations

    list_count = len(NUMBERED_ITEM_RE.findall(features_section))

    # Check prose count in Features Contained section
    prose_matches = SEPARABLE_COUNT_RE.findall(features_section)
    for match in prose_matches:
        stated = int(match)
        if stated != list_count:
            violations.append(
                f"Feature count mismatch in 'Features Contained': "
                f"list has {list_count} item(s) but prose says '{stated} separable'"
            )

    # Check prose count in Eval Notes
    eval_notes = extract_section(content, "## Eval Notes", [])
    if eval_notes:
        note_matches = NOTES_FEATURE_COUNT_RE.findall(eval_notes)
        for match in note_matches:
            stated = int(match)
            if stated != list_count:
                violations.append(
                    f"Feature count mismatch in 'Eval Notes': "
                    f"notes say '{stated}' but feature list has {list_count} item(s)"
                )

    return violations


# ── Runner ────────────────────────────────────────────────────────────────────

CHECKS = [
    check_required_sections,
    check_section_order,
    check_user_story_format,
    check_ac_format,
    check_ambiguities,
    check_feature_count_consistency,
]


def validate_file(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    violations = []
    for check in CHECKS:
        violations.extend(check(content))
    return violations


def main() -> None:
    if len(sys.argv) > 1:
        files = [Path(sys.argv[1])]
    else:
        files = sorted(SAMPLES_DIR.glob("sample-*.md"))

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
