"""LangGraph orchestration for the RTIA pipeline.

Defines the shared `PipelineState` and the compiled `pipeline` graph that
chains agents together. Currently the graph has a single node (Analyst);
subsequent agents (User Story Writer, AC Generator, Test Case, Reviewer)
attach as additional nodes without rewiring the existing structure.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agents.requirements_analyst import AnalystOutput, analyze_requirement


class PipelineState(TypedDict, total=False):
    """Shared state flowing through the RTIA pipeline.

    Each agent reads what it needs from prior fields and writes its own
    output slot. `total=False` means fields are populated incrementally as
    the graph runs — the initial invoke only needs `requirement_text`.
    """

    requirement_text: str
    analyst_output: AnalystOutput


def analyst_node(state: PipelineState) -> dict:
    """Run the Requirements Analyst on `state["requirement_text"]`."""
    result = analyze_requirement(state["requirement_text"])
    return {"analyst_output": result}


def build_pipeline():
    """Build and compile the RTIA pipeline graph.

    New agents register here as additional nodes. The order is the pipeline
    described in the README: Analyst → Story Writer → (human checkpoint) →
    AC Generator → Test Case → Reviewer.

    Callers build their own instance (no module-level singleton) so production
    and tests go through the same construction path.
    """
    builder = StateGraph(PipelineState)
    builder.add_node("analyst", analyst_node)
    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", END)
    return builder.compile()
