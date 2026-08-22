"""Tools for changing spreadsheet rows."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools.runtime import chosen
from excel_agent.sheets import (
    a1,
    find_header_row,
    header_map,
    last_data_row,
    resolve_spreadsheet,
)


CellValue = str | int | float | bool | None


def _error(
    code: str,
    message: str,
    *,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    **details: Any,
) -> dict:
    """Return a consistent structured tool failure."""
    return {
        "ok": False,
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }


def _one_or_many(
    row: int | None,
    rows: list[int] | None,
) -> tuple[list[int], dict | None]:
    """The rows a call names, whichever argument carried them.

    Sorted and deduplicated, because "rows 3, 3 and 5" names two rows, and
    the two arguments together must name something.
    """
    if row is not None and rows:
        return [], _error(
            "conflicting_rows",
            "Give either row or rows, not both.",
        )

    named = sorted({*(rows or []), *([row] if row is not None else [])})

    if not named:
        return [], _error(
            "missing_row",
            "Give a row, or several in rows.",
        )

    return named, None


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    """Sorted rows as inclusive contiguous ranges: [3,4,5,9] -> (3,5),(9,9)."""
    ranges: list[tuple[int, int]] = []

    for one in rows:
        if ranges and one == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], one)
        else:
            ranges.append((one, one))

    return ranges


def _cell_updates(
    sheet_name: str,
    row: int,
    values: dict[str, CellValue],
    headers: dict[str, int],
    count: int = 1,
) -> list[dict]:
    """Build one ValueRange per changed column, count rows tall."""
    return [
        {
            "range": a1(
                sheet_name,
                first_row=row,
                last_row=row + count - 1,
                first_column=headers[column],
                last_column=headers[column],
            ),
            "values": [["" if value is None else value]] * count,
        }
        for column, value in values.items()
    ]


def _load_table(
    spreadsheet: str | None,
    sheet: str | None,
) -> tuple[str, str, dict, list, int, dict[str, int], int]:
    """Resolve and inspect the table needed by row tools."""
    spreadsheet_id, spreadsheet_name = resolve_spreadsheet(spreadsheet)

    properties = spreadsheet_service.resolve_sheet(
        spreadsheet_id,
        sheet,
    )

    sheet_name = properties["title"]

    rows = spreadsheet_service.read_sheet(
        spreadsheet_id,
        sheet_name,
    )

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    last_row = last_data_row(rows, header_row)

    return (
        spreadsheet_id,
        spreadsheet_name,
        properties,
        rows,
        header_row,
        headers,
        last_row,
    )


def _validate_values(
    values: dict[str, CellValue],
    headers: dict[str, int],
) -> dict | None:
    """Validate column names before writing."""
    if not values:
        return _error(
            "no_values",
            "No values were supplied.",
        )

    unknown = [
        column
        for column in values
        if column not in headers
    ]

    if unknown:
        return _error(
            "unknown_columns",
            "One or more column names do not exist.",
            unknown_columns=unknown,
            available_columns=list(headers),
        )

    return None


@tool
def fill_rows(
    start_row: int,
    rows: list[dict[str, CellValue]],
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Write a block of consecutive rows, each with its own values, at once.

    One call fills the whole block: rows[0] lands in start_row, rows[1] in
    the row below it, and so on. Never write a block one update_row call per
    row - twenty rows is one fill_rows call with twenty dicts.

    Use update_row instead when several rows get the SAME values; use
    append_row to add records after the end of the data.

    Only the columns named in each dict are changed. A null clears the cell;
    a string beginning with "=" is a formula. Rows past the end of the data
    are written where asked, growing the sheet if needed.

    Args:
        start_row: The sheet row the first dict lands in.
        rows: One dict of column-name -> value per consecutive row.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    if not rows:
        return _error(
            "no_values",
            "No rows were supplied.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            headers,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        if not headers:
            return _error(
                "headers_not_found",
                "No column headers were found.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                header_row=header_row,
            )

        if start_row <= header_row:
            return _error(
                "row_not_found",
                "The block would overwrite the header row.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                first_data_row=header_row + 1,
            )

        # Every column across every row, checked before anything is written:
        # the block lands whole or not at all.
        together: dict[str, CellValue] = {}
        for one in rows:
            together.update(one)

        invalid = _validate_values(together, headers)
        if invalid:
            invalid["spreadsheet"] = spreadsheet_name
            invalid["sheet"] = sheet_name
            return invalid

        last_new_row = start_row + len(rows) - 1

        grid_rows = (
            properties
            .get("gridProperties", {})
            .get("rowCount")
        )

        if grid_rows is not None and last_new_row > grid_rows:
            spreadsheet_service.insert_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_id=properties["sheetId"],
                start_row=grid_rows + 1,
                count=last_new_row - grid_rows,
            )

        response = spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                update
                for offset, values in enumerate(rows)
                if values
                for update in _cell_updates(
                    sheet_name,
                    start_row + offset,
                    values,
                    headers,
                )
            ],
            value_input_option="USER_ENTERED",
        )

        past_data = max(0, last_new_row - max(last_row, header_row))

        return {
            "ok": True,
            "operation": "fill_rows",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "first_row": start_row,
            "last_row": last_new_row,
            "rows_written": len(rows),
            # Said out loud so a block that landed past the data is visible,
            # rather than a silent surprise off the bottom of the table.
            "rows_past_data": past_data,
            "updated_cells": response.get("totalUpdatedCells", 0),
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )


@tool
def update_row(
    values: dict[str, CellValue],
    row: int | None = None,
    rows: list[int] | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Update selected cells in existing rows, all with the same values.

    One row goes in row; several rows getting the SAME values go in rows,
    in one call - never one call per row. All named rows change together or
    not at all: one row that does not exist refuses the whole call before
    anything is written. For consecutive rows that each get DIFFERENT
    values, use fill_rows.

    Only the columns supplied in values are changed. A null value clears the
    cell. A string beginning with "=" is interpreted by Google Sheets as a
    formula.

    Call inspect_sheet or find_data first when the row number is not already
    known.

    Args:
        values: Column names mapped to their new values.
        row: One existing row, using the numbers shown in Google Sheets.
        rows: Several existing rows to change together.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    asked, wrong = _one_or_many(row, rows)

    if wrong:
        wrong["spreadsheet"] = spreadsheet
        wrong["sheet"] = sheet
        return wrong

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            headers,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        if not headers:
            return _error(
                "headers_not_found",
                "No column headers were found.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                header_row=header_row,
            )

        missing = [
            one for one in asked
            if one <= header_row or one > last_row
        ]

        if missing:
            return _error(
                "row_not_found",
                "One or more requested rows are not existing data rows. "
                "Nothing was changed.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                rows_not_found=missing,
                first_data_row=header_row + 1,
                last_data_row=last_row,
            )

        invalid = _validate_values(values, headers)
        if invalid:
            invalid["spreadsheet"] = spreadsheet_name
            invalid["sheet"] = sheet_name
            return invalid

        response = spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                update
                for one in asked
                for update in _cell_updates(
                    sheet_name,
                    one,
                    values,
                    headers,
                )
            ],
            value_input_option="USER_ENTERED",
        )

        return {
            "ok": True,
            "operation": "update_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "rows": asked,
            "updated_columns": list(values),
            "updated_cells": response.get(
                "totalUpdatedCells",
                len(values) * len(asked),
            ),
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )


@tool
def insert_row(
    row: int,
    values: dict[str, CellValue] | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Insert a new row at a specific position.

    The existing row currently at this number, and all rows below it, shift
    down by one. Values are optional; omit them to insert an empty row.

    Args:
        row: Position of the new row.
        values: Optional column names mapped to values for the new row.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            headers,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        if not headers:
            return _error(
                "headers_not_found",
                "No column headers were found.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                header_row=header_row,
            )

        # Existing data may be inserted into, or one row may be added
        # immediately after the current final data row.
        if row <= header_row or row > last_row + 1:
            return _error(
                "invalid_insert_position",
                "The requested row is not a valid insertion position.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                row=row,
                first_position=header_row + 1,
                last_position=last_row + 1,
            )

        if values:
            invalid = _validate_values(values, headers)
            if invalid:
                invalid["spreadsheet"] = spreadsheet_name
                invalid["sheet"] = sheet_name
                return invalid

        spreadsheet_service.insert_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_row=row,
            count=1,
        )

        updated_cells = 0

        if values:
            response = spreadsheet_service.update_cells(
                spreadsheet_id=spreadsheet_id,
                updates=_cell_updates(
                    sheet_name,
                    row,
                    values,
                    headers,
                ),
                value_input_option="USER_ENTERED",
            )

            updated_cells = response.get(
                "totalUpdatedCells",
                len(values),
            )

        return {
            "ok": True,
            "operation": "insert_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "row": row,
            "values": values or {},
            "updated_cells": updated_cells,
            "row_numbers_changed": True,
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )


@tool
def append_row(
    values: dict[str, CellValue],
    count: int = 1,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Append one or more copies of a new data row after the current table.

    Use this when the requested row belongs at the end. Use insert_row when it
    must be placed at a specific position. To repeat a row, make ONE call with
    count set - never call this once per copy.

    Args:
        values: Column names mapped to values for the new row.
        count: How many copies of the row to append. All of them are written
            in one batch.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            headers,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        if not headers:
            return _error(
                "headers_not_found",
                "No column headers were found.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                header_row=header_row,
            )

        invalid = _validate_values(values, headers)
        if invalid:
            invalid["spreadsheet"] = spreadsheet_name
            invalid["sheet"] = sheet_name
            return invalid

        if count < 1:
            return _error(
                "invalid_count",
                "count must be at least 1.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                count=count,
            )

        # The row after the last one holding data, worked out here rather than
        # left to Google.
        #
        # This used to hand values.append a range spanning every named column,
        # and Google decided for itself where the table inside it ended. A
        # sheet with a second small table off to the right shares the header
        # row, so that range covered both, and the guess did not match the
        # table meant: rows landed below the wrong block, or a single value
        # landed away from its column.
        target_row = last_row + 1

        grid_rows = (
            properties
            .get("gridProperties", {})
            .get("rowCount")
        )

        last_new_row = target_row + count - 1

        # values.append grew the sheet when it ran out of rows, so a grid
        # already full to its last row needs the room made for it.
        if (
            grid_rows is not None
            and last_new_row > grid_rows
        ):
            spreadsheet_service.insert_rows(
                spreadsheet_id=spreadsheet_id,
                sheet_id=properties["sheetId"],
                start_row=grid_rows + 1,
                count=last_new_row - grid_rows,
            )

        # One range per named column, count rows tall, so a column with no
        # value given is left alone rather than written blank, and nothing is
        # written into the gaps between one table and the next.
        response = spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=_cell_updates(
                sheet_name,
                target_row,
                values,
                headers,
                count,
            ),
            value_input_option="USER_ENTERED",
        )

        return {
            "ok": True,
            "operation": "append_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "row": target_row,
            "last_row_written": last_new_row,
            "count": count,
            "values": values,
            "updated_columns": list(values),
            "updated_cells": response.get(
                "totalUpdatedCells",
                len(values) * count,
            ),
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )


@tool
def delete_row(
    row: int | None = None,
    rows: list[int] | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Delete existing data rows and everything in them.

    One row goes in row; several go in rows, in one call - never one call
    per row. All named rows are deleted together or not at all: one row that
    does not exist refuses the whole call before anything is changed.

    Row numbers below a deleted row change after this operation.

    Args:
        row: One existing data row to delete.
        rows: Several existing data rows to delete together.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    asked, wrong = _one_or_many(row, rows)

    if wrong:
        wrong["spreadsheet"] = spreadsheet
        wrong["sheet"] = sheet
        return wrong

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            _,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        missing = [
            one for one in asked
            if one <= header_row or one > last_row
        ]

        if missing:
            return _error(
                "row_not_found",
                "One or more requested rows are not existing data rows. "
                "Nothing was deleted.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                rows_not_found=missing,
                first_data_row=header_row + 1,
                last_data_row=last_row,
            )

        spreadsheet_service.delete_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            ranges=_runs(asked),
        )

        return {
            "ok": True,
            "operation": "delete_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "deleted_rows": asked,
            "deleted_count": len(asked),
            "row_numbers_changed": True,
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )


@tool
def move_row(
    row: int,
    to_row: int,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Move one existing data row to another position.

    Args:
        row: Current row number.
        to_row: Final row number after the move.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            _,
            last_row,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]

        if row <= header_row or row > last_row:
            return _error(
                "row_not_found",
                "The source row is not an existing data row.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                row=row,
                first_data_row=header_row + 1,
                last_data_row=last_row,
            )

        if to_row <= header_row or to_row > last_row:
            return _error(
                "invalid_destination",
                "The destination is outside the data rows.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                to_row=to_row,
                first_data_row=header_row + 1,
                last_data_row=last_row,
            )

        if row == to_row:
            return {
                "ok": True,
                "operation": "move_row",
                "spreadsheet": spreadsheet_name,
                "sheet": sheet_name,
                "row": row,
                "to_row": to_row,
                "changed": False,
            }

        spreadsheet_service.move_row(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            row=row,
            to_row=to_row,
        )

        return {
            "ok": True,
            "operation": "move_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "from_row": row,
            "to_row": to_row,
            "changed": True,
            "row_numbers_changed": True,
        }

    except ValueError as failure:
        return _error(
            "invalid_request",
            str(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    except HttpError as failure:
        return _error(
            "google_api_error",
            readable(failure),
            spreadsheet=spreadsheet,
            sheet=sheet,
        )