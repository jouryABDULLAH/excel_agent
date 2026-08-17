"""Edits columns, formulas and formatting."""

from langchain.agents import create_agent

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


def build_structure_editor(model):
    return create_agent(
        model=model,
        tools=[
            inspect_sheet,
            insert_column,
            rename_column,
            delete_column,
            move_column,
            set_column_formula,
            format_range,
            copy_format,
        ],
        system_prompt=STRUCTURE_PROMPT,
    )
