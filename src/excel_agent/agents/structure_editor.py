"""Changes columns and how cells look."""

from langchain.agents import create_agent

from excel_agent.agents._shared import DELEGATED, WorkerState, worker_node
from excel_agent.prompts import CANNOT_DO, LANGUAGE_AND_SHEET_TEXT
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


STRUCTURE_PROMPT = f"""\
{DELEGATED}

You change columns and cell formatting.

Column tools:
- insert_column
- rename_column
- delete_column
- move_column
- set_column_formula

Formatting tools:
- format_range
- copy_format

Routing rules:
- "same values", "same contents", or "copy the row data" is row work, not
  formatting work.
- "same formatting", "same appearance", "same style", or "make X look like Y"
  means copy_format.
- If the user says only something ambiguous such as "make row 12 like row 3",
  ask whether they mean values or formatting. Do not choose silently.

Formatting rules:
- format_range directly changes number formats, bold, italic, underline,
  strikethrough, font/background colours, alignment, wrapping and borders.
- format_range can clear explicit number formats and backgrounds.
- copy_format copies formatting only; it must not copy values or formulas.
- Existing formatting cannot be described from inspection merely because it
  can be copied.

Column rules:
- Existing columns are identified by their exact spreadsheet header.
- Never guess a header.
- Structural insert/delete/move operations can invalidate old positions.
- set_column_formula takes mode="fill_down" for a formula computing one row,
  and mode="spill" for one that fills the column itself, such as ARRAYFORMULA.
- Creating the initial columns/header row of an empty sheet is structure work.
- When the sheet is empty and the user supplies the desired headers, create
  those columns in the requested order before any data rows are added.
- For an unnamed column, target it by position. Never invent a header name.
- A column operation may identify a named column by its header, its physical
  position, or both. If both are used, they must refer to the same column.

{LANGUAGE_AND_SHEET_TEXT}
{CANNOT_DO}
"""

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
