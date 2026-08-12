"""Tool for summarising a sheet.

Answers questions about a whole column - how many, how much, what range, what
appears most - without the model reading every row and counting.

Read-only tool: nothing here writes to the file.
"""

from collections import Counter
from datetime import date, datetime

from langchain_core.tools import tool

from excel_agent.config import resolve_workbook
from excel_agent.tools.inspect import as_text
from excel_agent.tracing import traced
from excel_agent.workbook import (
    find_header_row,
    header_map,
    is_blank,
    last_data_row,
    load_book,
    load_values,
    resolve_sheet,
)


def is_number(value) -> bool:
    """Whether a value is a number to do arithmetic on.

    True and False are whole numbers as far as Python is concerned, and adding
    them up would be nonsense, so they are left out.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def total_of(values: list) -> str:
    """Add numbers up and render the answer without a trailing .0 or a long tail."""
    added = sum(values)
    return as_text(round(added, 2))


def summarise(values: list) -> str:
    """Describe a column's values in a phrase.

    Numbers get their range and their total, dates get their range, and
    anything else gets whatever turns up most often, since a total means
    nothing for text.
    """
    if not values:
        return "nothing to summarise"

    if all(is_number(value) for value in values):
        return (
            f"{as_text(min(values))} to {as_text(max(values))}, "
            f"adding up to {total_of(values)}"
        )

    if all(isinstance(value, (datetime, date)) for value in values):
        return f"{as_text(min(values))} to {as_text(max(values))}"

    most_common, seen = Counter(as_text(value) for value in values).most_common(1)[0]
    if seen == 1:
        return "every value different"
    return f'"{most_common}" most often, {seen} times'


@tool
@traced
def sheet_stats(
    columns: list[str] | None = None,
    workbook: str | None = None,
    sheet: str | None = None,
) -> str:
    """Summarise the columns of a sheet: how many, how much, what range.

    Use this instead of reading every row and working it out yourself. It
    answers questions like how many rows there are, how many are blank, how
    many different values a column holds, and the smallest, largest and total
    of a column of numbers.

    Args:
        columns: Column names to summarise. Leave empty to summarise all.
        workbook: Which workbook to read, by file name. Leave this out to read
            the one being worked on.
        sheet: Which sheet to read, by name. Leave this out to read the sheet
            the workbook opens on.

    Returns:
        A table with a line per column: how many rows hold a value, how many
        are blank, how many different values there are, and a summary.

        A column the sheet works out for itself has no summary unless the file
        has been opened in Excel since the formulas were last changed, because
        until then the results are not stored in the file. That is said
        plainly rather than reported as a total of zero.

    Examples:
        sheet_stats()
        sheet_stats(columns=["Units", "Region"])
    """
    try:
        path = resolve_workbook(workbook)
    except ValueError as explanation:
        return str(explanation)

    try:
        worksheet = resolve_sheet(load_values(path), sheet)
    except ValueError as explanation:
        return str(explanation)

    formulas = load_book(path)[worksheet.title]

    header_row = find_header_row(worksheet)
    headers = header_map(worksheet, header_row)
    if not headers:
        return (
            f"No column names were found. Row {header_row} is empty, and no "
            "row near the top of the sheet looks like a header."
        )

    last_row = last_data_row(worksheet, header_row)
    total_rows = max(last_row - header_row, 0)
    if total_rows == 0:
        return (
            f"Sheet: {worksheet.title} in {path.name}. It has column names but "
            "no rows of data yet."
        )

    names = list(headers)
    if columns:
        unknown = [name for name in columns if name not in headers]
        if unknown:
            return (
                f"Unknown column(s): {', '.join(unknown)}. "
                f"The sheet has: {', '.join(names)}."
            )
        names = list(columns)

    rows = range(header_row + 1, last_row + 1)
    lines = [
        f"Sheet: {worksheet.title} in {path.name} ({total_rows} rows of data, "
        f"column names in row {header_row})",
        "",
        "| column | filled | blank | different | summary |",
        "|---|---|---|---|---|",
    ]

    for name in names:
        column = headers[name]
        values = [worksheet.cell(row=row, column=column).value for row in rows]
        filled = [value for value in values if not is_blank(value)]

        calculated = any(
            str(formulas.cell(row=row, column=column).value or "").startswith("=")
            for row in rows
        )
        if calculated and not filled:
            summary = "worked out by the sheet, and its results are not stored in the file"
        else:
            summary = summarise(filled)

        different = len({as_text(value) for value in filled})
        lines.append(
            f"| {name} | {len(filled)} | {len(values) - len(filled)} | "
            f"{different} | {summary} |"
        )

    return "\n".join(lines)


CASES = [
    ("every column", {}),
    ("one column", {"columns": ["Units"]}),
    ("two columns", {"columns": ["Region", "Unit Price"]}),
    ("a column that does not exist", {"columns": ["Profit"]}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.stats`.

    Reading only, so running this never changes the file.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(sheet_stats.invoke(arguments))
        print()


if __name__ == "__main__":
    main()