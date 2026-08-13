"""Tool for reading the sheet.

Gives the model a view of the data before it changes anything, using the row
numbers shown down the side of the sheet so modify_row can be pointed at the
right row.
"""

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    Cell,
    cell,
    chart_kind,
    chart_title,
    charts_in,
    find_header_row,
    grid,
    header_map,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
)
from excel_agent.tracing import traced

# Upper bound on max_rows, so one call cannot return the whole of a long sheet.
ROW_LIMIT = 200


def as_text(one: Cell) -> str:
    """Render one cell for the markdown table.

    Google has already formatted every value the way the sheet displays it, so
    a date reads as a date and a currency keeps its symbol without anything
    here knowing about either. A cell holding a formula shows the formula
    only when it has no result to show, which happens while a sheet is still
    working one out.

    Nothing at all becomes an empty string, so a blank cell leaves a gap in
    the table rather than the word None.
    """
    if one.displayed is not None:
        return str(one.displayed)
    if one.formula:
        return one.formula

    return ""


@tool
@traced
def inspect_sheet(
    columns: list[str] | None = None,
    start_row: int = 1,
    max_rows: int = 20,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Read rows from the sheet, with their real row numbers.

    Call this before changing anything, so the row numbers you use are the
    ones the sheet really has.

    Args:
        columns: Column names to show. Leave empty to show every column.
        start_row: The row to start reading from. Row numbers are the ones
            shown down the side of the sheet in Google Sheets.
        max_rows: How many rows to read.
        spreadsheet: Which spreadsheet to read, by name. Leave this out to
            read the one being worked on.
        sheet: Which sheet to read, by name. Leave this out to read the first
            sheet in the spreadsheet.

    Returns:
        A markdown table whose row column holds the real row number, or an
        explanation of why nothing was read.

    Examples:
        inspect_sheet()
        inspect_sheet(columns=["Region", "Units"], max_rows=50)
        inspect_sheet(spreadsheet="Sales Orders", sheet="Q1")
    """
    try:
        spreadsheet_id, title = resolve_spreadsheet(spreadsheet)
        properties = resolve_sheet(spreadsheet_id, sheet)
        rows = grid(spreadsheet_id, properties["title"])
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    where = f"{properties['title']} in {title}"

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    if not headers:
        return (
            f"Sheet: {where}. No column names were found: row {header_row} is "
            "empty, and no row near the top looks like a header."
        )

    if max_rows < 1:
        return f"max_rows was {max_rows}, so no rows were read. Ask for at least 1."

    names = list(headers)
    if columns:
        unknown = [name for name in columns if name not in headers]
        if unknown:
            return (
                f"Unknown column(s): {', '.join(unknown)}. "
                f"The sheet has: {', '.join(names)}."
            )
        names = list(columns)

    last_row = last_data_row(rows, header_row)
    total_rows = max(last_row - header_row, 0)
    if total_rows == 0:
        return f"Sheet: {where}. It has column names but no rows of data yet."

    first_data_row = header_row + 1
    first = max(start_row, first_data_row)
    last = min(first + min(max_rows, ROW_LIMIT) - 1, last_row)

    if first > last_row:
        return (
            f"Sheet: {where} has {total_rows} rows of data, ending at row "
            f"{last_row}, so there is nothing to read from row {start_row}."
        )

    summary = (
        f"Sheet: {where} ({total_rows} rows of data, column names in row "
        f"{header_row})"
    )
    if first > first_data_row or last < last_row:
        summary += f". Showing rows {first} to {last}."

    lines = [
        summary,
        "",
        "| row | " + " | ".join(names) + " |",
        "|" + "---|" * (len(names) + 1),
    ]

    for row in range(first, last + 1):
        values = [as_text(cell(rows, row, headers[name])) for name in names]
        lines.append(f"| {row} | " + " | ".join(values) + " |")

    if last < last_row:
        lines.append("")
        lines.append(
            f"Rows {last + 1} to {last_row} were not shown. "
            f"Call again with start_row={last + 1} to see them."
        )

    # A chart has an id but no name, so the number here is how modify_chart is
    # pointed at one. Listed last, because it is about the sheet rather than
    # about the rows just read.
    drawn = charts_in(spreadsheet_id, properties["title"])
    if drawn:
        lines.append("")
        lines.append(f"{len(drawn)} chart(s) on this sheet:")
        for number, chart in enumerate(drawn, start=1):
            spec = chart.get("spec", {})
            lines.append(f"  {number}. {chart_title(spec)} ({chart_kind(spec)})")

    return "\n".join(lines)


CASES = [
    ("the first rows", {}),
    ("two columns", {"columns": ["Region", "Units"]}),
    ("paging on from row 10", {"start_row": 10, "max_rows": 5}),
    ("more rows than there are", {"max_rows": 5000}),
    ("max_rows of zero", {"max_rows": 0}),
    ("a column that does not exist", {"columns": ["Profit Margin ", "Nonsense"]}),
    ("a sheet that does not exist", {"sheet": "Nonsense"}),
    ("a spreadsheet that does not exist", {"spreadsheet": "Nonsense"}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.sheets.inspect`.

    Reading only, so running this never changes anything. It works on the
    spreadsheet named in EXCEL_AGENT_SPREADSHEET.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(inspect_sheet.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
