"""Tools for changing spreadsheet columns."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.sheets import (
    a1,
    column_letter,
    find_header_row,
    header_map,
    last_data_row,
    resolve_spreadsheet,
    to_grid_range,
)


def _error(
    code: str,
    message: str,
    *,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    **details: Any,
) -> dict:
    """Return one consistent structured tool failure."""
    return {
        "ok": False,
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }


def _load_table(
    spreadsheet: str | None,
    sheet: str | None,
) -> tuple[
    str,
    str,
    dict,
    int,
    dict[str, int],
    int,
]:
    """Resolve the sheet and inspect its table structure."""
    spreadsheet_id, spreadsheet_name = resolve_spreadsheet(
        spreadsheet
    )

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
        header_row,
        headers,
        last_row,
    )


def _require_headers(
    headers: dict[str, int],
    *,
    spreadsheet: str,
    sheet: str,
    header_row: int,
) -> dict | None:
    """Return an error when the sheet has no usable headers."""
    if headers:
        return None

    return _error(
        "headers_not_found",
        "No column headers were found.",
        spreadsheet=spreadsheet,
        sheet=sheet,
        header_row=header_row,
    )


def _require_column(
    column: str,
    headers: dict[str, int],
    *,
    spreadsheet: str,
    sheet: str,
) -> dict | None:
    """Return an error when a named column does not exist."""
    if column in headers:
        return None

    return _error(
        "column_not_found",
        "The requested column does not exist.",
        spreadsheet=spreadsheet,
        sheet=sheet,
        column=column,
        available_columns=list(headers),
    )


@tool
def insert_column(
    name: str,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Insert a new empty column.

    By default the column is added immediately after the last named column.
    On an empty sheet with no headers, this creates the first column at
    position 1.

    Give position to insert it somewhere specific, counting from 1 at the
    left side of the table.

    Args:
        name: Header for the new column.
        position: Optional final position of the new column.
        spreadsheet: Spreadsheet name. Omit to use the current spreadsheet.
        sheet: Sheet name. Omit to use the first sheet.
    """
    if not name or not name.strip():
        return _error(
            "missing_name",
            "The new column needs a name.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    name = name.strip()

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            header_row,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        if name in headers:
            return _error(
                "duplicate_column",
                "A column with that name already exists.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                column=name,
                available_columns=list(headers),
            )

        # If the sheet has no headers yet, there is no rightmost named
        # column. Treat that as position 0 so the first valid column is 1.
        rightmost = (
            max(headers.values())
            if headers
            else 0
        )

        if position is None:
            position = rightmost + 1

        if (
            position < 1
            or position > rightmost + 1
        ):
            return _error(
                "invalid_position",
                (
                    "The requested column position is outside the table."
                    if headers
                    else (
                        "The first column of an empty sheet "
                        "must be at position 1."
                    )
                ),
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                position=position,
                first_position=1,
                last_position=rightmost + 1,
            )

        spreadsheet_service.insert_columns(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_column=position,
            count=1,
        )

        # Headers are identifiers, not user-entered spreadsheet values.
        # RAW preserves names such as 007, 1-2 and +Notes exactly as written.
        spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                {
                    "range": a1(
                        sheet_name,
                        first_row=header_row,
                        last_row=header_row,
                        first_column=position,
                        last_column=position,
                    ),
                    "values": [[name]],
                }
            ],
            value_input_option="RAW",
        )

        return {
            "ok": True,
            "operation": "insert_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": name,
            "position": position,
            "column_letter": column_letter(position),
            "column_positions_changed": (
                bool(headers)
                and position <= rightmost
            ),
            "created_first_column": not bool(headers),
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
def rename_column(
    column: str,
    new_name: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Rename one existing column without moving its data.

    Args:
        column: Current header name.
        new_name: New header name.
        spreadsheet: Spreadsheet name. Omit to use the current spreadsheet.
        sheet: Sheet name. Omit to use the first sheet.
    """
    if not column or not column.strip():
        return _error(
            "missing_column",
            "The column to rename must be named.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if not new_name or not new_name.strip():
        return _error(
            "missing_new_name",
            "The column needs a new name.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    column = column.strip()
    new_name = new_name.strip()

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            header_row,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        if name in headers:
            return _error(
                "duplicate_column",
                "A column with that name already exists.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                column=name,
                available_columns=list(headers),
            )

        # An empty sheet has no rightmost named column yet.
        # Treat its right edge as position 0, making position 1 the only
        # valid place for the first header.
        rightmost = (
            max(headers.values())
            if headers
            else 0
        )

        if position is None:
            position = rightmost + 1

        if (
            position < 1
            or position > rightmost + 1
        ):
            return _error(
                "invalid_position",
                (
                    "The requested column position is outside the table."
                    if headers
                    else "The first column of an empty sheet must be at position 1."
                ),
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                position=position,
                first_position=1,
                last_position=rightmost + 1,
            )

        spreadsheet_service.insert_columns(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_column=position,
            count=1,
        )

        spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                {
                    "range": a1(
                        sheet_name,
                        first_row=header_row,
                        last_row=header_row,
                        first_column=position,
                        last_column=position,
                    ),
                    "values": [[name]],
                }
            ],
            value_input_option="RAW",
        )

        return {
            "ok": True,
            "operation": "insert_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": name,
            "position": position,
            "column_letter": column_letter(position),
            "column_positions_changed": (
                bool(headers)
                and position <= rightmost
            ),
            "created_first_column": not bool(headers),
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
def delete_column(
    column: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Delete one existing column and every value in it.

    Args:
        column: Header name of the column to delete.
        spreadsheet: Spreadsheet name. Omit to use the current spreadsheet.
        sheet: Sheet name. Omit to use the first sheet.
    """
    if not column or not column.strip():
        return _error(
            "missing_column",
            "The column to delete must be named.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    column = column.strip()

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        missing = _require_column(
            column,
            headers,
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )
        if missing:
            return missing

        position = headers[column]

        spreadsheet_service.delete_columns(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_column=position,
        )

        return {
            "ok": True,
            "operation": "delete_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "deleted_column": column,
            "deleted_position": position,
            "column_positions_changed": True,
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
def move_column(
    column: str,
    to_position: int,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Move an existing column to another table position.

    Args:
        column: Header name of the column to move.
        to_position: Final position, counting from 1 at the left.
        spreadsheet: Spreadsheet name. Omit to use the current spreadsheet.
        sheet: Sheet name. Omit to use the first sheet.
    """
    if not column or not column.strip():
        return _error(
            "missing_column",
            "The column to move must be named.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    column = column.strip()

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        missing = _require_column(
            column,
            headers,
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )
        if missing:
            return missing

        from_position = headers[column]
        rightmost = max(headers.values())

        if to_position < 1 or to_position > rightmost:
            return _error(
                "invalid_position",
                "The destination position is outside the table.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                to_position=to_position,
                first_position=1,
                last_position=rightmost,
            )

        if to_position == from_position:
            return {
                "ok": True,
                "operation": "move_column",
                "spreadsheet": spreadsheet_name,
                "sheet": sheet_name,
                "column": column,
                "from_position": from_position,
                "to_position": to_position,
                "changed": False,
            }

        spreadsheet_service.move_column(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            column=from_position,
            to_position=to_position,
        )

        return {
            "ok": True,
            "operation": "move_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": column,
            "from_position": from_position,
            "to_position": to_position,
            "changed": True,
            "column_positions_changed": True,
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
def set_column_formula(
    column: str,
    formula: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Fill an existing column with a relative spreadsheet formula.

    The formula is written to the first data row and copied downward so
    relative references change naturally from row to row.

    Args:
        column: Header name of the destination column.
        formula: Formula as typed in Google Sheets, beginning with "=".
        spreadsheet: Spreadsheet name. Omit to use the current spreadsheet.
        sheet: Sheet name. Omit to use the first sheet.
    """
    if not column or not column.strip():
        return _error(
            "missing_column",
            "The formula destination column must be named.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if not formula or not formula.strip():
        return _error(
            "missing_formula",
            "A formula must be supplied.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    column = column.strip()
    formula = formula.strip()

    if not formula.startswith("="):
        return _error(
            "invalid_formula",
            'A formula must begin with "=".',
            spreadsheet=spreadsheet,
            sheet=sheet,
            formula=formula,
        )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            header_row,
            headers,
            last_row,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        missing = _require_column(
            column,
            headers,
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )
        if missing:
            return missing

        first_data_row = header_row + 1

        if last_row < first_data_row:
            return _error(
                "no_data_rows",
                "There are no data rows to fill.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                column=column,
            )

        position = headers[column]

        # Formula text should be interpreted by Sheets.
        spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                {
                    "range": a1(
                        sheet_name,
                        first_row=first_data_row,
                        last_row=first_data_row,
                        first_column=position,
                        last_column=position,
                    ),
                    "values": [[formula]],
                }
            ],
            value_input_option="USER_ENTERED",
        )

        if last_row > first_data_row:
            spreadsheet_service.copy_paste(
                spreadsheet_id=spreadsheet_id,
                source=to_grid_range(
                    properties["sheetId"],
                    first_data_row,
                    first_data_row,
                    position,
                    position,
                ),
                destination=to_grid_range(
                    properties["sheetId"],
                    first_data_row + 1,
                    last_row,
                    position,
                    position,
                ),
                paste_type="PASTE_FORMULA",
            )

        filled_rows = last_row - first_data_row + 1

        return {
            "ok": True,
            "operation": "set_column_formula",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": column,
            "formula": formula,
            "first_row": first_data_row,
            "last_row": last_row,
            "filled_rows": filled_rows,
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