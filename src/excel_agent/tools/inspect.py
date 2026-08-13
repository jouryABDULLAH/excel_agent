"""Tool for reading the sheet.

Gives the model a view of the data before it changes anything, using real
Excel row numbers so modify_row can be pointed at the right row.
"""

from datetime import date, datetime

from langchain_core.tools import tool

from excel_agent.config import resolve_workbook
from excel_agent.tracing import traced
from excel_agent.workbook import (
    find_header_row,
    header_map,
    last_data_row,
    load_book,
    load_values,
    resolve_sheet,
)

# Upper bound on max_rows, so one call cannot return a huge sheet.
ROW_LIMIT = 200


def as_text(value) -> str:
    """Render one cell value for the markdown table.

    Blanks become empty strings rather than None, dates lose the midnight
    timestamp, and whole numbers stored as floats lose the trailing .0.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@tool
@traced
def inspect_sheet(
    columns: list[str] | None = None,
    start_row: int | None = None,
    max_rows: int = 50,
    workbook: str | None = None,
    sheet: str | None = None,
) -> str:
    """Read rows from the sheet so you can see what is there before changing it.

    Call this before modify_row, so that you work from the real row numbers
    instead of guessing them.

    Args:
        columns: Column names to show. Leave empty to show every column.
        start_row: First Excel row to read. Omit this unless you are paging
            through a long sheet.
        max_rows: How many rows to read at most.
        workbook: Which workbook to read, by file name. Leave this out to read
            the one being worked on, which is what you normally want. Name a
            workbook only when the user named a file.
        sheet: Which sheet to read, by name. Leave this out to read the sheet
            the workbook opens on. Name a sheet only when the user named one.
            A name that reaches no sheet is answered with the names that do
            exist.

    Returns:
        A markdown table. Its row column holds the real Excel row number,
        which is what modify_row expects. The first line says which workbook
        and sheet it came from, which row the column names are in, and how many
        rows of data follow, so you can tell whether you have seen all of them.
    """
    try:
        path = resolve_workbook(workbook)
    except ValueError as explanation:
        return str(explanation)

    try:
        worksheet = resolve_sheet(load_values(path), sheet)
    except ValueError as explanation:
        return str(explanation)

    # Found by the name of the sheet already settled on, so the values and the
    # formulas are two views of the same sheet rather than two lookups that
    # could disagree.
    formulas = load_book(path)[worksheet.title]


    header_row = find_header_row(worksheet)
    headers = header_map(worksheet, header_row)


    if not headers:
        return (
            f"No column names were found. Row {header_row} is empty, and no "
            "row near the top of the sheet looks like a header."
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

    last_row = last_data_row(worksheet, header_row)
    total_rows = max(last_row - header_row, 0)

    if total_rows == 0:
        return (
            f"Sheet: {worksheet.title} in {path.name}. It has column names but no "
            "rows of data yet."
        )

    first_data_row = header_row + 1
    first = max(start_row or first_data_row, first_data_row)
    last = min(first + min(max_rows, ROW_LIMIT) - 1, last_row)

    if first > last_row:
        return (
            f"Sheet: {worksheet.title} in {path.name} has {total_rows} rows of data, "
            f"ending at row {last_row}, so there is nothing to read from row "
            f"{start_row}."
        )

    # The workbook is named on every read, so that two tables in one
    # conversation cannot be mistaken for each other.
    summary = (
        f"Sheet: {worksheet.title} in {path.name} ({total_rows} rows of data, "
        f"column names in row {header_row})"
    )
    if first > first_data_row or last < last_row:
        summary += f". Showing rows {first} to {last}."

    lines = [
        summary,
        "",
        "| row | " + " | ".join(names) + " |",
        "|" + "---|" * (len(names) + 1),
    ]

    showed_a_formula = False
    for row in range(first, last + 1):
        cells = []
        for name in names:
            value = worksheet.cell(row=row, column=headers[name]).value
            if value is None and formulas is not None:
                formula = formulas.cell(row=row, column=headers[name]).value
                if isinstance(formula, str) and formula.startswith("="):
                    value = formula
                    showed_a_formula = True
            cells.append(as_text(value))
        lines.append(f"| {row} | " + " | ".join(cells) + " |")

    if showed_a_formula:
        lines.append("")
        lines.append(
            "A cell shown as a formula is calculated by the sheet itself. Its "
            "result is worked out when the file is opened in Excel, so there "
            "is no value to read here. Do not try to set these cells."
        )

    if last < last_row:
        lines.append("")
        lines.append(
            f"Rows {last + 1} to {last_row} were not shown. "
            f"Call again with start_row={last + 1} to see them."
        )

    return "\n".join(lines)



CASES = [
    ("the whole sheet", {}),
    ("one column", {"columns": ["Product"]}),
    ("three columns", {"columns": ["Product", "Region", "Units"]}),
    ("columns asked for in a different order", {"columns": ["Region", "ID"]}),
    ("the first three rows", {"max_rows": 3}),
    ("paging on from row 5", {"start_row": 5, "max_rows": 3}),
    ("the last row on its own", {"start_row": 11, "max_rows": 1}),
    ("start_row on the header row, which gets clamped", {"start_row": 1, "max_rows": 2}),
    ("start_row past the end of the data", {"start_row": 500}),
    ("max_rows above the limit, which gets capped", {"max_rows": 5000}),
    ("max_rows of zero", {"max_rows": 0}),
    ("a column that does not exist", {"columns": ["Profit"]}),
    ("a real column in the wrong case", {"columns": ["region"]}),
    ("one real column and one made up one", {"columns": ["Region", "Profit"]}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.inspect`.

    Prints what the model would see for each case above. Reading only, so
    running this never changes the file.
    """
    
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(inspect_sheet.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
