"""Tools for changing spreadsheet columns."""

from typing import Any, Literal

from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools.runtime import chosen
from excel_agent.sheets import (
    a1,
    cell,
    column_letter,
    find_header_row,
    header_map,
    sheet_width,
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
    headers = header_map(rows, header_row, sheet_width(properties))
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


def _same_name(header: str, wanted: str) -> bool:
    """Whether a header is the one asked for, ignoring how it was capitalised."""
    return header == wanted or header.casefold() == wanted.casefold()


def _matching(
    named: list[tuple[int, str]],
    column: str,
) -> list[tuple[int, str]]:
    """The columns a name reaches, an exact match winning over a casefolded one.

    Capitalisation is how someone writes a header, not what tells two columns
    apart: "profit margin" means the "Profit Margin" column. Exact wins so a
    sheet holding both spellings still reaches the one actually named.
    """
    exact = [one for one in named if one[1] == column]

    if exact:
        return exact

    return [one for one in named if _same_name(one[1], column)]


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

        if column is not None and not _same_name(
            actual_header,
            column,
        ):
            # Where the named column really is, so the next call can be
            # right rather than being another guess.
            elsewhere = [
                found_position
                for found_position, _ in _matching(
                    _named_headers(
                        rows,
                        header_row,
                        width,
                    ),
                    column,
                )
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

    matching = _matching(named_positions, column)
    matching_positions = [one for one, _ in matching]

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

    # The sheet's own spelling, so what comes back names the real column
    # rather than however it was asked for.
    return (
        matching[0][0],
        matching[0][1],
        None,
    )

@tool
def insert_column(
    name: str | None = None,
    position: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
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
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

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

        # Only a position of nought or less is impossible: a column may be
        # created out past the last named one, as it can in Sheets.
        if position < 1:
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
    runtime: ToolRuntime = None,
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
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

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
    runtime: ToolRuntime = None,
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
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

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
    runtime: ToolRuntime = None,
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
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

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
    mode: Literal["fill_down", "spill"] = "fill_down",
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Put a spreadsheet formula in an existing physical column.

    Identify the destination column by its header name, physical position,
    or both.

    Use position for unnamed columns or when duplicate header names are
    ambiguous. If both column and position are supplied, they must identify
    the same physical column.

    Choose mode by the kind of formula:

    - "fill_down" for a formula that computes one row, written per row with
      its references shifted for each: "=F2*H2" becomes "=F3*H3" on the next
      row, and so on down the column.
    - "spill" for a formula that produces the whole column by itself, such as
      anything beginning "=ARRAYFORMULA(". It is written to the first data
      row only and Sheets fills the rest. Filling one of these down instead
      puts a competing copy in every row, and Sheets rejects the collision by
      showing #REF! in all of them.

    Args:
        formula: Formula as typed in Google Sheets, beginning with "=".
        column: Optional header name of the destination column.
        position: Optional physical destination column position.
        mode: "fill_down" to compute each row, "spill" to let one formula
            produce the column.
        spreadsheet: Spreadsheet name, not an ID. Omit to use the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit to use the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

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

        # A table with headers and nothing under them used to be refused.
        # Sheets lets a formula go in any cell, so the first data row is
        # written and the column starts there.
        last_row = max(last_row, first_data_row)

        # A spilling formula fills the column itself, so a copy in every row
        # would be overlapping spills, which Sheets blocks with #REF!.
        written_to_row = (
            first_data_row
            if mode == "spill"
            else last_row
        )

        # Sheets shifts relative references for each cell repeated into, so
        # "=F2*H2" reads "=F3*H3" a row lower.
        spreadsheet_service.repeat_cell(
            spreadsheet_id=spreadsheet_id,
            grid_range=to_grid_range(
                properties["sheetId"],
                first_data_row,
                written_to_row,
                target_position,
                target_position,
            ),
            cell={
                "userEnteredValue": {
                    "formulaValue": formula,
                }
            },
            fields="userEnteredValue",
        )

        filled_rows = (
            written_to_row
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
            "mode": mode,
            "first_row": first_data_row,
            "last_row": written_to_row,
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