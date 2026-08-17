"""Reads rows and edits them."""

from langchain.agents import create_agent

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


def build_row_editor(model):
    return create_agent(
        model=model,
        tools=[
            inspect_sheet,
            find_data,
            update_row,
            insert_row,
            append_row,
            delete_row,
            move_row,
        ],
        system_prompt=ROW_EDITOR_PROMPT,
    )
