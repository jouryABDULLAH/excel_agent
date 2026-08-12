"""Tool for changing columns.

Columns are added, removed, moved and renamed through the Sheets API, so
formulas and charts that referred to them are rewritten to follow.
"""

from typing import Literal

from langchain_core.tools import tool


@tool
def modify_column(
    action: Literal["add", "remove", "move", "rename", "set_formula"],
    column: str,
    to_position: int | None = None,
    new_name: str | None = None,
    formula: str | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Add, remove, move or rename a column, or fill it with a formula.

    Call inspect_sheet first, so the column names you use are the real ones.

    Args:
        action: What to do. "set_formula" writes one formula and fills it
            down the whole column.
        column: The column to change, by the name in its header. For "add",
            the name the new column should have.
        to_position: Where the column should end up, counting from the left.
            Needed for move only.
        new_name: What the column should be called. Needed for rename only.
        formula: The formula to fill down, written as it would be typed, such
            as "=B2*C2". Needed for set_formula only.
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed.
    """
    raise NotImplementedError
