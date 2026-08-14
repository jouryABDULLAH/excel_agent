"""Tool for finding a row without knowing its number.

Answers where something is inside one sheet. Which spreadsheet holds it is a
different question, asked of Drive by find_spreadsheet, and kept apart because
this one gives back row numbers: a row number is only good in the sheet it
came from, so whoever will act on it should be the one who asked.
"""

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
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

# Enough to show what matched without turning an answer into a whole sheet.
MATCH_LIMIT = 30


def matches(text: str, wanted: str, whole: bool) -> bool:
    """Whether one cell's text counts as a match.

    Matched without regard to case, because a person looking for "north"
    means the column that says "North".
    """
    if is_blank(text):
        return False

    text = str(text).strip().lower()
    wanted = wanted.strip().lower()

    return text == wanted if whole else wanted in text


@tool
def find_data(
    text: str,
    column: str | None = None,
    whole_cell: bool = False,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Find where something appears in a spreadsheet, by its value rather than its row number.

    Use this instead of reading the sheet and looking through it yourself. It
    gives back real row numbers, which is what a change has to be pointed at.

    Args:
        text: What to look for.
        column: Look only in this column, by name. Leave it out to look in
            every column.
        whole_cell: True to match only a cell holding exactly this and nothing
            else. False, the default, matches a cell containing it.
        spreadsheet: Which spreadsheet to look in, by name. Leave this out to
            look in the one being worked on.
        sheet: Which sheet to look in, by name. Leave this out to look in the
            first sheet.

    Returns:
        The rows that matched, with their row numbers, or a sentence saying
        nothing matched. Use find_spreadsheet instead when the question is
        which file holds something.

    Examples:
        find_data(text="ORD-1042")
        find_data(text="North", column="Region")
    """
    if is_blank(text):
        return "Say what to look for."

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
            f"Sheet: {where}. No column names were found, so there is nothing "
            "to search by column."
        )

    looking_in = list(headers)
    if column:
        if column not in headers:
            return (
                f'There is no column called "{column}". '
                f"The sheet has: {', '.join(looking_in)}."
            )
        looking_in = [column]

    last_row = last_data_row(rows, header_row)
    hits = []
    for row in range(header_row + 1, last_row + 1):
        for name in looking_in:
            if matches(cell(rows, row, headers[name]).displayed, text, whole_cell): # type: ignore
                hits.append((row, name))
                break

    if not hits:
        searched = f'"{column}"' if column else "any column"
        return f'Nothing in {searched} holds "{text}", in {where}.'

    shown = hits[:MATCH_LIMIT]
    names = list(headers)
    lines = [
        f'{len(hits)} row(s) in {where} hold "{text}"'
        + (f" in {column}" if column else "")
        + ".",
        "",
        "| row | matched in | " + " | ".join(names) + " |",
        "|" + "---|" * (len(names) + 2),
    ]

    for row, matched_in in shown:
        values = [
            str(cell(rows, row, headers[name]).displayed or "") for name in names
        ]
        lines.append(f"| {row} | {matched_in} | " + " | ".join(values) + " |")

    if len(hits) > len(shown):
        lines.append("")
        lines.append(
            f"{len(hits) - len(shown)} more row(s) matched and are not shown. "
            "Narrow the search with a column, or with more of the text."
        )

    return "\n".join(lines)


CASES = [
    ("a value anywhere", {"text": "North"}),
    ("a value in one column", {"text": "North", "column": "Region"}),
    ("a whole cell only", {"text": "North", "whole_cell": True}),
    ("something that is not there", {"text": "no such value anywhere"}),
    ("a column that does not exist", {"text": "North", "column": "Nonsense"}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.sheets.find`."""
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(find_data.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
