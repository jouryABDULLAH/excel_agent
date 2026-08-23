"""The graph."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from excel_agent.agents import SPECIALISTS
from excel_agent.graph.state import State
from excel_agent.graph.supervisor import build_supervisor, supervisor_node
from excel_agent.graph.validator import route_correction, validator_node
from excel_agent.model import build_model


def route_worker(state: State) -> str:
    """Where the supervisor said to go next.

    supervisor_node sets route on both of its branches, so a missing one means
    that node is broken rather than that the turn is over.
    """
    route = state.get("route")

    if route is None:
        raise ValueError("supervisor produced no route")

    return route


# Tests can switch the judge off with judge=None.
# not specifying judge in build_graph(.., judge=..) would set the judge to use the same model the orchestrator uses
SAME_MODEL = object()


def build_graph(model=None, checkpointer=None, judge=SAME_MODEL):
    """Wire the supervisor to its specialists and back."""
    model = model or build_model()

    if judge is SAME_MODEL:
        judge = model

    builder = StateGraph(State)

    builder.add_node(
        "supervisor",
        supervisor_node(build_supervisor(model)),
    )

    for specialist in SPECIALISTS:
        builder.add_node(
            specialist.NAME,
            specialist.build(model),
        )

    builder.add_node("validator", validator_node(judge))

    builder.add_edge(START, "supervisor")

    # "end" still means the supervisor is done; the validator is what being
    # done now goes through on the way out.
    builder.add_conditional_edges(
        "supervisor",
        route_worker,
        {
            **{one.NAME: one.NAME for one in SPECIALISTS},
            "end": "validator",
        },
    )

    builder.add_conditional_edges(
        "validator",
        route_correction,
        {"supervisor": "supervisor", "end": END},
    )

    # Every worker reports back to the supervisor.
    for specialist in SPECIALISTS:
        builder.add_edge(specialist.NAME, "supervisor")

    return builder.compile(
        checkpointer=checkpointer or InMemorySaver()
    )
