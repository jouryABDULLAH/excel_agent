"""The graph."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from excel_agent.agents import SPECIALISTS
from excel_agent.graph.state import State
from excel_agent.graph.supervisor import build_supervisor, supervisor_node


def route_worker(state: State) -> str:
    """Where the supervisor said to go next.

    supervisor_node sets route on both of its branches, so a missing one means
    that node is broken rather than that the turn is over.
    """
    route = state.get("route")

    if route is None:
        raise ValueError("supervisor produced no route")

    return route


def build_graph(model, checkpointer=None):
    """Wire the supervisor to its specialists and back."""
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

    builder.add_edge(START, "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_worker,
        {
            **{one.NAME: one.NAME for one in SPECIALISTS},
            "end": END,
        },
    )

    # Every worker reports back rather than answering the user or handing on to
    # another worker, so one place decides what happens next.
    for specialist in SPECIALISTS:
        builder.add_edge(specialist.NAME, "supervisor")

    return builder.compile(
        checkpointer=checkpointer or InMemorySaver()
    )
