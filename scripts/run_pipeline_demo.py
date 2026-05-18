"""End-to-end demo of the RTIA pipeline against a sample requirement.

Invokes the compiled LangGraph pipeline and prints the Analyst's output.
Currently only the Analyst is wired in; later agents will appear in this
same output as they're added to the graph.

Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).

Run with:
    uv run python scripts/run_pipeline_demo.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

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


def main() -> None:
    load_dotenv()

    raw_markdown = SAMPLE_PATH.read_text(encoding="utf-8")
    requirement_text = extract_section(raw_markdown, "Raw Requirement")

    banner("INPUT REQUIREMENT")
    print(requirement_text)

    banner("INVOKING PIPELINE")
    print("Calling Claude…")
    pipeline = build_pipeline()
    result = pipeline.invoke({"requirement_text": requirement_text})

    analyst = result["analyst_output"]

    banner("ANALYST OUTPUT")
    print(f"Intent:\n  {analyst.intent}\n")
    print(f"Actors ({len(analyst.actors)}):")
    for actor in analyst.actors:
        print(f"  - {actor}")
    print(f"\nAmbiguities ({len(analyst.ambiguities)}):")
    for amb in analyst.ambiguities:
        print(f"  - {amb}")


if __name__ == "__main__":
    main()
