"""End-to-end demo: Analyst -> Faithfulness Judge.

Runs the Requirements Analyst on sample-01 with a real Claude call, then
grades the Analyst's output using the Faithfulness Judge (another real
Claude call). Prints both stages so you can see LLM-as-judge mechanics
without any framework abstraction.

Requires ANTHROPIC_API_KEY in `.env` (see `.env.example`).

Run with:
    uv run python scripts/run_judge_demo.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.requirements_analyst import analyze_requirement  # noqa: E402
from evals.judge import judge_faithfulness  # noqa: E402

SAMPLE_PATH = REPO_ROOT / "evals" / "sample-requirements" / "sample-01-well-structured.md"

SECTION_DIVIDER = "=" * 70


def extract_section(markdown: str, header: str) -> str:
    """Pull out the body between `## {header}` and the next `---` divider."""
    pattern = rf"^## {re.escape(header)}\s*\n(.*?)\n---"
    match = re.search(pattern, markdown, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else ""


def banner(title: str) -> None:
    print()
    print(SECTION_DIVIDER)
    print(title)
    print(SECTION_DIVIDER)


def main() -> None:
    load_dotenv()

    raw_markdown = SAMPLE_PATH.read_text(encoding="utf-8")
    requirement_text = extract_section(raw_markdown, "Raw Requirement")

    banner("STAGE 1 — ANALYST: produce intent / actors / ambiguities")
    print("Calling Claude (Analyst)…")
    analyst_result = analyze_requirement(requirement_text)
    print("\nAnalyst output (summary):")
    print(f"  intent       : {analyst_result.intent}")
    print(f"  actors ({len(analyst_result.actors)})    : {analyst_result.actors}")
    print(f"  ambiguities  : {len(analyst_result.ambiguities)} flagged")

    banner("STAGE 2 — JUDGE: grade Analyst output for faithfulness")
    print("Calling Claude (Judge)…")
    verdict = judge_faithfulness(
        input_text=requirement_text,
        agent_output=analyst_result.model_dump_json(indent=2),
    )

    banner("JUDGE VERDICT")
    print(f"Faithfulness score: {verdict.score} / 5")
    print(f"\nReasoning:\n  {verdict.reasoning}")
    if verdict.unsupported_claims:
        print(f"\nUnsupported claims ({len(verdict.unsupported_claims)}):")
        for claim in verdict.unsupported_claims:
            print(f"  - {claim}")
    else:
        print("\nUnsupported claims: (none)")

    banner("WHAT THIS DEMONSTRATES")
    print(
        "  - LLM-as-judge in ~80 lines, no framework.\n"
        "  - The judge is just another Claude call with a different prompt.\n"
        "  - Score is a NUMBER, gateable in CI ('fail if score < 4').\n"
        "  - unsupported_claims is a LIST, surfacing specific hallucinations\n"
        "    (e.g. 'QA stakeholders' that showed up in an earlier Analyst run).\n"
        "  - Run this across all 3 sample-requirements files and you have a\n"
        "    faithfulness pass-rate across the dataset — the foundation of\n"
        "    a real eval suite.\n"
    )


if __name__ == "__main__":
    main()
