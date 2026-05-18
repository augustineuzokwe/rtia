"""End-to-end demo of the RTIA pipeline against a sample requirement.

Invokes the compiled LangGraph pipeline. The pipeline pauses at the PO
checkpoint if the Analyst flagged any critical ambiguities; the demo
collects answers from stdin and resumes the graph. If all ambiguities
are normal, the pipeline flows through without pausing.

Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).

Run with:
    uv run python scripts/run_pipeline_demo.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.graph import build_pipeline  # noqa: E402

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


def collect_po_answers(critical_questions: list[str]) -> dict[str, str]:
    """Prompt the user (acting as PO) to answer each critical question."""
    answers: dict[str, str] = {}
    for question in critical_questions:
        print(f"\nQ: {question}")
        response = input("PO answer (press Enter to skip → 'no answer given'): ").strip()
        answers[question] = response or "no answer given"
    return answers


def main() -> None:
    load_dotenv()

    raw_markdown = SAMPLE_PATH.read_text(encoding="utf-8")
    requirement_text = extract_section(raw_markdown, "Raw Requirement")

    banner("INPUT REQUIREMENT")
    print(requirement_text)

    pipeline = build_pipeline()
    config = {"configurable": {"thread_id": "demo-1"}}

    banner("INVOKING PIPELINE")
    print("Calling Claude (Analyst)…")
    result = pipeline.invoke({"requirement_text": requirement_text}, config=config)

    if "__interrupt__" in result:
        critical = result["__interrupt__"][0].value["critical_ambiguities"]
        banner(f"PO CHECKPOINT — {len(critical)} CRITICAL AMBIGUITY/IES")
        print("The graph has paused. Please answer the critical questions below.")
        answers = collect_po_answers(critical)
        banner("RESUMING PIPELINE")
        result = pipeline.invoke(Command(resume=answers), config=config)
    else:
        banner("NO CRITICAL AMBIGUITIES — PIPELINE FLOWED THROUGH")

    analyst = result["analyst_output"]

    banner("ANALYST OUTPUT")
    print(f"Intent:\n  {analyst.intent}\n")
    print(f"Actors ({len(analyst.actors)}):")
    for actor in analyst.actors:
        print(f"  - {actor}")
    print(f"\nAmbiguities ({len(analyst.ambiguities)}):")
    for amb in analyst.ambiguities:
        print(f"  - [{amb.severity}] {amb.question}")

    po_answers = result.get("po_answers", {})
    if po_answers:
        banner("PO ANSWERS")
        for question, answer in po_answers.items():
            print(f"Q: {question}")
            print(f"A: {answer}\n")


if __name__ == "__main__":
    main()
