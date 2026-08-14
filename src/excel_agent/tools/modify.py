"""Tool for changing rows.

Rows are added, edited, removed and moved through the Sheets API, which
rewrites every formula that referred to them, so nothing here has to protect a
calculated cell.
"""

from typing import Literal

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    a1,
    batch,
    find_header_row,
    grid,
    header_map,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
    to_dimension_range,
    write_values,
)


def describe(values: dict) -> str:
    """Render the changed columns for the confirmation message."""
    return ", ".join(
        f"{name} = {'(blank)' if value is None else value}"
        for name, value in values.items()
    )


def cells_for(title: str, row: int, values: dict, headers: dict[str, int]) -> list[dict]:
    """One range per column being written, for values.batchUpdate.

    Written a cell at a time rather than as one run, because the columns named
    may sit anywhere across the row and everything between them must be left
    exactly as it is.

    Nothing becomes an empty string, which is how a cell is cleared: there is
    no way to send "no value" that Google reads as "empty this".
    """
    return [
        {
            "range": a1(title, row, row, headers[name], headers[name]),
            "values": [["" if value is None else value]],
        }
        for name, value in values.items()
    ]


@tool
def modify_row(
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
        changed. Nothing is written when the answer is an explanation, so you
        are free to correct the arguments and try again.

    Examples:
        modify_row(action="edit", row=5, values={"Units": 20})
        modify_row(action="edit", row=5, values={"Notes": null})
        modify_row(action="add", values={"Product": "Webcam", "Region": "EU"})
        modify_row(action="add", values={"Total": "=D7*E7"})
        modify_row(action="remove", row=5)
        modify_row(action="move", row=8, to_row=2)
    """
    try:
        spreadsheet_id, name = resolve_spreadsheet(spreadsheet)
        properties = resolve_sheet(spreadsheet_id, sheet)
        rows = grid(spreadsheet_id, properties["title"])
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    title = properties["title"]
    where = f"({title} in {name})"

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    if not headers:
        return (
            f"No column names were found. Row {header_row} is empty, and no "
            f"row near the top of the sheet looks like a header. {where}"
        )

    last_row = last_data_row(rows, header_row)

    # Everything is checked before anything is written, so a rejected call
    # leaves the sheet exactly as it was.
    if action in ("edit", "remove", "move"):
        if row is None:
            return (
                f"The {action} action needs a row number. "
                "Call inspect_sheet to find the right one."
            )
        if row <= header_row or row > last_row:
            return (
                f"Row {row} does not exist. The sheet has rows "
                f"{header_row + 1} to {last_row}. {where}"
            )

    if action in ("add", "edit"):
        if not values:
            return f"The {action} action needs at least one column in values."
        unknown = [column for column in values if column not in headers]
        if unknown:
            return (
                f"Unknown column(s): {', '.join(unknown)}. "
                f"The sheet has: {', '.join(headers)}."
            )

    if action == "move":
        if to_row is None:
            return "The move action needs to_row, the row it should end up at."
        if to_row <= header_row or to_row > last_row:
            return (
                f"Row {to_row} is not somewhere a row can go. The sheet has "
                f"rows {header_row + 1} to {last_row}. {where}"
            )
        if to_row == row:
            return f"Row {row} is already where it should be. Nothing changed."

    try:
        if action == "add":
            assert values is not None
            new_row = last_row + 1
            write_values(spreadsheet_id, cells_for(title, new_row, values, headers))
            return (
                f"Added row {new_row} with {describe(values)}. Any other "
                f"column was left blank. {where}"
            )

        if action == "edit":
            assert values is not None and row is not None
            write_values(spreadsheet_id, cells_for(title, row, values, headers))
            return f"Updated row {row}: {describe(values)}. {where}"

        if action == "remove":
            assert row is not None
            batch(
                spreadsheet_id,
                [
                    {
                        "deleteDimension": {
                            "range": to_dimension_range(
                                properties["sheetId"], "ROWS", row, row
                            )
                        }
                    }
                ],
            )
            return (
                f"Removed row {row}. The rows below it have moved up by one, "
                "so any row numbers you read earlier are now out of date. Call "
                f"inspect_sheet again before changing anything else. {where}"
            )

        if action == "move":
            assert row is not None and to_row is not None
            # Google counts the destination in the rows as they are now,
            # before the row being moved is lifted out. Moving down, that
            # means the number the row should end up at; moving up, the one
            # before it.
            destination = to_row if to_row > row else to_row - 1
            batch(
                spreadsheet_id,
                [
                    {
                        "moveDimension": {
                            "source": to_dimension_range(
                                properties["sheetId"], "ROWS", row, row
                            ),
                            "destinationIndex": destination,
                        }
                    }
                ],
            )
            return (
                f"Moved row {row} to row {to_row}. Everything between them has "
                "shifted by one, so any row numbers you read earlier are now "
                f"out of date. {where}"
            )
    except HttpError as failure:
        return readable(failure)

    return f'Unknown action "{action}". Use add, edit, remove or move.'


CASES = [
    ("a row that does not exist", {"action": "edit", "row": 9999, "values": {"Region": "EU"}}),
    ("a column that does not exist", {"action": "add", "values": {"Nonsense": 1}}),
    ("edit with nothing to change", {"action": "edit", "row": 2, "values": {}}),
    ("move with nowhere to go", {"action": "move", "row": 2}),
]


def main() -> None:
    """Try the refusals by hand with `python -m excel_agent.tools.modify`.

    Only the calls that change nothing are here. Anything that writes belongs
    in a scratch spreadsheet, run by hand, so a demo file is not quietly
    reshaped by a smoke test.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(modify_row.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
