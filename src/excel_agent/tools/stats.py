"""Tool for summarising a column.

Reads the whole column however long it is, which is what makes it the right
answer to a question inspect_sheet would need many calls to answer.
"""

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    Cell,
    cell,
    find_header_row,
    grid,
    header_map,
    is_blank,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
)

# How many of the most common values to name for a column of text.
COMMON_LIMIT = 3


def is_number(one: Cell) -> bool:
    """Whether a cell holds a number to do arithmetic on.

    A date is a number underneath, a count of days, so it is left out: adding
    up a column of dates gives a five figure number that means nothing. A
    boolean is a number to Python and nonsense to add up as well.
    """
    return (
        isinstance(one.value, (int, float))
        and not isinstance(one.value, bool)
        and not one.is_date
    )


def rounded(number: float) -> str:
    """Render a total without a trailing .0 or a long tail of decimals."""
    number = round(number, 2)
    return str(int(number)) if number == int(number) else str(number)


def describe(cells: list[Cell]) -> str:
    """Say what a column holds, in a phrase.

    Numbers get their range and their total, dates get their range, and
    anything else gets whatever turns up most often, since a total means
    nothing for text.

    The smallest and largest are shown the way the sheet shows them, so a
    price keeps its currency and a date reads as a date. The total is worked
    out from the values underneath, where the sheet's formatting cannot reach.
    """
    if not cells:
        return "nothing to summarise"

    if all(is_number(one) for one in cells):
        least = min(cells, key=lambda one: one.value)
        most = max(cells, key=lambda one: one.value)
        total = sum(one.value for one in cells)
        return (
            f"{least.displayed} to {most.displayed}, adding up to {rounded(total)}"
        )

    if all(one.is_date for one in cells):
        earliest = min(cells, key=lambda one: one.value)
        latest = max(cells, key=lambda one: one.value)
        return f"{earliest.displayed} to {latest.displayed}"

    seen: dict[str, int] = {}
    for one in cells:
        shown = str(one.displayed).strip()
        seen[shown] = seen.get(shown, 0) + 1

    common = sorted(seen.items(), key=lambda pair: (-pair[1], pair[0]))
    if common[0][1] == 1:
        return "every value different"

    named = ", ".join(
        f'"{value}" {count} times' for value, count in common[:COMMON_LIMIT] if count > 1
    )
    return f"most often {named}"


@tool
def sheet_stats(
    column: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Summarise one column: how many values, and the totals worth knowing.

    Use this rather than reading rows and working it out yourself. It reads
    the whole column however long it is, where inspect_sheet returns only as
    much as it is allowed to.

    Args:
        column: The column to summarise, by the name in its header.
        spreadsheet: Which spreadsheet to read, by name. Leave this out to
            read the one being worked on.
        sheet: Which sheet to read, by name. Leave this out to read the first
            sheet in the spreadsheet.

    Returns:
        A sentence of figures, or an explanation of why none were worked out.

    Examples:
        sheet_stats(column="Units")
        sheet_stats(column="Region", spreadsheet="Sales Orders")
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
            f"Sheet: {where}. No column names were found, so there is no "
            "column to summarise."
        )

    if column not in headers:
        return (
            f'There is no column called "{column}". '
            f"The sheet has: {', '.join(headers)}."
        )

    last_row = last_data_row(rows, header_row)
    if last_row <= header_row:
        return f"Sheet: {where}. It has column names but no rows of data yet."

    number = headers[column]
    values = [cell(rows, row, number) for row in range(header_row + 1, last_row + 1)]
    filled = [one for one in values if not is_blank(one.displayed)]

    different = len({str(one.displayed).strip() for one in filled})
    calculated = sum(1 for one in filled if one.formula)

    lines = [
        f'"{column}" in {where}: {len(filled)} filled, '
        f"{len(values) - len(filled)} blank, {different} different.",
        describe(filled) + ".",
    ]
    if calculated:
        # Worth saying, because a column the sheet works out can be summarised
        # here but must not be written to.
        lines.append(
            f"{calculated} of them are worked out by a formula in the sheet."
        )

    return " ".join(lines)


CASES = [
    ("a column of numbers", {"column": "Units"}),
    ("a column of money", {"column": "Revenue"}),
    ("a column of dates", {"column": "Order Date"}),
    ("a column of words", {"column": "Region"}),
    ("a column that does not exist", {"column": "Nonsense"}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.stats`.

    Reading only, so running this never changes anything. It works on the
    spreadsheet named in EXCEL_AGENT_SPREADSHEET.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(sheet_stats.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
