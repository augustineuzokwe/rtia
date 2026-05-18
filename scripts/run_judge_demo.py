"""End-to-end demo: Analyst -> Faithfulness Judge + Calibration Check.

Runs three stages of real Claude calls:

1. Analyst on sample-01 (produces intent/actors/ambiguities).
2. Judge on the Analyst's output (faithfulness score 1-5).
3. **Calibration check**: judge graded against a hardcoded KNOWN-BAD
   output that contains deliberate hallucinations ("QA stakeholders",
   "QA Lead", invented mechanisms). The judge MUST flag these — if
   it returns score 5/5 with no unsupported claims, the judge prompt
   is miscalibrated and needs to be tightened further.

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

# Hardcoded adversarial output for the calibration check (Stage 3).
# Every claim flagged with `# BAD:` is deliberately unfaithful — the
# input requirement never says "QA Lead", "QA stakeholders", "WebSocket",
# "Redis", or "JWT". A correctly calibrated judge must catch these.
ADVERSARIAL_KNOWN_BAD_OUTPUT = """\
{
  "intent": "Provide QA stakeholders and QA Leads with a real-time view of test runs backed by Redis and JWT.",
  "actors": [
    "QA Lead",
    "Engineering Manager",
    "Authenticated user",
    "WebSocket gateway",
    "Redis cache"
  ],
  "ambiguities": [
    "What is the SLA for dashboard load time?"
  ]
}
"""  # noqa: E501


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

    banner("STAGE 3 — CALIBRATION CHECK: judge a KNOWN-BAD output")
    print(
        "Running the judge against a hardcoded adversarial output that\n"
        "contains deliberate hallucinations: 'QA stakeholders', 'QA Lead',\n"
        "'Engineering Manager', 'WebSocket gateway', 'Redis cache', 'JWT'.\n"
        "None of these appear in the input requirement.\n\n"
        "A correctly calibrated judge MUST flag these. If the verdict below\n"
        "is 5/5 with no unsupported claims, the judge prompt is still too\n"
        "lenient and needs further tightening.\n"
    )
    print("Calling Claude (Judge on known-bad output)…")
    calibration_verdict = judge_faithfulness(
        input_text=requirement_text,
        agent_output=ADVERSARIAL_KNOWN_BAD_OUTPUT,
    )

    banner("CALIBRATION VERDICT")
    print(f"Faithfulness score: {calibration_verdict.score} / 5")
    print(f"\nReasoning:\n  {calibration_verdict.reasoning}")
    if calibration_verdict.unsupported_claims:
        print(f"\nUnsupported claims ({len(calibration_verdict.unsupported_claims)}):")
        for claim in calibration_verdict.unsupported_claims:
            print(f"  - {claim}")
    else:
        print("\nUnsupported claims: (none)")

    banner("CALIBRATION RESULT")
    if calibration_verdict.score >= 5 or not calibration_verdict.unsupported_claims:
        print(
            "  ❌ JUDGE IS MISCALIBRATED.\n"
            "     The judge gave a high score to a known-bad output, or did not\n"
            "     surface any of the deliberately-planted hallucinations.\n"
            "     → Tighten prompts/judge_prompts.py further and re-run.\n"
        )
    else:
        print(
            "  ✅ Judge correctly flagged the planted hallucinations.\n"
            "     Score is below 5 and unsupported_claims is non-empty.\n"
            "     This judge can now be trusted (on this adversarial pattern)\n"
            "     to flag similar inventions in real Analyst runs.\n"
        )

    banner("WHAT THIS DEMONSTRATES")
    print(
        "  - LLM-as-judge in ~80 lines, no framework.\n"
        "  - The judge is just another Claude call with a different prompt.\n"
        "  - Score is a NUMBER, gateable in CI ('fail if score < 4').\n"
        "  - unsupported_claims is a LIST, surfacing specific hallucinations.\n"
        "  - **The judge itself needs testing.** Stage 3 is the calibration\n"
        "    check — it verifies the judge catches known-bad cases. This is\n"
        "    how you avoid the 'who watches the watchmen' trap of LLM-as-judge.\n"
        "  - Run this across all 3 sample-requirements files and you have a\n"
        "    faithfulness pass-rate across the dataset — the foundation of\n"
        "    a real eval suite.\n"
    )


if __name__ == "__main__":
    main()
