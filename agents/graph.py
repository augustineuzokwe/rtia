"""LangGraph orchestration for the RTIA pipeline.

Defines the shared `PipelineState` and the compiled pipeline graph that
chains agents together. Currently wires the Analyst and the PO checkpoint;
subsequent agents (User Story Writer, AC Generator, Test Case, Reviewer)
attach as additional nodes without rewiring the existing structure.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.requirements_analyst import AnalystOutput, analyze_requirement


class PipelineState(TypedDict, total=False):
    """Shared state flowing through the RTIA pipeline.

    Each node reads what it needs from prior fields and writes its own
    output slot. `total=False` means fields are populated incrementally as
    the graph runs — the initial invoke only needs `requirement_text`.
    """

    requirement_text: str
    analyst_output: AnalystOutput
    po_answers: dict[str, str]


def analyst_node(state: PipelineState) -> dict:
    """Run the Requirements Analyst on `state["requirement_text"]`."""
    result = analyze_requirement(state["requirement_text"])
    return {"analyst_output": result}


def po_checkpoint_node(state: PipelineState) -> dict:
    """Pause the graph for PO input only when critical ambiguities exist.

    If the Analyst flagged any ambiguity as "critical", the graph pauses
    via LangGraph's `interrupt()` and waits for the caller to resume with
    a dict mapping each critical question to its answer. If all ambiguities
    are "normal", the checkpoint passes through immediately — the Story
    Writer will treat normal ambiguities as story assumptions.

    Requires the compiled graph to have a checkpointer (see build_pipeline).
    """
    critical = [a for a in state["analyst_output"].ambiguities if a.severity == "critical"]
    if not critical:
        return {"po_answers": {}}

    answers = interrupt({"critical_ambiguities": [a.question for a in critical]})
    return {"po_answers": answers}


def build_pipeline():
    """Build and compile the RTIA pipeline graph.

    Compiled with an in-memory checkpointer because `interrupt()` requires
    one to persist state across pause/resume. In-memory is correct for
    demos and tests; production callers should swap for a durable
    checkpointer (Redis, Postgres) so paused threads survive restarts.

    Callers build their own instance (no module-level singleton) so
    production and tests go through the same construction path.
    """
    builder = StateGraph(PipelineState)
    builder.add_node("analyst", analyst_node)
    builder.add_node("po_checkpoint", po_checkpoint_node)
    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", "po_checkpoint")
    builder.add_edge("po_checkpoint", END)
    return builder.compile(checkpointer=MemorySaver())
