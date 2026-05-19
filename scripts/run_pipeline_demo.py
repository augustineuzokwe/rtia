"""End-to-end demo of the RTIA pipeline against a sample requirement.

Invokes the compiled LangGraph pipeline. The pipeline pauses at the PO
checkpoint if the Analyst flagged any critical ambiguities; the demo
collects answers from stdin and resumes the graph. If all ambiguities
are normal, the pipeline flows through without pausing. After the
checkpoint, the User Story Writer produces a single user story.

Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).

Run with:
    uv run python scripts/run_pipeline_demo.py                      # default: sample-01
    uv run python scripts/run_pipeline_demo.py sample-02-vague-ambiguous.md
    uv run python scripts/run_pipeline_demo.py /abs/path/to/req.md  # any path works
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Command

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.graph import build_pipeline  # noqa: E402
from agents.observability import tracing_status  # noqa: E402

LANGSMITH_DASHBOARD_URL = "https://smith.langchain.com"

SAMPLES_DIR = REPO_ROOT / "evals" / "sample-requirements"
DEFAULT_SAMPLE = "sample-01-well-structured.md"


def resolve_sample(arg: str | None) -> Path:
    """Resolve a CLI argument to a real sample file path.

    Accepts: nothing (default sample), a bare filename inside the samples
    directory, or an absolute path. Fails loudly if the file doesn't exist
    so the user never silently runs the wrong file.
    """
    if arg is None:
        return SAMPLES_DIR / DEFAULT_SAMPLE
    candidate = Path(arg)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    bare = SAMPLES_DIR / arg
    if bare.exists():
        return bare
    raise FileNotFoundError(
        f"Sample not found: tried {candidate} and {bare}. "
        f"Available: {sorted(p.name for p in SAMPLES_DIR.glob('*.md'))}"
    )


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

    tracing = tracing_status()
    print(tracing.status_line())
    if tracing.enabled:
        print(f"  View traces at {LANGSMITH_DASHBOARD_URL} → project '{tracing.project}'")

    parser = argparse.ArgumentParser(
        description="Run the RTIA pipeline against a sample requirement."
    )
    parser.add_argument(
        "sample",
        nargs="?",
        default=None,
        help=(
            f"Sample filename inside evals/sample-requirements/, or an absolute path. "
            f"Default: {DEFAULT_SAMPLE}"
        ),
    )
    args = parser.parse_args()
    sample_path = resolve_sample(args.sample)

    raw_markdown = sample_path.read_text(encoding="utf-8")
    requirement_text = extract_section(raw_markdown, "Raw Requirement")

    banner(f"INPUT REQUIREMENT — {sample_path.name}")
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

    story = result["user_story"]
    banner("USER STORY (paste-ready markdown)")
    print(story.as_markdown_sections())


if __name__ == "__main__":
    main()
