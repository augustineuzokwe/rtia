"""Assert the CI regression job disables the LLM response cache.

The "false-green CI" trap (Issue #230) is the failure mode this defends
against: if a future CI refactor silently drops the cache-disable
mechanism, the eval gate will start replaying stale cached results and
the trustworthiness of every PR metric goes to zero.

Two redundant disables are checked because they cover different
refactor risks:
1. ``env: RTIA_LLM_CACHE: disabled`` on the eval-suite step - survives a
   refactor that swaps the command but keeps the env block.
2. ``--no-cache`` on the ``run_evals.py`` command line - survives a
   refactor that swaps the env block (e.g. via a matrix strategy or a
   reusable workflow) but keeps the command unchanged.

If you intentionally remove one, you must remove this assertion for it
and explicitly document the reasoning in [ADR-0013](../docs/adr-0013-llm-response-cache.md).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_CI_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _load_ci_workflow() -> dict:
    return yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))


def _find_regression_eval_step(workflow: dict) -> dict:
    """Locate the step that runs the eval suite inside the regression job."""
    jobs = workflow.get("jobs", {})
    regression = jobs.get("regression")
    assert regression is not None, "Workflow is missing the 'regression' job"
    for step in regression.get("steps", []):
        if not isinstance(step, dict):
            continue
        with_block = step.get("with") or {}
        command = with_block.get("command", "")
        if "evals/run_evals.py" in command:
            return step
    raise AssertionError("Regression job has no step that runs evals/run_evals.py")


def test_regression_eval_step_sets_cache_disabled_env() -> None:
    workflow = _load_ci_workflow()
    step = _find_regression_eval_step(workflow)
    env = step.get("env") or {}
    value = env.get("RTIA_LLM_CACHE")
    assert value == "disabled", (
        "CI regression job must set RTIA_LLM_CACHE=disabled to avoid the "
        "false-green eval trap. See Issue #230 and ADR-0013."
    )


def test_regression_eval_command_passes_no_cache_flag() -> None:
    workflow = _load_ci_workflow()
    step = _find_regression_eval_step(workflow)
    command = step.get("with", {}).get("command", "")
    assert "--no-cache" in command, (
        "CI regression job must pass --no-cache on the evals/run_evals.py "
        "command line as belt-and-suspenders for the env-var disable. "
        "See ADR-0013 §'CI always disables'."
    )


def test_regression_eval_step_exports_google_api_key() -> None:
    """The eval step must export GOOGLE_API_KEY into the subprocess env.

    A "Verify Gemini API key" step earlier in the job checks that the
    secret is *present*, but presence is not export: the eval subprocess
    only sees env vars set on its own step. Drop this and the live run
    fails at ``ChatGoogleGenerativeAI`` construction (judge init) with
    "API key required for Gemini Developer API" - which no contract test
    that checks only the cache disable would catch.
    """
    workflow = _load_ci_workflow()
    step = _find_regression_eval_step(workflow)
    env = step.get("env") or {}
    assert "GOOGLE_API_KEY" in env, (
        "CI regression eval step must export GOOGLE_API_KEY (from "
        "secrets.GOOGLE_API_KEY_CI || secrets.GOOGLE_API_KEY) so the eval "
        "subprocess can construct the Gemini client and the judge."
    )
