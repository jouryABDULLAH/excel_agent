"""Changes row data: updates, inserts, appends, deletes and moves."""

from langchain.agents import create_agent

from excel_agent.agents._shared import WorkerState, worker_node
from excel_agent.subagents.prompts import ROW_EDITOR_PROMPT
from excel_agent.tools import (
    append_row,
    delete_row,
    find_data,
    insert_row,
    inspect_sheet,
    move_row,
    update_row,
)


NAME = "row_editor"

# It reads before it writes: a row number handed over by someone else is stale
# before it arrives.
TOOLS = (
    inspect_sheet,
    find_data,
    update_row,
    insert_row,
    append_row,
    delete_row,
    move_row,
)


def build(model):
    """The row editor, as a graph node."""
    return worker_node(
        NAME,
        create_agent(
            model=model,
            tools=list(TOOLS),
            system_prompt=ROW_EDITOR_PROMPT,
            state_schema=WorkerState,
        ),
    )
