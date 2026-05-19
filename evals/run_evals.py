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

from agents.config import (  # noqa: E402
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
)
from agents.requirements_analyst import _PROMPT_HASH, AnalystOutput  # noqa: E402
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
    metrics: list[MetricResult] = field(default_factory=list)
    analyst_usage: UsageTelemetry = field(default_factory=UsageTelemetry)


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


def evaluate_sample(sample: SampleRecord, judge: ClaudeJudge) -> SampleReport:
    actual, usage = _run_analyst_capturing_usage(sample.raw_requirement)
    expected = sample.expected_analyst
    metrics = [
        score_intent_faithfulness(actual, expected, judge),
        score_actor_set_completeness(actual, expected, judge),
        score_ambiguity_discipline(actual, expected, judge),
    ]
    return SampleReport(
        name=sample.name,
        analyst_output=actual.model_dump(),
        metrics=metrics,
        analyst_usage=usage,
    )


def _serialise(reports: list[SampleReport]) -> dict:
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
        "analyst_prompt_hash": _PROMPT_HASH,
        "samples": [
            {
                "name": r.name,
                "analyst_output": r.analyst_output,
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
    args = parser.parse_args(argv)

    samples = load_all_samples()
    if args.sample:
        samples = [s for s in samples if s.name.startswith(args.sample)]
        if not samples:
            print(f"No samples match {args.sample!r}", file=sys.stderr)
            return 2

    judge = ClaudeJudge()
    reports = [evaluate_sample(s, judge) for s in samples]
    payload = _serialise(reports)
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
