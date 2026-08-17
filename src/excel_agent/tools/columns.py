"""Tools for changing spreadsheet columns."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.sheets import (
    a1,
    cell,
    column_letter,
    find_header_row,
    header_map,
    last_data_row,
    resolve_spreadsheet,
    to_grid_range,
)
from excel_agent.tools.inspect import _as_text


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
    list,
    int,
    dict[str, int],
    int,
]:
    """Resolve the sheet and inspect its table structure.

    The rows are returned as well as the structure read out of them, because
    every tool that resolves a column by position needs the physical header
    row and would otherwise read the whole grid a second time.
    """
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
        rows,
        header_row,
        headers,
        last_row,
    )


def _named_headers(
    rows: list[list[Any]],
    header_row: int,
    width: int,
) -> list[tuple[int, str]]:
    """Every named column in the physical header row, with its position.

    header_map cannot be used here: it drops unnamed columns and keeps only
    one of any two columns sharing a name, which is exactly what the tools
    resolving a column by position have to be able to see.
    """
    found = []

    for position in range(1, width + 1):
        header = _as_text(
            cell(
                rows,
                header_row,
                position,
            )
        ).strip()

        if header:
            found.append((position, header))

    return found


def _resolve_column_target(
    *,
    column: str | None,
    position: int | None,
    rows: list[list[Any]],
    header_row: int,
    column_count: int | None,
    spreadsheet: str,
    sheet: str,
) -> tuple[int | None, str | None, dict | None]:
    """Resolve a physical column by name, position, or both.

    Position is authoritative when supplied. Both are bounded by the width of
    the sheet rather than the width of the table, so an unnamed column sitting
    beyond the last header can still be reached.

    Returns:
        (position, current_header, error)
    """
    # gridProperties carries the width; the data extent is only a fallback.
    width = (
        column_count
        if isinstance(column_count, int)
        else max(
            (len(row) for row in rows),
            default=0,
        )
    )

    if column is not None:
        column = column.strip() or None

    if column is None and position is None:
        return (
            None,
            None,
            _error(
                "missing_column",
                "Give either a column name or a column position.",
                spreadsheet=spreadsheet,
                sheet=sheet,
            ),
        )

    if position is not None:
        if position < 1 or position > width:
            return (
                None,
                None,
                _error(
                    "invalid_position",
                    "The requested column position is outside the sheet.",
                    spreadsheet=spreadsheet,
                    sheet=sheet,
                    position=position,
                    first_position=1,
                    last_position=width,
                ),
            )

        actual_header = _as_text(
            cell(
                rows,
                header_row,
                position,
            )
        ).strip()

        if (
            column is not None
            and actual_header != column
        ):
            # Where the named column really is, so the next call can be
            # right rather than being another guess.
            elsewhere = [
                found_position
                for found_position, found_header in _named_headers(
                    rows,
                    header_row,
                    width,
                )
                if found_header == column
            ]

            return (
                None,
                None,
                _error(
                    "column_position_mismatch",
                    (
                        f'Position {position} has header '
                        f'"{actual_header}" rather than "{column}".'
                    ),
                    spreadsheet=spreadsheet,
                    sheet=sheet,
                    column=column,
                    position=position,
                    requested_position=position,
                    column_position=(
                        elsewhere[0]
                        if len(elsewhere) == 1
                        else None
                    ),
                    actual_header=actual_header or None,
                ),
            )

        return (
            position,
            actual_header or None,
            None,
        )

    # Name-only lookup: inspect the physical header row so duplicate
    # names are detected instead of silently choosing one.
    assert column is not None

    # One walk of the header row gives both the columns that match and the
    # ones worth naming back when none do.
    named_positions = _named_headers(
        rows,
        header_row,
        width,
    )

    matching_positions = [
        candidate_position
        for candidate_position, candidate_header in named_positions
        if candidate_header == column
    ]

    if not matching_positions:
        return (
            None,
            None,
            _error(
                "column_not_found",
                "The requested column does not exist.",
                spreadsheet=spreadsheet,
                sheet=sheet,
                column=column,
                available_columns=[
                    candidate_header
                    for _, candidate_header in named_positions
                ],
            ),
        )

    if len(matching_positions) > 1:
        return (
            None,
            None,
            _error(
                "ambiguous_column",
                (
                    f'More than one column has the header "{column}". '
                    "Give its physical position."
                ),
                spreadsheet=spreadsheet,
                sheet=sheet,
                column=column,
                matching_positions=matching_positions,
            ),
        )

    return (
        matching_positions[0],
        column,
        None,
    )

@tool
def insert_column(
    name: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Insert a new column.

    The new column may be named or unnamed.

    By default the column is added immediately after the last named column.
    On an empty sheet with no headers, this creates the first column at
    position 1.

    Give position to insert it somewhere specific, counting from 1 at the
    left side of the table.

    Args:
        name: Optional header for the new column. Omit for an unnamed column.
        position: Optional final position of the new column.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    if name is not None:
        name = name.strip()

        if not name:
            name = None

    if position is not None and position < 1:
        return _error(
            "invalid_position",
            "Column position must be at least 1.",
            spreadsheet=spreadsheet,
            sheet=sheet,
            position=position,
        )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            _,
            header_row,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        # If the sheet has no named columns yet, position 1 is the
        # default location for the first table column.
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

        # Only write a header when the caller supplied one.
        if name is not None:
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
            "has_header": name is not None,
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
    new_name: str,
    column: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Set the header of one existing physical column.

    Identify the target by its current header name, physical position,
    or both. Position counts from 1 at the left edge of the sheet.

    Use position to name an unnamed column or when duplicate header names
    make a name ambiguous.

    An empty new_name clears the current header. Duplicate header names are
    allowed. If both column and position are supplied, they must identify the
    same physical column.

    Args:
        new_name: New header value. Use an empty string to clear the header.
        column: Optional current header name.
        position: Optional physical column position, starting from 1.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Empty string is intentionally allowed so a header can be cleared.
    new_name = new_name.strip()

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            rows,
            header_row,
            _,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        # header_map cannot represent unnamed columns or duplicate header
        # names, so the physical header cells read by _load_table are what
        # the target is resolved against.
        (
            target_position,
            old_name,
            error,
        ) = _resolve_column_target(
            column=column,
            position=position,
            rows=rows,
            header_row=header_row,
            column_count=(
                properties
                .get("gridProperties", {})
                .get("columnCount")
            ),
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )

        if error:
            return error

        assert target_position is not None

        # An unnamed column reads back as None, which clearing asks for too.
        if (old_name or "") == new_name:
            return {
                "ok": True,
                "operation": "rename_column",
                "spreadsheet": spreadsheet_name,
                "sheet": sheet_name,
                "old_name": old_name or None,
                "new_name": new_name or None,
                "position": target_position,
                "column_letter": column_letter(
                    target_position
                ),
                "changed": False,
            }

        # Renaming only changes the header cell.
        spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                {
                    "range": a1(
                        sheet_name,
                        first_row=header_row,
                        last_row=header_row,
                        first_column=target_position,
                        last_column=target_position,
                    ),
                    "values": [[new_name]],
                }
            ],
            value_input_option="RAW",
        )

        return {
            "ok": True,
            "operation": "rename_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "old_name": old_name or None,
            "new_name": new_name or None,
            "position": target_position,
            "column_letter": column_letter(
                target_position
            ),
            "changed": True,
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
    column: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Delete one existing physical column and every value in it.

    Identify the target by its header name, physical position, or both.
    Position counts from 1 at the left edge of the table.

    Use position for unnamed columns or when duplicate header names are
    ambiguous. If both column and position are supplied, they must identify
    the same physical column.

    Args:
        column: Optional current header name.
        position: Optional physical column position, starting from 1.
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
            rows,
            header_row,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        (
            target_position,
            target_header,
            error,
        ) = _resolve_column_target(
            column=column,
            position=position,
            rows=rows,
            header_row=header_row,
            column_count=(
                properties
                .get("gridProperties", {})
                .get("columnCount")
            ),
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )

        if error:
            return error

        assert target_position is not None

        spreadsheet_service.delete_columns(
            spreadsheet_id=spreadsheet_id,
            sheet_id=properties["sheetId"],
            start_column=target_position,
        )

        return {
            "ok": True,
            "operation": "delete_column",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "deleted_column": target_header,
            "deleted_position": target_position,
            "column_letter": column_letter(
                target_position
            ),
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
    to_position: int,
    column: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Move one existing physical column to another table position.

    Identify the source column by its header name, physical position, or both.
    Position and to_position count from 1 at the left edge of the table.

    Use position for unnamed columns or when duplicate header names are
    ambiguous. If both column and position are supplied, they must identify
    the same physical column.

    Args:
        to_position: Final physical position of the column.
        column: Optional current header name.
        position: Optional current physical column position.
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
            rows,
            header_row,
            headers,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        rightmost = (
            max(headers.values())
            if headers
            else 0
        )

        if (
            to_position < 1
            or to_position > rightmost
        ):
            return _error(
                "invalid_position",
                "The destination position is outside the table.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                to_position=to_position,
                first_position=1,
                last_position=rightmost,
            )

        (
            from_position,
            target_header,
            error,
        ) = _resolve_column_target(
            column=column,
            position=position,
            rows=rows,
            header_row=header_row,
            column_count=(
                properties
                .get("gridProperties", {})
                .get("columnCount")
            ),
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )

        if error:
            return error

        assert from_position is not None

        if to_position == from_position:
            return {
                "ok": True,
                "operation": "move_column",
                "spreadsheet": spreadsheet_name,
                "sheet": sheet_name,
                "column": target_header,
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
            "column": target_header,
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
    formula: str,
    column: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Fill an existing physical column with a relative spreadsheet formula.

    Identify the destination column by its header name, physical position,
    or both.

    Use position for unnamed columns or when duplicate header names are
    ambiguous. If both column and position are supplied, they must identify
    the same physical column.

    The formula is written to the first data row and copied downward so
    relative references change naturally from row to row.

    Args:
        formula: Formula as typed in Google Sheets, beginning with "=".
        column: Optional header name of the destination column.
        position: Optional physical destination column position.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    if not formula or not formula.strip():
        return _error(
            "missing_formula",
            "A formula must be supplied.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

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
            rows,
            header_row,
            headers,
            last_row,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        (
            target_position,
            target_header,
            error,
        ) = _resolve_column_target(
            column=column,
            position=position,
            rows=rows,
            header_row=header_row,
            column_count=(
                properties
                .get("gridProperties", {})
                .get("columnCount")
            ),
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
        )

        if error:
            return error

        assert target_position is not None

        first_data_row = header_row + 1

        if last_row < first_data_row:
            return _error(
                "no_data_rows",
                "There are no data rows to fill.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                column=target_header,
                position=target_position,
            )

        # Formula text must be interpreted by Google Sheets.
        spreadsheet_service.update_cells(
            spreadsheet_id=spreadsheet_id,
            updates=[
                {
                    "range": a1(
                        sheet_name,
                        first_row=first_data_row,
                        last_row=first_data_row,
                        first_column=target_position,
                        last_column=target_position,
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
                    target_position,
                    target_position,
                ),
                destination=to_grid_range(
                    properties["sheetId"],
                    first_data_row + 1,
                    last_row,
                    target_position,
                    target_position,
                ),
                paste_type="PASTE_FORMULA",
            )

        filled_rows = (
            last_row
            - first_data_row
            + 1
        )

        return {
            "ok": True,
            "operation": "set_column_formula",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": target_header,
            "position": target_position,
            "column_letter": column_letter(
                target_position
            ),
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