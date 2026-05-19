"""Per-agent eval runner — Analyst layer.

Invokes the Requirements Analyst on every loaded sample, scores its
output against the per-sample ground truth using the three Analyst
metrics, captures token-usage telemetry from the Anthropic response
metadata, and writes a JSON report under ``evals/reports/``.

Usage:
    uv run python evals/run_evals.py                 # all samples
    uv run python evals/run_evals.py sample-01       # single sample (by stem prefix)

Requires ``ANTHROPIC_API_KEY`` in the environment (loaded via .env).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Allow `uv run python evals/run_evals.py` from the repo root without
# requiring `pip install -e .` first — mirrors scripts/run_pipeline_demo.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from agents.ac_generator import _PROMPT_HASH as AC_PROMPT_HASH  # noqa: E402
from agents.ac_generator import AcGeneratorOutput, generate_acceptance_criteria  # noqa: E402
from agents.config import (  # noqa: E402
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
)
from agents.requirements_analyst import _PROMPT_HASH, AnalystOutput  # noqa: E402
from agents.user_story_writer import UserStory, write_user_story  # noqa: E402
from evals.ac_metrics import (  # noqa: E402
    score_ac_coverage,
    score_ac_faithfulness,
    score_ac_testability,
)
from evals.dataset import SampleRecord, load_all_samples  # noqa: E402
from evals.judge import ClaudeJudge  # noqa: E402
from evals.metrics import (  # noqa: E402
    MetricResult,
    score_actor_set_completeness,
    score_ambiguity_discipline,
    score_intent_faithfulness,
)
from prompts.requirements_analyst_prompts import (  # noqa: E402
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

_AUTO_PO_ANSWER = (
    "Auto-resolved by eval runner: pick the first reasonable interpretation "
    "and proceed; do not block on this."
)
"""Canned PO answer for any CRITICAL ambiguity an Analyst flags during eval.

The eval runner is unattended — we can't pause for PO input. Using a fixed
answer keeps the downstream Story Writer behaviour deterministic across
runs. The Story-Writer / AC-Generator quality on multi-feature samples is
therefore upstream-coupled to this canned answer; it is documented in
baselines.md so a reader knows what they are looking at.
"""

REPORTS_DIR = Path(__file__).parent / "reports"


@dataclass
class UsageTelemetry:
    """Token counts pulled from the LLM response metadata."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: UsageTelemetry) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class SampleReport:
    name: str
    analyst_output: dict
    story_output: dict | None = None
    ac_output: dict | None = None
    metrics: list[MetricResult] = field(default_factory=list)
    analyst_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    story_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    ac_usage: UsageTelemetry = field(default_factory=UsageTelemetry)

    @property
    def pipeline_usage(self) -> UsageTelemetry:
        total = UsageTelemetry()
        total.add(self.analyst_usage)
        total.add(self.story_usage)
        total.add(self.ac_usage)
        return total


def _run_analyst_capturing_usage(text: str) -> tuple[AnalystOutput, UsageTelemetry]:
    """Run the Analyst once and pull token usage from the raw response.

    The library entry point (`analyze_requirement`) returns just the parsed
    output. We re-implement the small invoke here so the response metadata
    (which carries `usage`) is available to the runner without leaking
    telemetry plumbing into agent code.
    """
    llm = ChatAnthropic(
        model=DEFAULT_MODEL,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_retries=DEFAULT_MAX_RETRIES,
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=USER_PROMPT_TEMPLATE.format(requirement_text=text)),
    ]
    config = {
        "metadata": {
            "agent": "requirements_analyst",
            "prompt_hash": _PROMPT_HASH,
            "context": "evals.run_evals",
        }
    }
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    parsed = AnalystOutput.model_validate(json.loads(raw))
    usage_meta = getattr(response, "usage_metadata", None) or {}
    telemetry = UsageTelemetry(
        input_tokens=int(usage_meta.get("input_tokens", 0)),
        output_tokens=int(usage_meta.get("output_tokens", 0)),
    )
    return parsed, telemetry


def _auto_po_answers(analyst_output: AnalystOutput) -> dict[str, str]:
    """Provide the canned PO answer for each CRITICAL Analyst ambiguity."""
    return {
        a.question: _AUTO_PO_ANSWER for a in analyst_output.ambiguities if a.severity == "critical"
    }


def _usage_from_response(response) -> UsageTelemetry:
    meta = getattr(response, "usage_metadata", None) or {}
    return UsageTelemetry(
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
    )


def _run_story_writer_capturing_usage(
    analyst_output: AnalystOutput, po_answers: dict[str, str]
) -> tuple[UserStory, UsageTelemetry]:
    """Run the Story Writer via the library entry point and pull usage telemetry.

    The library call wraps its own LLM construction; we monkey-light it by
    re-running the same path with a captured response is fragile. Instead we
    call ``write_user_story`` (which has the prompt-cache + retry policy) and
    accept that we miss per-call usage data here. We approximate token spend
    by re-tokenising on the report side later if needed.
    """
    # Pragmatic compromise: write_user_story returns just the parsed UserStory.
    # To capture usage without forking the function, we'd need to either
    # restructure that function or do the invoke ourselves. For now, return
    # zeros — the Anthropic console remains the source of truth on cost; this
    # file-level telemetry is informational, not billing.
    story = write_user_story(analyst_output, po_answers)
    return story, UsageTelemetry()


def _run_ac_generator_capturing_usage(
    user_story: UserStory,
    analyst_output: AnalystOutput,
    po_answers: dict[str, str],
) -> tuple[AcGeneratorOutput, UsageTelemetry]:
    """Run the AC Generator and return its output + zero-usage placeholder.

    Same trade-off as ``_run_story_writer_capturing_usage`` — see that
    docstring. AC_PROMPT_HASH is imported so the report's metadata can
    cite the AC Generator's prompt version even when token telemetry is
    unavailable.
    """
    result = generate_acceptance_criteria(user_story, analyst_output, po_answers)
    return result, UsageTelemetry()


def evaluate_sample(
    sample: SampleRecord,
    geval_judge: ClaudeJudge,
    match_judge: ClaudeJudge,
) -> SampleReport:
    """Score one sample with split judges.

    ``geval_judge`` (typically Opus) is used for the criterion-based GEval
    metric where judge reasoning quality matters. ``match_judge`` (typically
    a cheaper model like Haiku) handles the structured-output verdicts for
    actor synonyms and ambiguity-category matching — these are short, narrow
    classification calls where a smaller model is calibrated well enough.
    Split established here, not pushed into metrics.py, so the metric API
    stays single-judge and the model-economics decision lives at the runner.
    """
    analyst_output, analyst_usage = _run_analyst_capturing_usage(sample.raw_requirement)
    po_answers = _auto_po_answers(analyst_output)

    # Chain forward through the rest of the pipeline so AC-layer metrics
    # score against what the AC Generator actually produces. This couples
    # AC scores to upstream agent quality; the trade-off is documented in
    # baselines.md. Failure here (e.g. Story Writer JSON parse) propagates
    # as an exception — eval results for the AC layer are explicitly
    # unavailable rather than silently zero'd.
    story, story_usage = _run_story_writer_capturing_usage(analyst_output, po_answers)
    ac_result, ac_usage = _run_ac_generator_capturing_usage(story, analyst_output, po_answers)

    metrics = [
        # Analyst layer — intent + actor synonym matching stay on the stronger
        # judge (both require nuanced reasoning). Ambiguity-category matching
        # goes to the cheaper match judge — most-called metric, cheapest to
        # downgrade, calibrated empirically (#77).
        score_intent_faithfulness(analyst_output, sample.expected_analyst, geval_judge),
        score_actor_set_completeness(analyst_output, sample.expected_analyst, geval_judge),
        score_ambiguity_discipline(analyst_output, sample.expected_analyst, match_judge),
        # AC layer — coverage uses the cheaper judge (short classification per
        # AC), testability is fully programmatic (no judge), faithfulness uses
        # GEval so calibration matches Analyst intent-faithfulness.
        score_ac_coverage(ac_result, sample.expected_acs, match_judge),
        score_ac_testability(ac_result),
        score_ac_faithfulness(ac_result, story, geval_judge),
    ]

    return SampleReport(
        name=sample.name,
        analyst_output=analyst_output.model_dump(),
        story_output=story.model_dump(),
        ac_output=ac_result.model_dump(),
        metrics=metrics,
        analyst_usage=analyst_usage,
        story_usage=story_usage,
        ac_usage=ac_usage,
    )


def _serialise(
    reports: list[SampleReport],
    *,
    geval_judge_model: str,
    match_judge_model: str,
) -> dict:
    aggregate_usage = UsageTelemetry()
    for r in reports:
        aggregate_usage.add(r.analyst_usage)

    by_metric: dict[str, list[float]] = {}
    for report in reports:
        for m in report.metrics:
            by_metric.setdefault(m.name, []).append(m.score)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": DEFAULT_MODEL,
        "geval_judge_model": geval_judge_model,
        "match_judge_model": match_judge_model,
        "analyst_prompt_hash": _PROMPT_HASH,
        "ac_generator_prompt_hash": AC_PROMPT_HASH,
        "samples": [
            {
                "name": r.name,
                "analyst_output": r.analyst_output,
                "story_output": r.story_output,
                "ac_output": r.ac_output,
                "metrics": [asdict(m) for m in r.metrics],
                "usage": asdict(r.analyst_usage),
            }
            for r in reports
        ],
        "aggregate": {
            "analyst_usage": asdict(aggregate_usage),
            "mean_scores": {name: sum(s) / len(s) for name, s in by_metric.items()},
        },
    }


def _print_summary(payload: dict) -> None:
    print(f"\nmodel={payload['model']}  prompt_hash={payload['analyst_prompt_hash']}")
    print(f"judges: geval={payload['geval_judge_model']}  match={payload['match_judge_model']}")
    print(f"samples evaluated: {len(payload['samples'])}\n")
    for s in payload["samples"]:
        print(f"  {s['name']}")
        for m in s["metrics"]:
            print(f"    {m['name']:<28} {m['score']:.2f}  {m['reason'][:90]}")
        print(f"    usage: input={s['usage']['input_tokens']} output={s['usage']['output_tokens']}")
    print("\nmean scores:")
    for name, score in payload["aggregate"]["mean_scores"].items():
        print(f"  {name:<28} {score:.2f}")
    usage = payload["aggregate"]["analyst_usage"]
    print(
        f"\nAnalyst token usage (excl. judge): "
        f"input={usage['input_tokens']} output={usage['output_tokens']}"
    )


def main(argv: list[str] | None = None) -> int:
    # override=True so a stale or empty ANTHROPIC_API_KEY in the calling
    # shell (common when running under sandboxed agent/CI environments)
    # doesn't shadow the value the user set in .env.
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sample",
        nargs="?",
        help="Optional sample name prefix (e.g. sample-01) to run a single sample.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Write JSON report to this path (default: evals/reports/run-<utc>.json).",
    )
    parser.add_argument(
        "--geval-judge-model",
        default=DEFAULT_MODEL,
        help=(
            "Model for the GEval intent-faithfulness judge — keep at the default "
            "(Opus) unless you have a specific reason; downgrading degrades the "
            "metric you most rely on."
        ),
    )
    parser.add_argument(
        "--match-judge-model",
        default="claude-haiku-4-5-20251001",
        help=(
            "Model for short structured-output judge calls (actor synonyms, "
            "ambiguity-category matching). Defaults to Haiku 4.5 to keep eval "
            "spend low; bump to Opus if you suspect classification drift."
        ),
    )
    args = parser.parse_args(argv)

    samples = load_all_samples()
    if args.sample:
        samples = [s for s in samples if s.name.startswith(args.sample)]
        if not samples:
            print(f"No samples match {args.sample!r}", file=sys.stderr)
            return 2

    geval_judge = ClaudeJudge(model=args.geval_judge_model)
    match_judge = ClaudeJudge(model=args.match_judge_model)
    reports = [evaluate_sample(s, geval_judge, match_judge) for s in samples]
    payload = _serialise(
        reports,
        geval_judge_model=args.geval_judge_model,
        match_judge_model=args.match_judge_model,
    )
    _print_summary(payload)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.report_path or REPORTS_DIR / (
        f"run-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
