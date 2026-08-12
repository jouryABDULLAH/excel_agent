"""Tool for summarising a column.

Reads the whole column however long it is, which is what makes it the right
answer to a question inspect_sheet would need many calls to answer.
"""

from langchain_core.tools import tool


@tool
def sheet_stats(
    column: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Summarise one column: how many values, and the totals worth knowing.

    Args:
        column: The column to summarise, by the name in its header.
        spreadsheet: Which spreadsheet to read, by name. Leave this out to
            read the one being worked on.
        sheet: Which sheet to read, by name. Leave this out to read the first
            sheet in the spreadsheet.

    Returns:
        A sentence of figures, or an explanation of why none were worked out.
    """
    raise NotImplementedError
