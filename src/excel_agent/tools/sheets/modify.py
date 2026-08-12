"""Tool for changing rows.

Rows are added, edited, removed and moved through the Sheets API, which
rewrites every formula that referred to them. Nothing here has to protect a
calculated cell the way the openpyxl tools do.
"""

from typing import Literal

from langchain_core.tools import tool


@tool
def modify_sheet(
    action: Literal["add", "edit", "remove", "move"],
    row: int | None = None,
    to_row: int | None = None,
    values: dict[str, str | int | float | None] | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Add, edit, remove or move a row in the sheet.

    Call inspect_sheet first, so the row numbers you use are real ones.

    Args:
        action: What to do. "add" puts a new row at the bottom, "edit" changes
            cells in a row that already exists, "remove" deletes a whole row,
            "move" carries a row to a different position.
        row: The row number to change. Needed for edit, remove and move, and
            ignored for add.
        to_row: Where the row should end up. Needed for move only.
        values: Column name mapped to new value. Only the columns listed here
            are changed. Pass null to clear a cell. A value beginning with "="
            is stored as a formula. Ignored for remove and move.
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed.
    """
    raise NotImplementedError
