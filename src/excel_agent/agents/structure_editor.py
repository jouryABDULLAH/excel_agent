"""Changes columns and how cells look."""

from langchain.agents import create_agent

from excel_agent.agents._shared import WorkerState, worker_node
from excel_agent.subagents.prompts import STRUCTURE_PROMPT
from excel_agent.tools import (
    copy_format,
    delete_column,
    format_range,
    insert_column,
    inspect_sheet,
    move_column,
    rename_column,
    set_column_formula,
)


NAME = "structure_editor"

TOOLS = (
    inspect_sheet,
    insert_column,
    rename_column,
    delete_column,
    move_column,
    set_column_formula,
    format_range,
    copy_format,
)


def build(model):
    """The structure editor, as a graph node."""
    return worker_node(
        NAME,
        create_agent(
            model=model,
            tools=list(TOOLS),
            system_prompt=STRUCTURE_PROMPT,
            state_schema=WorkerState,
        ),
    )
