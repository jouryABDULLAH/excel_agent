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

    # Columns of the tables the application drew this turn, so the supervisor
    # can tell a table it should not repeat from one it wrote itself.
    drawn_columns: list[str]

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


def table_free(said: str) -> str:
    """The text with any markdown table removed.

    A table line is one that starts with a pipe; everything around it
    survives. Used on both sides of the delivery note: the worker's report
    loses its table because the application draws it, and the supervisor's
    reply loses one it rebuilt from the report.
    """
    return "\n".join(
        line
        for line in said.splitlines()
        if not line.lstrip().startswith("|")
    ).strip()


def _cells(line: str) -> list[str]:
    """The names in one markdown table row."""
    return [
        one.strip().strip("*_ ").casefold()
        for one in line.strip().strip("|").split("|")
    ]


def _is_the_drawn_one(block: list[str], columns: set[str]) -> bool:
    """Whether a table block is the one the application already drew.

    Matched on its heading naming the drawn columns, so a table the planner
    wrote about something else -- suggested columns, a comparison it made up
    -- is left alone.
    """
    named = {one for one in _cells(block[0]) if one}

    return len(named & columns) >= 2


def without_drawn_table(said: str, columns: list[str] | None) -> str:
    """The reply with only the already-drawn table removed.

    The planner cannot see the table the application draws, and writing the
    same rows out again would show the data twice. Removing every table
    instead threw away ones it wrote itself, which is how a reply that was a
    table of suggested columns reached the user as nothing at all.
    """
    wanted = {one.strip().casefold() for one in columns or [] if one.strip()}

    if not wanted:
        return said

    kept: list[str] = []
    block: list[str] = []

    def settle() -> None:
        if block and not _is_the_drawn_one(block, wanted):
            kept.extend(block)

        block.clear()

    for line in said.splitlines():
        if line.lstrip().startswith("|"):
            block.append(line)
            continue

        settle()
        kept.append(line)

    settle()

    return "\n".join(kept).strip()


# Delegating is how the supervisor answers, not work done on a spreadsheet.
# The runner skips it so it does not show up as an action the user was told
# about.
DECISION_NAMES = frozenset({DELEGATE})
