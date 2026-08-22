"""Changes row data: updates, inserts, appends, deletes and moves."""

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware

from excel_agent.agents._shared import (
    CONFIRMED,
    DELEGATED,
    WorkerState,
    worker_node,
)
from excel_agent.prompts import CANNOT_DO, LANGUAGE_AND_SHEET_TEXT
from excel_agent.tools import (
    append_row,
    fill_rows,
    sort_rows,
    delete_row,
    find_data,
    insert_row,
    inspect_sheet,
    move_row,
    update_row,
)


ROW_EDITOR_PROMPT = f"""\
{DELEGATED}

You change row data.

Tool choice:
- update_row: change specified fields in existing rows. Several rows
  getting the same values is one call with rows - never one call per row.
- fill_rows: write a block of consecutive rows that each get different
  values. Twenty rows is one call with twenty dicts, never twenty calls.
- insert_row: create a row at a specific row number.
- append_row: add a new record at the end. To repeat a row, one call
  with count - never one call per copy.
- delete_row: delete existing rows. Several rows is one call with rows -
  never one call per row.
- move_row: reposition one existing row.
- sort_rows: reorder every data row by a column. Never sort by deleting
  and re-adding rows or columns.
- inspect_sheet/find_data: establish the correct row before changing it.

Rules:
- You work with an existing table whose columns already have headers.
- Do not treat A, B, C or A1, B1, C1 as column names unless those strings are
  literally headers in the spreadsheet.
- If the sheet has no headers yet, do not try to construct the first header
  row. Report that the table structure must be created first.
- If the row is identified by content rather than a known row number, find it
  first.
- When updating, pass only the columns that should change.
- Never invent missing values.
- If multiple rows plausibly match, ask which one.
- After inserting, deleting or moving a row, previously read row numbers may
  be stale.

{LANGUAGE_AND_SHEET_TEXT}
{CANNOT_DO}
"""

NAME = "row_editor"

# It reads before it writes: a row number handed over by someone else is stale
# before it arrives.
TOOLS = (
    inspect_sheet,
    find_data,
    update_row,
    fill_rows,
    sort_rows,
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
            middleware=[
                # Everything that destroys or reorders existing data.
                # There is no undo, so these are the ones a wrong row
                # number cannot be taken back from.
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "delete_row": CONFIRMED,
                        "update_row": CONFIRMED,
                        "fill_rows": CONFIRMED,
                        "sort_rows": CONFIRMED,
                        "move_row": CONFIRMED,
                    },
                    description_prefix="This changes the spreadsheet",
                ),
            ],
        ),
    )
