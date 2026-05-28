"""Non-interactive smoke test for the RTIA agent pipeline.

Runs Analyst → Story Writer end-to-end against every sample requirement,
asserts that a fully-populated FinalUserStory comes out the other side
(to the extent of the agents we have today), and enforces a hard token
budget so a regression that 2xes spend fails CI loudly instead of
silently burning credit.

The graph itself (`agents/graph.py`) pauses at the PO checkpoint when
the Analyst emits critical ambiguities - that interactivity is the
right call for the demo but wrong for an unattended nightly run. This
script bypasses the checkpoint by calling the agents directly and
auto-answering critical ambiguities with a fixed string the Story
Writer can absorb. The goal is invariant checking ("did the pipeline
break?"), not story quality (Phase 6 evals own that).

Usage:
    uv run python scripts/run_integration_smoke.py
    uv run python scripts/run_integration_smoke.py \
        --model claude-haiku-4-5-20251001 \
        --budget-input-tokens 50000 \
        --budget-output-tokens 5000

Exit codes:
    0  all samples passed all invariants and stayed under budget
    1  at least one sample failed an invariant
    2  budget exceeded
"""

from __future__ import annotations

# All imports below the sys.path mutation are E402 by design - the path
# tweak is what lets `uv run python scripts/run_integration_smoke.py`
# work from the repo root without `pip install -e .`. Disabling E402
# for the file is cleaner than tagging every import.
# ruff: noqa: E402
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from agents.config import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SECONDS
from agents.final_artifact import FinalUserStory
from agents.requirements_analyst import _PROMPT_HASH as ANALYST_PROMPT_HASH
from agents.requirements_analyst import AnalystOutput
from agents.user_story_writer import _PROMPT_HASH as WRITER_PROMPT_HASH
from agents.user_story_writer import SYSTEM_PROMPT as WRITER_SYSTEM_PROMPT
from agents.user_story_writer import USER_PROMPT_TEMPLATE as WRITER_USER_PROMPT_TEMPLATE
from agents.user_story_writer import UserStory, _format_ambiguities, _format_po_answers
from evals.dataset import load_all_samples
from prompts.requirements_analyst_prompts import SYSTEM_PROMPT as ANALYST_SYSTEM_PROMPT
from prompts.requirements_analyst_prompts import (
    USER_PROMPT_TEMPLATE as ANALYST_USER_PROMPT_TEMPLATE,
)

# Default model for nightly runs: cheapest dated ID currently published.
# (Anthropic dated IDs only exist for Haiku 4.5 and below today; see ADR-0001.)
DEFAULT_INTEGRATION_MODEL = "claude-haiku-4-5-20251001"

# Token budget for a single nightly run across all samples. Calibrated to be
# roughly 2x the observed Phase 6 baseline (input≈6.9k, output≈0.9k on Opus
# for Analyst alone; adding Story Writer ≈ doubles it). The budget is a
# regression tripwire, not a SLO - bump it deliberately when a real prompt
# change moves the floor.
DEFAULT_BUDGET_INPUT_TOKENS = 50_000
DEFAULT_BUDGET_OUTPUT_TOKENS = 5_000

# Stand-in answer for any CRITICAL ambiguity the Analyst raises. The Story
# Writer just needs *something* to absorb - story quality is judged by the
# evals job, not this smoke job.
_AUTO_PO_ANSWER = (
    "Auto-resolved by integration smoke: pick the first reasonable interpretation "
    "and proceed; do not block on this."
)


@dataclass
class UsageTelemetry:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: UsageTelemetry) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class SampleResult:
    name: str
    final_story: FinalUserStory | None = None
    usage: UsageTelemetry = field(default_factory=UsageTelemetry)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures and self.final_story is not None


def _make_llm(model: str) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_retries=DEFAULT_MAX_RETRIES,
    )


def _collect_usage(response) -> UsageTelemetry:
    meta = getattr(response, "usage_metadata", None) or {}
    return UsageTelemetry(
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _strip_json_fences(raw: str) -> str:
    """Strip ```json ... ``` fences that smaller models sometimes wrap output in.

    The agent prompts explicitly forbid markdown fences, and Opus respects
    that. Haiku/Sonnet are less reliable on this exact instruction, so the
    smoke script (which deliberately runs against a cheaper model) strips
    them defensively rather than treating a wrapped-but-valid JSON response
    as a failure. The production agent path keeps strict parsing.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    return text


def _run_analyst(requirement_text: str, model: str) -> tuple[AnalystOutput, UsageTelemetry]:
    llm = _make_llm(model)
    messages = [
        SystemMessage(content=ANALYST_SYSTEM_PROMPT),
        HumanMessage(
            content=ANALYST_USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text)
        ),
    ]
    config = {
        "metadata": {
            "agent": "requirements_analyst",
            "prompt_hash": ANALYST_PROMPT_HASH,
            "context": "scripts.run_integration_smoke",
        }
    }
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return AnalystOutput.model_validate(json.loads(_strip_json_fences(raw))), _collect_usage(
        response
    )


def _auto_po_answers(analyst_output: AnalystOutput) -> dict[str, str]:
    """Answer every CRITICAL ambiguity with the same canned response.

    Normal-severity ambiguities flow through as Story Writer assumptions
    without needing an answer - same behaviour as the graph's PO checkpoint.
    """
    return {
        a.question: _AUTO_PO_ANSWER for a in analyst_output.ambiguities if a.severity == "critical"
    }


def _run_story_writer(
    analyst_output: AnalystOutput, po_answers: dict[str, str], model: str
) -> tuple[UserStory, UsageTelemetry]:
    llm = _make_llm(model)
    user_prompt = WRITER_USER_PROMPT_TEMPLATE.format(
        intent=analyst_output.intent,
        actors="\n".join(f"- {actor}" for actor in analyst_output.actors) or "(none)",
        ambiguities=_format_ambiguities(analyst_output),
        po_answers=_format_po_answers(po_answers),
    )
    messages = [
        SystemMessage(content=WRITER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    config = {
        "metadata": {
            "agent": "user_story_writer",
            "prompt_hash": WRITER_PROMPT_HASH,
            "context": "scripts.run_integration_smoke",
        }
    }
    response = llm.invoke(messages, config=config)
    raw = response.content if isinstance(response.content, str) else str(response.content)
    return UserStory.model_validate(json.loads(_strip_json_fences(raw))), _collect_usage(response)


def _build_final_story(story: UserStory) -> FinalUserStory:
    """Assemble the FinalUserStory contract from the Story Writer output.

    Acceptance criteria + test cases stay empty - those agents land in
    Phases 8/9. The smoke job's invariant is "description + objective
    populated", not "full artifact ready".
    """
    return FinalUserStory(
        description=story.description,
        objective=story.objective,
        assumptions=list(story.assumptions),
    )


def _check_invariants(sample_name: str, final_story: FinalUserStory) -> list[str]:
    """Return a list of invariant violations (empty when the sample passed)."""
    failures: list[str] = []
    if not final_story.description.strip():
        failures.append("description is empty")
    if not final_story.objective.strip():
        failures.append("objective is empty")
    # Cheap shape sanity - the Story Writer prompt enforces this format, so
    # missing it indicates a real regression worth failing on.
    desc_lower = final_story.description.lower()
    if not desc_lower.startswith(("as a ", "as an ")):
        failures.append(
            f"description does not start with 'As a/an ...': {final_story.description[:60]!r}"
        )
    if sample_name != "sample-01-well-structured" and not final_story.assumptions:
        # sample-01 has no Analyst-flagged ambiguities so assumptions can be
        # legitimately empty there. The other two should accumulate at least
        # one assumption from the normal-severity ambiguities flowing through.
        # Soft signal - recorded as a failure but the budget check still runs.
        failures.append("expected at least one assumption from normal-severity ambiguities")
    return failures


def evaluate_sample(name: str, requirement_text: str, model: str) -> SampleResult:
    result = SampleResult(name=name)
    try:
        analyst_output, analyst_usage = _run_analyst(requirement_text, model)
        result.usage.add(analyst_usage)
        po_answers = _auto_po_answers(analyst_output)
        story, writer_usage = _run_story_writer(analyst_output, po_answers, model)
        result.usage.add(writer_usage)
        result.final_story = _build_final_story(story)
        result.failures.extend(_check_invariants(name, result.final_story))
    except Exception as exc:  # noqa: BLE001 - we want to surface ANY exception as a smoke failure
        result.failures.append(f"unexpected exception: {type(exc).__name__}: {exc}")
    return result


def _print_summary(results: list[SampleResult], total_usage: UsageTelemetry) -> None:
    print()
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name}  input={r.usage.input_tokens} output={r.usage.output_tokens}")
        for f in r.failures:
            print(f"      • {f}")
    print()
    print(f"total usage: input={total_usage.input_tokens} output={total_usage.output_tokens}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_INTEGRATION_MODEL)
    parser.add_argument("--budget-input-tokens", type=int, default=DEFAULT_BUDGET_INPUT_TOKENS)
    parser.add_argument("--budget-output-tokens", type=int, default=DEFAULT_BUDGET_OUTPUT_TOKENS)
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help=(
            "Allow the LLM response cache for this run. Default OFF: the "
            "integration smoke exists to verify live behaviour, not snapshot "
            "replay (Issue #230 table)."
        ),
    )
    args = parser.parse_args(argv)
    if not args.use_cache:
        os.environ["RTIA_LLM_CACHE"] = "disabled"

    samples = load_all_samples()
    print(f"running integration smoke against {len(samples)} samples using model={args.model}")

    results = [evaluate_sample(s.name, s.raw_requirement, args.model) for s in samples]
    total_usage = UsageTelemetry()
    for r in results:
        total_usage.add(r.usage)

    _print_summary(results, total_usage)

    over_input = total_usage.input_tokens > args.budget_input_tokens
    over_output = total_usage.output_tokens > args.budget_output_tokens
    if over_input or over_output:
        print(
            f"\nBUDGET EXCEEDED - input={total_usage.input_tokens}/{args.budget_input_tokens} "
            f"output={total_usage.output_tokens}/{args.budget_output_tokens}",
            file=sys.stderr,
        )
        return 2

    if any(not r.passed for r in results):
        failed_names = [r.name for r in results if not r.passed]
        print(f"\nINTEGRATION SMOKE FAILED: {failed_names}", file=sys.stderr)
        return 1

    print("\nINTEGRATION SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
