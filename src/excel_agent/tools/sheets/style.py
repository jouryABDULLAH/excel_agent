"""Tool for how the sheet looks.

Number formats, weight and colour, over a column or a run of rows. The local
tools have no equivalent: this is one of the things the Sheets API can do
that openpyxl makes hard.
"""

from langchain_core.tools import tool


@tool
def modify_style(
    column: str | None = None,
    first_row: int | None = None,
    last_row: int | None = None,
    number_format: str | None = None,
    bold: bool | None = None,
    background: str | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Change how cells are displayed, without changing what they hold.

    Args:
        column: The column to style, by the name in its header. Leave this
            out to style whole rows.
        first_row: The first row to style. Leave this out to style the whole
            column, header included.
        last_row: The last row to style. Leave this out to reach the bottom
            of the data.
        number_format: How numbers should read, written the way Google Sheets
            writes it, such as "#,##0.00" or "0%" or "dd/mm/yyyy".
        bold: Whether the text should be bold.
        background: The fill colour, as a name such as "yellow" or a hex code
            such as "#fff2cc".
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed.
    """
    raise NotImplementedError
