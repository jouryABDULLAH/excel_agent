"""Tool for reading the sheet.

Fetched twice: once for the values as they are displayed, once for the
formulas behind them, so a calculated cell is shown as its formula.
"""

from langchain_core.tools import tool


@tool
def inspect_sheet(
    start_row: int = 1,
    max_rows: int = 20,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Read rows from the sheet, with their real row numbers.

    Args:
        start_row: The row to start reading from. Row numbers are the ones
            shown down the side of the sheet in Google Sheets.
        max_rows: How many rows to read.
        spreadsheet: Which spreadsheet to read, by name. Leave this out to
            read the one being worked on.
        sheet: Which sheet to read, by name. Leave this out to read the first
            sheet in the spreadsheet.

    Returns:
        A table of the rows, or an explanation of why none were read.
    """
    raise NotImplementedError
