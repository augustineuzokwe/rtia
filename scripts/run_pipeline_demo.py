"""End-to-end demo of the RTIA pipeline against a sample requirement.

Invokes the compiled LangGraph pipeline. The pipeline pauses at the PO
checkpoint if the Analyst flagged any critical ambiguities; the demo
collects answers from stdin and resumes the graph. If all ambiguities
are normal, the pipeline flows through without pausing. After the
checkpoint, the User Story Writer produces a single user story.

Requires `GOOGLE_API_KEY` in `.env` (see `.env.example`).

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

from agents._secret_scan import SecretInInputError, raise_if_secrets_found  # noqa: E402
from agents.graph import build_pipeline  # noqa: E402
from agents.observability import (  # noqa: E402
    ProductionTracingError,
    assert_safe_for_env,
    tracing_status,
)

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


def collect_story_review_response(payload: dict) -> dict:
    """Show the rendered story preview; collect accept-or-override.

    Empty input (default in non-interactive mode like `yes ""`) is
    treated as accept — so smoke runs don't get stuck. To override,
    answer 'n' and the demo prompts for new description / objective.
    """
    print(payload["rendered_artifact"])
    print()
    answer = input("Accept this story? (Y/n; n to override): ").strip().lower()
    if answer in ("", "y", "yes"):
        return {"accepted": True}
    new_description = input("New description (Enter to keep current): ").strip()
    new_objective = input("New objective (Enter to keep current): ").strip()
    return {
        "accepted": False,
        "description": new_description or payload["description"],
        "objective": new_objective or payload["objective"],
    }


_COST_DISCLOSURE = (
    "Cost disclosure: this demo makes 5 Gemini 3.5 Flash calls "
    "(Analyst + Story Writer + AC Generator + Test Case Writer + Reviewer). "
    "Estimated paid-tier spend ≈$0.007 per run on AI Studio's standard "
    "pricing. Free-tier accounts: 20 RPD per project per model — see "
    "docs/adr-0006-provider-switch.md for the rationale."
)


def main() -> None:
    load_dotenv()

    # Phase 12.4 — refuse to start with RTIA_ENV=production AND
    # LANGSMITH_TRACING truthy. See docs/adr-0008-pii-langsmith.md.
    # Asserted BEFORE the cost banner so a misconfigured prod
    # deployment fails fast without printing anything that implies the
    # pipeline is about to run.
    try:
        assert_safe_for_env()
    except ProductionTracingError as exc:
        print(f"\nABORTED: {exc}", file=sys.stderr)
        sys.exit(2)

    print(_COST_DISCLOSURE)
    print()

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

    # Phase 12.3 — refuse to invoke the pipeline if the input contains a
    # high-confidence secret pattern. The scanner runs BEFORE any LLM
    # call or LangSmith trace, so a detected credential never leaves the
    # local process. See agents/_secret_scan.py and issue #124 for the
    # block-not-warn policy decision.
    try:
        raise_if_secrets_found(requirement_text)
    except SecretInInputError as exc:
        print(
            "\nABORTED: secret pattern detected in input.",
            file=sys.stderr,
        )
        for finding in exc.findings:
            print(
                f"  - {finding.pattern_name}: {finding.redacted_match} "
                f"(offset {finding.start}-{finding.end})",
                file=sys.stderr,
            )
        print(
            "\nNo external call was made (no Gemini, no LangSmith). "
            "Redact or rotate the credential(s) and re-submit.",
            file=sys.stderr,
        )
        sys.exit(2)

    banner(f"INPUT REQUIREMENT — {sample_path.name}")
    print(requirement_text)

    pipeline = build_pipeline()
    config = {"configurable": {"thread_id": "demo-1"}}

    banner("INVOKING PIPELINE")
    print("Calling Gemini (Analyst)…")
    result = pipeline.invoke({"requirement_text": requirement_text}, config=config)

    # The pipeline has two interrupts (PO Checkpoint + Story Review Checkpoint).
    # Loop until the run completes — each iteration handles whichever checkpoint
    # fired, dispatching on the interrupt payload's shape.
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        if "critical_ambiguities" in payload:
            critical = payload["critical_ambiguities"]
            banner(f"PO CHECKPOINT — {len(critical)} CRITICAL AMBIGUITY/IES")
            print("The graph has paused. Please answer the critical questions below.")
            resume_value: object = collect_po_answers(critical)
        elif "rendered_artifact" in payload:
            banner("STORY REVIEW CHECKPOINT — review the rendered story below")
            resume_value = collect_story_review_response(payload)
        else:
            raise RuntimeError(f"Unknown interrupt payload shape: {sorted(payload)}")
        banner("RESUMING PIPELINE")
        result = pipeline.invoke(Command(resume=resume_value), config=config)

    analyst = result["analyst_output"]

    banner("ANALYST OUTPUT")
    print(f"Intent:\n  {analyst.intent}\n")
    print(f"Actors ({len(analyst.actors)}):")
    for actor in analyst.actors:
        print(f"  - {actor}")
    print(f"\nAmbiguities ({len(analyst.ambiguities)}):")
    for amb in analyst.ambiguities:
        print(f"  - [{amb.severity}] {amb.question}")

    if analyst.implied_stories:
        print(f"\nImplied stories ({len(analyst.implied_stories)}) — PO must pick one:")
        for s in analyst.implied_stories:
            print(f"  - {s.title}: {s.summary}")

    po_answers = result.get("po_answers", {})
    if po_answers:
        banner("PO ANSWERS")
        for question, answer in po_answers.items():
            print(f"Q: {question}")
            print(f"A: {answer}\n")

    artifact = result["final_artifact"]
    banner("FINAL ARTIFACT (paste-ready markdown)")
    print(artifact.as_markdown())

    if "review_report" in result:
        report = result["review_report"]
        banner(f"REVIEWER REPORT  [{report.overall_quality.upper()}]")
        if report.coverage_gaps:
            print("Coverage gaps:")
            for gap in report.coverage_gaps:
                print(f"  - {gap}")
        if report.weak_acs:
            print("Weak ACs:")
            for item in report.weak_acs:
                print(f"  - {item}")
        if report.untestable_criteria:
            print("Untestable criteria:")
            for item in report.untestable_criteria:
                print(f"  - {item}")
        if report.recommendations:
            print("Recommendations:")
            for rec in report.recommendations:
                print(f"  - {rec}")
        if not any(
            [
                report.coverage_gaps,
                report.weak_acs,
                report.untestable_criteria,
                report.recommendations,
            ]
        ):
            print("No issues found — artifact looks solid.")


if __name__ == "__main__":
    main()
