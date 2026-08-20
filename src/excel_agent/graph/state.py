"""What the graph carries between its nodes."""

from typing import Literal, get_args

from langchain.agents import AgentState
from pydantic import BaseModel, Field


WorkerName = Literal[
    "file_manager",
    "analyst",
    "row_editor",
    "structure_editor",
    "chart_maker",
]

# Where the supervisor can send a turn: a worker, or out.
RouteName = WorkerName | Literal["end"]

# Derived from the type rather than written twice, so the names exist once.
WORKERS: tuple[WorkerName, ...] = get_args(WorkerName)


class State(AgentState):
    """State shared by every node in the graph.

    `messages` comes from AgentState and holds the conversation as the
    supervisor sees it: what the user asked, each delegation it made with the
    report that answered it, and what it replied. A worker's own working --
    its model calls, tool calls and tool results -- stays inside that worker.
    """

    spreadsheet_id: str | None
    spreadsheet_name: str | None

    route: RouteName | None
    task: str | None

    # Read by the runner rather than the last message.
    final_answer: str | None

    # Turn-scoped, cleared by the supervisor when it finishes.
    worker_results: list[str]

    # The column names of each table the application drew this turn, one
    # entry per table, so the supervisor can tell a table it must not repeat
    # from one it wrote itself.
    drawn_tables: list[list[str]]

    # How many times the supervisor has delegated this turn. Also
    # turn-scoped. Past MAX_DELEGATIONS it must answer from the reports it
    # has -- saying what was and was not done, never claiming a success no
    # report establishes.
    delegations: int

    # What the validator found wrong with the answer, or None when nothing
    # is pending. Set on the way back to the supervisor for its one
    # correction pass, and what marks that pass as already spent: feedback
    # from a second look is not acted on.
    correction: str | None


class Delegate(BaseModel):
    """Hand the next step to a specialist."""

    next: WorkerName = Field(
        description="The specialist that should do the next step."
    )
    task: str = Field(
        description="What that specialist should do, in a sentence."
    )


# The supervisor delegates by calling this, and finishes by not calling it.
DELEGATE = "delegate"

# Delegating is how the supervisor answers, not work done on a spreadsheet.
# The runner skips it so it does not show up as an action the user was told
# about.
DECISION_NAMES = frozenset({DELEGATE})
