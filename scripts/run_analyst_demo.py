"""Live-API smoke test for the Requirements Analyst agent.

Runs `analyze_requirement()` against a real sample requirement with a real
Claude call (NOT mocked). Prints everything the agent sends and receives so
you can see the system work end-to-end and judge whether the output is
sensible.

Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).

Run with:
    uv run python scripts/run_analyst_demo.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add the repo root to sys.path so we can import `agents.*` and `prompts.*`
# when this script is run directly (it lives in `scripts/`, not at root).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.requirements_analyst import (  # noqa: E402
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    analyze_requirement,
)
from prompts.requirements_analyst_prompts import (  # noqa: E402
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

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
    # python-dotenv reads .env and pushes values into os.environ.
    # ChatAnthropic (inside analyze_requirement) picks ANTHROPIC_API_KEY
    # up from there automatically — we never pass it explicitly.
    load_dotenv()

    raw_markdown = SAMPLE_PATH.read_text(encoding="utf-8")
    requirement_text = extract_section(raw_markdown, "Raw Requirement")

    banner("REQUIREMENT TEXT (input to the agent)")
    print(requirement_text)

    banner("LLM PARAMS")
    print(f"  model        = {DEFAULT_MODEL}")
    print("  temperature  = None (Opus 4.7 rejects this param; use model default)")
    print(f"  timeout      = {DEFAULT_TIMEOUT_SECONDS}s")
    print(f"  max_retries  = {DEFAULT_MAX_RETRIES}")
    print("  max_tokens   = None (use model default)")

    banner("SYSTEM PROMPT (sent as SystemMessage)")
    print(SYSTEM_PROMPT)

    banner("USER PROMPT (sent as HumanMessage)")
    print(USER_PROMPT_TEMPLATE.format(requirement_text=requirement_text))

    banner("CALLING CLAUDE… (this is a real API call, costs apply)")
    result = analyze_requirement(requirement_text)

    banner("PARSED ANALYST OUTPUT")
    print("Intent:")
    print(f"  {result.intent}")
    print("\nActors:")
    for actor in result.actors:
        print(f"  - {actor}")
    print("\nAmbiguities:")
    if result.ambiguities:
        for amb in result.ambiguities:
            print(f"  - {amb}")
    else:
        print("  (none flagged)")

    banner("GROUND TRUTH FROM SAMPLE (full pipeline expected output)")
    print(
        "This is what the WHOLE pipeline should eventually produce — story + ACs.\n"
        "The Analyst only produces intent/actors/ambiguities, so use this as\n"
        "context to judge whether the Analyst's output is coherent with what\n"
        "the downstream agents will need to write.\n"
    )
    expected = extract_section(raw_markdown, "Expected Output (ground truth for eval dataset)")
    print(expected if expected else "(could not extract expected section)")

    banner("WHAT TO LOOK FOR")
    print(
        "  1. Did 'intent' capture the underlying goal (live test-run summary\n"
        "     for QA Leads), not just restate the requirement word-for-word?\n"
        "  2. Did 'actors' include both QA Lead and unauthenticated user?\n"
        "  3. Are 'ambiguities' minimal? This sample is 'well-structured', so\n"
        "     few or none should be flagged. Lots of ambiguities here would\n"
        "     suggest the prompt is over-eager.\n"
    )


if __name__ == "__main__":
    main()
