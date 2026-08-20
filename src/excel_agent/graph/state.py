"""What the graph carries between its nodes."""

from typing import Literal

from langchain.agents import AgentState
from pydantic import BaseModel, Field


WORKERS = (
    "file_manager",
    "analyst",
    "row_editor",
    "structure_editor",
    "chart_maker",
)


class State(AgentState):
    """State shared by every node in the graph.

    `messages` comes from AgentState and holds the conversation: what the user
    asked and what the supervisor answered. A worker's own messages are not
    here, because each worker runs as a separate agent with a private history.
    """

    spreadsheet_id: str | None
    spreadsheet_name: str | None

    route: str | None
    task: str | None

    # Read by the runner rather than the last message.
    final_answer: str | None

    # Turn-scoped, cleared by the supervisor when it finishes.
    worker_results: list[str]

    # How many times the supervisor has delegated this turn. Also
    # turn-scoped: past MAX_DELEGATIONS it must answer with what it has.
    delegations: int


class Delegate(BaseModel):
    """Hand the next step to a specialist."""

    next: Literal[WORKERS] = Field(  # type: ignore[valid-type]
        description="The specialist that should do the next step."
    )
    task: str = Field(
        description="What that specialist should do, in a sentence."
    )


# The supervisor delegates by calling this, and finishes by not calling it.
DELEGATE = "delegate"

# Appended to a report whose table was cut because the application draws it:
# the supervisor cannot see the drawing, and an intro pointing at nothing
# reads as a worker that returned nothing, so it sends the task out again.
# The supervisor strips it from the final answer; the user never reads it.
DELIVERED = (
    "(The rows are shown to the user in a table drawn below your reply. "
    "Done. If you mention it, say the table below, never above.)"
)

# Delegating is how the supervisor answers, not work done on a spreadsheet.
# The runner skips it so it does not show up as an action the user was told
# about.
DECISION_NAMES = frozenset({DELEGATE})
