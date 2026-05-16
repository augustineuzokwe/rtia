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
    "## Eval Notes",
]

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


CHECKS = [
    check_required_sections,
    check_user_story_format,
    check_ac_format,
    check_ambiguities,
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
