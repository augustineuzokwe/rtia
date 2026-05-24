"""Per-agent eval runner.

Invokes the Analyst → Story Writer → AC Generator chain on every loaded
sample, scores their outputs against per-sample ground truth using the
4-metric suite (post-Gemini cutover), and writes a JSON report under
``evals/reports/``.

Usage:
    uv run python evals/run_evals.py                 # all samples
    uv run python evals/run_evals.py sample-01       # single sample (by stem prefix)

Requires ``GOOGLE_API_KEY`` in the environment (loaded via .env).

History — pre-cutover this used a Claude judge with a split between
"GEval judge" (stronger model for subtle reasoning) and "match judge"
(cheaper model for classification). The GEval metrics were dropped in
the cutover (ADR-0006); the remaining 4 metrics are all classification
or programmatic, so a single Gemini judge handles everything.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Allow `uv run python evals/run_evals.py` from the repo root without
# requiring `pip install -e .` first — mirrors scripts/run_pipeline_demo.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: E402

from agents._llm_utils import coerce_response_text, strip_json_fence  # noqa: E402
from agents._logging import configure_logging  # noqa: E402
from agents.ac_generator import _PROMPT_HASH as AC_PROMPT_HASH  # noqa: E402
from agents.ac_generator import AcGeneratorOutput, generate_acceptance_criteria  # noqa: E402
from agents.config import (  # noqa: E402
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
)
from agents.requirements_analyst import _PROMPT_HASH, AnalystOutput  # noqa: E402
from agents.test_case_writer import _PROMPT_HASH as TC_PROMPT_HASH  # noqa: E402
from agents.test_case_writer import TestCaseWriterOutput, write_test_cases  # noqa: E402
from agents.user_story_writer import UserStory, write_user_story  # noqa: E402
from evals._telemetry_capture import capture_agent_telemetry  # noqa: E402
from evals.ac_metrics import score_ac_coverage, score_ac_testability  # noqa: E402
from evals.dataset import SampleRecord, load_all_samples  # noqa: E402
from evals.judge import GeminiJudge  # noqa: E402
from evals.metrics import (  # noqa: E402
    MetricResult,
    score_actor_set_completeness,
    score_ambiguity_discipline,
    score_injection_resistance,
    score_intent_keyword_overlap,
    score_requirement_fidelity,
)
from evals.tc_metrics import score_tc_coverage_breadth, score_tc_executability  # noqa: E402
from prompts.requirements_analyst_prompts import (  # noqa: E402
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

_AUTO_PO_ANSWER = (
    "Auto-resolved by eval runner: pick the first reasonable interpretation "
    "and proceed; do not block on this."
)
"""Fallback PO answer when no per-sample directive fixture is present.

The eval runner is unattended — we can't pause for PO input. When a sample
has no fixture under ``evals/ground-truth/po-answers/``, every CRITICAL
ambiguity gets this constant string. Multi-feature samples should ship a
fixture so AC-layer metrics measure AC quality against a known scope
rather than against whatever the Story Writer guesses from this vague
fallback. See ``evals/ground-truth/po-answers/README.md`` for the format.
"""

PO_DIRECTIVES_DIR = Path(__file__).parent / "ground-truth" / "po-answers"

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
    tc_output: dict | None = None
    metrics: list[MetricResult] = field(default_factory=list)
    analyst_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    story_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    ac_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    tc_usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    # Phase 13.1 — wall-clock duration of the production agent chain
    # (Analyst → Story Writer → AC Generator → Test Case Writer) for this
    # sample. Excludes judge calls because those are eval scaffolding,
    # not the pipeline being measured. Source: sum of Phase 13.2
    # ``agent_invocation_end`` log records captured during evaluate_sample().
    pipeline_duration_ms: int = 0
    # Per-agent breakdown of the pipeline duration. Keys match the
    # ``agent`` field in ``agent_invocation_end`` records. Empty when
    # capture is unavailable; missing keys mean that agent didn't run.
    # Added to support issue #163 (pipeline speedup) — without per-agent
    # baselines we can't tell which agent is the bottleneck.
    per_agent_duration_ms: dict[str, int] = field(default_factory=dict)

    @property
    def pipeline_usage(self) -> UsageTelemetry:
        total = UsageTelemetry()
        total.add(self.analyst_usage)
        total.add(self.story_usage)
        total.add(self.ac_usage)
        total.add(self.tc_usage)
        return total


def _run_analyst_capturing_usage(text: str) -> tuple[AnalystOutput, UsageTelemetry]:
    """Run the Analyst once and pull token usage from the raw response.

    The library entry point (`analyze_requirement`) returns just the parsed
    output. We re-implement the small invoke here so the response metadata
    (which carries `usage_metadata` on the LangChain response) is available
    to the runner without leaking telemetry plumbing into agent code.
    """
    llm = ChatGoogleGenerativeAI(
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
    raw = coerce_response_text(response.content)
    parsed = AnalystOutput.model_validate(json.loads(strip_json_fence(raw)))
    usage_meta = getattr(response, "usage_metadata", None) or {}
    telemetry = UsageTelemetry(
        input_tokens=int(usage_meta.get("input_tokens", 0)),
        output_tokens=int(usage_meta.get("output_tokens", 0)),
    )
    return parsed, telemetry


def _load_po_directive(sample_name: str) -> str | None:
    """Return the per-sample PO directive string, or None if no fixture exists.

    Looked up by exact ``sample_name`` (the filename stem, e.g.
    ``sample-02-vague-ambiguous``). Fixtures live under
    ``evals/ground-truth/po-answers/<sample_name>.yaml`` and contain a single
    ``po_directive`` string. See that directory's README for rationale.
    """
    path = PO_DIRECTIVES_DIR / f"{sample_name}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    directive = data.get("po_directive")
    if not isinstance(directive, str) or not directive.strip():
        return None
    return directive.strip()


def _auto_po_answers(sample_name: str, analyst_output: AnalystOutput) -> dict[str, str]:
    """Provide the PO answer for each CRITICAL Analyst ambiguity.

    Uses the per-sample directive fixture if present; otherwise falls back to
    the legacy constant ``_AUTO_PO_ANSWER``. The same answer is applied to
    every critical question — the Analyst's exact wording is stochastic, so
    matching per-question would be fragile; the directive carries the scope
    decision regardless of how the question was phrased.
    """
    answer = _load_po_directive(sample_name) or _AUTO_PO_ANSWER
    return {a.question: answer for a in analyst_output.ambiguities if a.severity == "critical"}


def _run_story_writer(
    analyst_output: AnalystOutput, po_answers: dict[str, str]
) -> tuple[UserStory, UsageTelemetry]:
    """Run the Story Writer via the library entry point.

    Library entry returns parsed UserStory only — per-call token telemetry
    isn't available without restructuring the function. Return zero usage;
    the LangSmith trace remains the source of truth on cost for this layer.
    """
    story = write_user_story(analyst_output, po_answers)
    return story, UsageTelemetry()


def _run_ac_generator(
    user_story: UserStory,
    analyst_output: AnalystOutput,
    po_answers: dict[str, str],
) -> tuple[AcGeneratorOutput, UsageTelemetry]:
    """Run the AC Generator via the library entry point.

    Same telemetry trade-off as ``_run_story_writer`` — see that docstring.
    AC_PROMPT_HASH is imported so the report's metadata can cite the AC
    Generator's prompt version even when token telemetry is unavailable.
    """
    result = generate_acceptance_criteria(user_story, analyst_output, po_answers)
    return result, UsageTelemetry()


def _run_test_case_writer(
    user_story: UserStory,
    ac_output: AcGeneratorOutput,
) -> tuple[TestCaseWriterOutput, UsageTelemetry]:
    """Run the Test Case Writer via the library entry point.

    Same telemetry trade-off as the other downstream agents. Failure
    propagates as an exception so TC-layer metrics are explicitly
    unavailable rather than silently zero'd, matching the pattern used
    for the AC layer.
    """
    result = write_test_cases(user_story, ac_output.criteria)
    return result, UsageTelemetry()


def _usage_from_capture(telemetry, agent_name: str) -> UsageTelemetry:
    """Pull a single agent's token usage from the captured log records.

    Returns zeros when the agent didn't emit a record (e.g. the agent
    was skipped this run, or the log capture wasn't active). Zero is
    the right default here because a missing record means the agent
    didn't run, not that it ran and produced no tokens.
    """
    obs = telemetry.for_agent(agent_name)
    if obs is None:
        return UsageTelemetry()
    return UsageTelemetry(
        input_tokens=obs.input_tokens or 0,
        output_tokens=obs.output_tokens or 0,
    )


def evaluate_sample(sample: SampleRecord, judge: GeminiJudge) -> SampleReport:
    """Score one sample with a single Gemini judge.

    All four surviving metrics are either programmatic (``ac_testability``)
    or short classification calls — uniform judge use is appropriate
    post-cutover.
    """
    # Phase 13.1 — capture per-agent telemetry (token counts +
    # duration_ms) by listening to the Phase 13.2 log stream. Wraps the
    # full production-agent chain so every ``agent_invocation_end``
    # record lands in the capture bag. Judge calls are NOT inside this
    # block — they emit no telemetry and are excluded by design.
    with capture_agent_telemetry() as telemetry:
        analyst_output, analyst_usage = _run_analyst_capturing_usage(sample.raw_requirement)
        po_answers = _auto_po_answers(sample.name, analyst_output)

        # Chain forward so AC-layer metrics score against what the AC Generator
        # actually produces. Couples AC scores to upstream agent quality; the
        # trade-off is documented in baselines.md. Failure here (e.g. Story
        # Writer JSON parse) propagates as an exception — eval results for the
        # AC layer are explicitly unavailable rather than silently zero'd.
        story, _ = _run_story_writer(analyst_output, po_answers)
        ac_result, _ = _run_ac_generator(story, analyst_output, po_answers)
        tc_result, _ = _run_test_case_writer(story, ac_result)

    # Lift per-agent telemetry from the captured log records. Downstream
    # agents now have real numbers (zero before Phase 13.1). Analyst
    # telemetry from _run_analyst_capturing_usage is the authoritative
    # source because it predates Phase 13.2's log path; we cross-check
    # against the captured observation when available but defer to the
    # pre-existing direct read.
    story_usage = _usage_from_capture(telemetry, "user_story_writer")
    ac_usage = _usage_from_capture(telemetry, "ac_generator")
    tc_usage = _usage_from_capture(telemetry, "test_case_writer")
    pipeline_duration_ms = telemetry.total_duration_ms
    per_agent_duration_ms = {obs.agent: obs.duration_ms for obs in telemetry.observations}

    # Composite artifact text for the requirement_fidelity metric — the
    # everything-a-junior-engineer-would-read concatenation across all
    # four agents' outputs. Keep this assembly local to the runner; it's
    # not a project-wide abstraction yet.
    artifact_text = " ".join(
        [
            story.description,
            story.objective,
            " ".join(story.assumptions),
            " ".join(f"{c.given} {c.when} {c.then}" for c in ac_result.criteria),
            " ".join(f"{tc.scenario} {' '.join(tc.steps)} {tc.expected}" for tc in tc_result.cases),
        ]
    )

    # Score metrics concurrently (issue #163). 3 of the 8 base metrics
    # are LLM-judge calls (actor_set_completeness, ambiguity_discipline,
    # ac_coverage) that each take ~3-10s on Gemini Flash; the rest are
    # programmatic and finish in milliseconds. Running them in a thread
    # pool lets the slow ones overlap, dropping per-sample judge wall-
    # clock from ~sum() to ~max(). max_workers=4 covers all judge slots
    # plus headroom for the cheap programmatic ones. Order is preserved
    # below for reporting stability. GeminiJudge wraps a stateless
    # ChatGoogleGenerativeAI (see evals/judge.py), so concurrent
    # ``.invoke()`` calls are safe.
    metric_calls = [
        lambda: score_actor_set_completeness(analyst_output, sample.expected_analyst, judge),
        lambda: score_ambiguity_discipline(analyst_output, sample.expected_analyst, judge),
        lambda: score_intent_keyword_overlap(analyst_output, sample.expected_analyst),
        lambda: score_ac_coverage(ac_result, sample.expected_acs, judge),
        lambda: score_ac_testability(ac_result),
        lambda: score_tc_coverage_breadth(tc_result.cases, ac_result.criteria),
        lambda: score_tc_executability(tc_result.cases),
        lambda: score_requirement_fidelity(artifact_text, sample.requirement_key_terms),
    ]
    # Phase 12.1 — injection_resistance only runs on samples that ship an
    # `## Injection Test` block. Mean aggregation in `_serialise` skips
    # metrics absent from a sample, so adding/removing adversarial samples
    # does not distort the headline averages of the other metrics.
    if sample.injection_test is not None:
        metric_calls.append(
            lambda: score_injection_resistance(analyst_output, artifact_text, sample.injection_test)
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        # ``executor.map`` preserves input order, so the resulting
        # ``metrics`` list lines up with the ``metric_calls`` definitions
        # above — the report and the metric-floor check both rely on
        # this ordering.
        metrics = list(pool.map(lambda call: call(), metric_calls))

    return SampleReport(
        name=sample.name,
        analyst_output=analyst_output.model_dump(),
        story_output=story.model_dump(),
        ac_output=ac_result.model_dump(),
        tc_output=tc_result.model_dump(),
        metrics=metrics,
        analyst_usage=analyst_usage,
        story_usage=story_usage,
        ac_usage=ac_usage,
        tc_usage=tc_usage,
        pipeline_duration_ms=pipeline_duration_ms,
        per_agent_duration_ms=per_agent_duration_ms,
    )


def _serialise(reports: list[SampleReport], *, judge_model: str) -> dict:
    aggregate_analyst_usage = UsageTelemetry()
    aggregate_pipeline_usage = UsageTelemetry()
    aggregate_duration_ms = 0
    aggregate_per_agent_duration_ms: dict[str, int] = {}
    for r in reports:
        aggregate_analyst_usage.add(r.analyst_usage)
        aggregate_pipeline_usage.add(r.pipeline_usage)
        aggregate_duration_ms += r.pipeline_duration_ms
        for agent_name, ms in r.per_agent_duration_ms.items():
            aggregate_per_agent_duration_ms[agent_name] = (
                aggregate_per_agent_duration_ms.get(agent_name, 0) + ms
            )

    by_metric: dict[str, list[float]] = {}
    for report in reports:
        for m in report.metrics:
            by_metric.setdefault(m.name, []).append(m.score)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": DEFAULT_MODEL,
        "judge_model": judge_model,
        "analyst_prompt_hash": _PROMPT_HASH,
        "ac_generator_prompt_hash": AC_PROMPT_HASH,
        "test_case_writer_prompt_hash": TC_PROMPT_HASH,
        "samples": [
            {
                "name": r.name,
                "analyst_output": r.analyst_output,
                "story_output": r.story_output,
                "ac_output": r.ac_output,
                "tc_output": r.tc_output,
                "metrics": [asdict(m) for m in r.metrics],
                # Per-agent + pipeline-aggregate usage. ``usage`` (legacy
                # key) is kept as the Analyst-only view to avoid breaking
                # any downstream reader; new readers should prefer
                # ``per_agent_usage`` and ``pipeline_usage``.
                "usage": asdict(r.analyst_usage),
                "per_agent_usage": {
                    "analyst": asdict(r.analyst_usage),
                    "story_writer": asdict(r.story_usage),
                    "ac_generator": asdict(r.ac_usage),
                    "test_case_writer": asdict(r.tc_usage),
                },
                "pipeline_usage": asdict(r.pipeline_usage),
                "pipeline_duration_ms": r.pipeline_duration_ms,
                "per_agent_duration_ms": dict(r.per_agent_duration_ms),
            }
            for r in reports
        ],
        "aggregate": {
            "analyst_usage": asdict(aggregate_analyst_usage),
            "pipeline_usage": asdict(aggregate_pipeline_usage),
            "pipeline_duration_ms": aggregate_duration_ms,
            "per_agent_duration_ms": aggregate_per_agent_duration_ms,
            "mean_scores": {name: sum(s) / len(s) for name, s in by_metric.items()},
        },
    }


def _print_summary(payload: dict) -> None:
    print(f"\nmodel={payload['model']}  prompt_hash={payload['analyst_prompt_hash']}")
    print(f"judge: {payload['judge_model']}")
    print(f"samples evaluated: {len(payload['samples'])}\n")
    for s in payload["samples"]:
        print(f"  {s['name']}")
        for m in s["metrics"]:
            print(f"    {m['name']:<28} {m['score']:.2f}  {m['reason'][:90]}")
        pu = s.get("pipeline_usage", s["usage"])
        duration_s = s.get("pipeline_duration_ms", 0) / 1000.0
        print(
            f"    pipeline: input={pu['input_tokens']} output={pu['output_tokens']} "
            f"duration={duration_s:.1f}s"
        )
        per_agent = s.get("per_agent_duration_ms") or {}
        if per_agent:
            breakdown = "  ".join(f"{name}={ms / 1000:.1f}s" for name, ms in per_agent.items())
            print(f"    per-agent: {breakdown}")
    print("\nmean scores:")
    for name, score in payload["aggregate"]["mean_scores"].items():
        print(f"  {name:<28} {score:.2f}")
    agg_pu = payload["aggregate"].get("pipeline_usage", payload["aggregate"]["analyst_usage"])
    agg_duration_s = payload["aggregate"].get("pipeline_duration_ms", 0) / 1000.0
    print(
        f"\nPipeline token usage (excl. judge): "
        f"input={agg_pu['input_tokens']} output={agg_pu['output_tokens']}"
    )
    print(f"Pipeline wall-clock (excl. judge): {agg_duration_s:.1f}s")
    agg_per_agent = payload["aggregate"].get("per_agent_duration_ms") or {}
    if agg_per_agent:
        breakdown = "  ".join(f"{name}={ms / 1000:.1f}s" for name, ms in agg_per_agent.items())
        print(f"Per-agent wall-clock (aggregate): {breakdown}")


_COST_DISCLOSURE = (
    "Cost disclosure: this eval run makes ≈4 production-agent calls per sample "
    "(Analyst, Story Writer, AC Generator, Test Case Writer) plus ≈4–6 judge "
    "calls per sample (actor synonym tiebreaks, ambiguity category mapping, "
    "AC→category classification). The two TC-layer metrics are programmatic "
    "and add zero judge cost. Default scope is 3 samples ≈ 24–30 total "
    "Gemini 2.5 Flash calls. Estimated paid-tier spend ≈$0.03–0.04 per full "
    "run. Free-tier accounts will hit the 20-RPD cap mid-run — use paid "
    "tier for routine eval work. See docs/adr-0006-provider-switch.md."
)


def main(argv: list[str] | None = None) -> int:
    # override=True so a stale or empty GOOGLE_API_KEY in the calling
    # shell (common when running under sandboxed agent/CI environments)
    # doesn't shadow the value the user set in .env.
    load_dotenv(override=True)
    # Phase 13.1 — install the JSON log handler so capture_agent_telemetry
    # has events to read. The runner is an entry point, same contract as
    # scripts/run_pipeline_demo.py.
    configure_logging()
    print(_COST_DISCLOSURE)
    print()
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
        "--judge-model",
        default=DEFAULT_MODEL,
        help=(
            "Model for the single Gemini judge. Defaults to DEFAULT_MODEL. "
            "Override only for calibration experiments (e.g. testing a newer "
            "Gemini version against the baseline)."
        ),
    )
    args = parser.parse_args(argv)

    samples = load_all_samples()
    if args.sample:
        samples = [s for s in samples if s.name.startswith(args.sample)]
        if not samples:
            print(f"No samples match {args.sample!r}", file=sys.stderr)
            return 2

    judge = GeminiJudge(model=args.judge_model)
    reports = [evaluate_sample(s, judge) for s in samples]
    payload = _serialise(reports, judge_model=args.judge_model)
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
