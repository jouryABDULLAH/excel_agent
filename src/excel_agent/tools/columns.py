"""Tool for changing columns.

Columns are added, removed, moved and renamed through the Sheets API, so
formulas and charts that referred to them are rewritten to follow.
"""

from typing import Literal

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    a1,
    batch,
    column_letter,
    find_header_row,
    grid,
    header_map,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
    to_dimension_range,
    to_grid_range,
    write_values,
)


@tool
def modify_column(
    action: Literal["add", "remove", "move", "rename", "set_formula"],
    column: str,
    to_position: int | None = None,
    new_name: str | None = None,
    formula: str | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Add, remove, move or rename a column, or fill it with a formula.

    Call inspect_sheet first, so the column names you use are the real ones.

    Args:
        action: What to do. "set_formula" writes one formula and fills it
            down the whole column.
        column: The column to change, by the name in its header. For "add",
            the name the new column should have.
        to_position: Where the column should end up, counting from the left.
            Needed for move only.
        new_name: What the column should be called. Needed for rename only.
        formula: The formula to fill down, written as it would be typed, such
            as "=B2*C2". Needed for set_formula only.
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed. Nothing is written when the answer is an explanation, so you
        are free to correct the arguments and try again.

    Examples:
        modify_column(action="add", column="Profit")
        modify_column(action="rename", column="Units", new_name="Quantity")
        modify_column(action="move", column="Notes", to_position=2)
        modify_column(action="set_formula", column="Profit", formula="=I2-J2")
        modify_column(action="remove", column="Profit")
    """
    if not column or not column.strip():
        return f"The {action} action needs the name of a column."

    column = column.strip()

    try:
        spreadsheet_id, name = resolve_spreadsheet(spreadsheet)
        properties = resolve_sheet(spreadsheet_id, sheet)
        rows = grid(spreadsheet_id, properties["title"])
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    title = properties["title"]
    sheet_id = properties["sheetId"]
    where = f"({title} in {name})"

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    if not headers:
        return (
            f"No column names were found. Row {header_row} is empty, and no "
            f"row near the top of the sheet looks like a header. {where}"
        )

    # Everything is checked before anything is written, so a rejected call
    # leaves the sheet exactly as it was.
    if action == "add":
        if column in headers:
            return (
                f'There is already a column called "{column}". Two columns of '
                f"one name cannot be told apart. {where}"
            )
    elif column not in headers:
        return (
            f'There is no column called "{column}". '
            f"The sheet has: {', '.join(headers)}. {where}"
        )

    if action == "rename":
        if not new_name or not new_name.strip():
            return "The rename action needs a new_name to give the column."
        new_name = new_name.strip()
        if new_name in headers and new_name != column:
            return (
                f'There is already a column called "{new_name}". Two columns '
                f"of one name cannot be told apart. {where}"
            )

    if action == "move":
        if to_position is None:
            return (
                "The move action needs to_position, the place the column "
                "should end up, counting from the left."
            )
        if to_position < 1 or to_position > len(headers):
            return (
                f"Position {to_position} is not somewhere a column can go. The "
                f"sheet has {len(headers)} columns. {where}"
            )
        if to_position == headers[column]:
            return f'"{column}" is already there. Nothing changed. {where}'

    if action == "set_formula":
        if not formula or not formula.strip():
            return "The set_formula action needs a formula to fill down."
        formula = formula.strip()
        if not formula.startswith("="):
            return (
                f'A formula starts with "=". "{formula}" would be written as '
                "text. Use update_row to put a plain value in a cell."
            )

    last_row = last_data_row(rows, header_row)

    try:
        if action == "add":
            # Written into the first column past the last named one, which is
            # empty already, so nothing has to shift and no formula moves.
            position = max(headers.values()) + 1
            width = properties.get("gridProperties", {}).get("columnCount", 0)
            if position > width:
                batch(
                    spreadsheet_id,
                    [
                        {
                            "insertDimension": {
                                "range": to_dimension_range(
                                    sheet_id, "COLUMNS", position, position
                                )
                            }
                        }
                    ],
                )
            write_values(
                spreadsheet_id,
                [
                    {
                        "range": a1(title, header_row, header_row, position, position),
                        "values": [[column]],
                    }
                ],
            )
            return (
                f'Added a column called "{column}", at {column_letter(position)}. '
                f"It is empty: use update_row to put values into it, or "
                f"set_formula to work them out. {where}"
            )

        if action == "rename":
            assert new_name is not None
            write_values(
                spreadsheet_id,
                [
                    {
                        "range": a1(
                            title,
                            header_row,
                            header_row,
                            headers[column],
                            headers[column],
                        ),
                        "values": [[new_name]],
                    }
                ],
            )
            return (
                f'Renamed the column "{column}" to "{new_name}". Its data has '
                f"not moved. {where}"
            )

        if action == "remove":
            batch(
                spreadsheet_id,
                [
                    {
                        "deleteDimension": {
                            "range": to_dimension_range(
                                sheet_id, "COLUMNS", headers[column], headers[column]
                            )
                        }
                    }
                ],
            )
            return (
                f'Deleted the column "{column}" and everything in it. The '
                "columns to its right have moved one place left, and any "
                "formula that read it now shows #REF!, the way it would if the "
                f"column had been deleted by hand. {where}"
            )

        if action == "move":
            assert to_position is not None
            from_position = headers[column]
            # Google counts the destination in the columns as they are now,
            # before the one being moved is lifted out.
            destination = (
                to_position if to_position > from_position else to_position - 1
            )
            batch(
                spreadsheet_id,
                [
                    {
                        "moveDimension": {
                            "source": to_dimension_range(
                                sheet_id, "COLUMNS", from_position, from_position
                            ),
                            "destinationIndex": destination,
                        }
                    }
                ],
            )
            return (
                f'Moved "{column}" to position {to_position}, '
                f"{column_letter(to_position)}. The columns between have "
                f"shifted by one. {where}"
            )

        if action == "set_formula":
            assert formula is not None
            position = headers[column]
            first = header_row + 1
            if last_row < first:
                return (
                    f"There are no rows of data to fill, so the formula was "
                    f"not written. {where}"
                )

            write_values(
                spreadsheet_id,
                [
                    {
                        "range": a1(title, first, first, position, position),
                        "values": [[formula]],
                    }
                ],
            )

            filled = 1
            if last_row > first:
                # Copied rather than repeated, because copying is what shifts
                # =B2*C2 into =B3*C3 as it goes down. Writing the same text
                # into every row would leave all of them reading row 2.
                batch(
                    spreadsheet_id,
                    [
                        {
                            "copyPaste": {
                                "source": to_grid_range(
                                    sheet_id, first, first, position, position
                                ),
                                "destination": to_grid_range(
                                    sheet_id, first + 1, last_row, position, position
                                ),
                                "pasteType": "PASTE_FORMULA",
                            }
                        }
                    ],
                )
                filled = last_row - first + 1

            return (
                f'Filled "{column}" with {formula}, down {filled} row(s) from '
                f"row {first} to row {last_row}. Each row reads its own, so "
                f"row {first + 1} uses row {first + 1}. {where}"
            )
    except HttpError as failure:
        return readable(failure)

    return f'Unknown action "{action}". Use add, remove, move, rename or set_formula.'


CASES = [
    ("a column that does not exist", {"action": "rename", "column": "Nonsense", "new_name": "X"}),
    ("a name already taken", {"action": "add", "column": "Region"}),
    ("rename with no new name", {"action": "rename", "column": "Region"}),
    ("move with nowhere to go", {"action": "move", "column": "Region"}),
    ("a formula that is not one", {"action": "set_formula", "column": "Region", "formula": "B2*C2"}),
]


def main() -> None:
    """Try the refusals by hand with `python -m excel_agent.tools.columns`.

    Only the calls that change nothing are here. Anything that writes belongs
    in a scratch spreadsheet, run by hand.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(modify_column.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
