"""Tools for changing spreadsheet rows."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
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


def _cell_updates(
    sheet_name: str,
    row: int,
    values: dict[str, CellValue],
    headers: dict[str, int],
) -> list[dict]:
    """Build one ValueRange per changed column."""
    return [
        {
            "range": a1(
                sheet_name,
                first_row=row,
                last_row=row,
                first_column=headers[column],
                last_column=headers[column],
            ),
            "values": [["" if value is None else value]],
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
def update_row(
    row: int,
    values: dict[str, CellValue],
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Update selected cells in an existing row.

    Only the columns supplied in values are changed. A null value clears the
    cell. A string beginning with "=" is interpreted by Google Sheets as a
    formula.

    Call inspect_sheet or find_data first when the row number is not already
    known.

    Args:
        row: Existing row number, using the numbers shown in Google Sheets.
        values: Column names mapped to their new values.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
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

        if row <= header_row or row > last_row:
            return _error(
                "row_not_found",
                "The requested row is not an existing data row.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                row=row,
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
            updates=_cell_updates(
                sheet_name,
                row,
                values,
                headers,
            ),
            value_input_option="USER_ENTERED",
        )

        return {
            "ok": True,
            "operation": "update_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "row": row,
            "updated_columns": list(values),
            "updated_cells": response.get(
                "totalUpdatedCells",
                len(values),
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
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Append a new data row after the current table.

    Use this when the requested row belongs at the end. Use insert_row when it
    must be placed at a specific position.

    Args:
        values: Column names mapped to values for the new row.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
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

        first_column = min(headers.values())
        last_column = max(headers.values())

        row_values = [
            next(
                (
                    "" if values[column] is None else values[column]
                    for column, number in headers.items()
                    if number == column_number
                    and column in values
                ),
                "",
            )
            for column_number in range(
                first_column,
                last_column + 1,
            )
        ]

        table_range = a1(
            sheet_name,
            first_row=header_row,
            first_column=first_column,
            last_column=last_column,
        )

        response = spreadsheet_service.append_rows(
            spreadsheet_id=spreadsheet_id,
            range_name=table_range,
            values=[row_values],
            value_input_option="USER_ENTERED",
        )

        updates = response.get("updates", {})

        return {
            "ok": True,
            "operation": "append_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "row": last_row + 1,
            "values": values,
            "updated_range": updates.get("updatedRange"),
            "updated_cells": updates.get(
                "updatedCells",
                len(values),
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
    row: int,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Delete one existing data row and everything in it.

    Row numbers below the deleted row change after this operation.

    Args:
        row: Existing data row to delete.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
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
                "The requested row is not an existing data row.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                row=row,
                first_data_row=header_row + 1,
                last_data_row=last_row,
            )

        spreadsheet_service.delete_rows(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_row=row,
        )

        return {
            "ok": True,
            "operation": "delete_row",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "deleted_row": row,
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