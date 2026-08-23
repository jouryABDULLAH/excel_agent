"""Tool for changing how spreadsheet cells are displayed."""

from typing import Any, Literal
from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools.runtime import chosen
from excel_agent.sheets import (
    find_header_row,
    addressable,
    header_map,
    sheet_width,
    last_data_row,
    resolve_spreadsheet,
    to_grid_range,
)


# Common colours the model/user can name directly.
# Any other colour can still be supplied as #RRGGBB.
COLOURS = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#f4cccc",
    "orange": "#fce5cd",
    "yellow": "#fff2cc",
    "green": "#d9ead3",
    "blue": "#cfe2f3",
    "purple": "#d9d2e9",
    "grey": "#efefef",
    "gray": "#efefef",
}


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


def _as_colour(given: str) -> dict | None:
    """Convert a colour name or #RRGGBB value to Sheets RGB fractions."""
    text = given.strip().lower()
    text = COLOURS.get(text, text)

    if not text.startswith("#") or len(text) != 7:
        return None

    try:
        red = int(text[1:3], 16) / 255
        green = int(text[3:5], 16) / 255
        blue = int(text[5:7], 16) / 255
    except ValueError:
        return None

    return {
        "red": red,
        "green": green,
        "blue": blue,
    }


def _as_number_format(pattern: str) -> dict:
    """Infer the Sheets number-format type from its pattern."""
    lowered = pattern.lower()

    if "%" in pattern:
        kind = "PERCENT"
    elif any(
        part in lowered
        for part in ("yyyy", "yy", "mmm", "dd")
    ):
        kind = "DATE"
    elif any(
        symbol in pattern
        for symbol in ("$", "£", "€", "¥", "﷼")
    ):
        kind = "CURRENCY"
    else:
        kind = "NUMBER"

    return {
        "type": kind,
        "pattern": pattern,
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
    """Resolve the sheet and read enough structure to target formatting."""
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
        header_row,
        headers,
        last_row,
    )

def _format_area(
    *,
    properties: dict,
    headers: dict[str, int],
    header_row: int,
    end_of_data: int,
    column: str | None,
    last_column: str | None,
    first_row: int | None,
    last_row: int | None,
) -> tuple[dict, int, int, int, int]:
    """Resolve one source/destination formatting area.

    Returns:
        grid range,
        first row,
        last row,
        height,
        width.
    """
    if first_row is None:
        # A sheet with no header starts at row 1, not row 0.
        first = max(header_row, 1)
        end = end_of_data
    else:
        first = first_row
        end = (
            last_row
            if last_row is not None
            else first_row
        )

    if last_row is not None and first_row is None:
        raise ValueError(
            "last_row cannot be used without first_row."
        )

    if first < 1 or end < first:
        raise ValueError(
            f"Rows {first} to {end} are not a valid range."
        )

    grid_rows = properties.get(
        "gridProperties",
        {},
    ).get("rowCount")

    if grid_rows is not None and end > grid_rows:
        raise ValueError(
            f"Row {end} is beyond the sheet grid of "
            f"{grid_rows} rows."
        )

    if column is not None:
        first_number = headers[column]
        # A span runs between the two named columns whichever way round
        # they were given; one column named alone is a span of one.
        last_number = (
            headers[last_column]
            if last_column is not None
            else first_number
        )

        first_column = min(first_number, last_number)
        last_column = max(first_number, last_number)
    else:
        # No names means the whole width of the data: with nothing named,
        # "every named column" is every column there is.
        first_column = min(headers.values(), default=1)
        last_column = max(headers.values(), default=headers.width or 1)

    grid_range = to_grid_range(
        properties["sheetId"],
        first,
        end,
        first_column,
        last_column,
    )

    height = end - first + 1
    width = last_column - first_column + 1

    return (
        grid_range,
        first,
        end,
        height,
        width,
    )

@tool
def format_range(
    columns: list[str] | None = None,
    first_row: int | None = None,
    last_row: int | None = None,

    number_format: str | None = None,
    clear_number_format: bool = False,

    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    strikethrough: bool | None = None,

    font_size: int | None = None,

    font_color: str | None = None,

    background: str | None = None,
    clear_background: bool = False,

    horizontal_alignment: Literal[
        "LEFT",
        "CENTER",
        "RIGHT",
    ] | None = None,

    vertical_alignment: Literal[
        "TOP",
        "MIDDLE",
        "BOTTOM",
    ] | None = None,

    wrap: Literal[
        "WRAP",
        "CLIP",
        "OVERFLOW_CELL",
    ] | None = None,

    border_style: Literal[
        "DOTTED",
        "DASHED",
        "SOLID",
        "SOLID_MEDIUM",
        "SOLID_THICK",
        "DOUBLE",
    ] | None = None,

    border_color: str | None = None,

    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Change how a range of cells is displayed without changing its values.

    Columns are addressed by their actual header names. Leave columns out to
    format every named column in the requested rows.

    When no row bounds are supplied, the format covers the used table from
    its header through its final data row. When first_row is supplied without
    last_row, only that one row is formatted.

    Args:
        columns: Optional column names to format. Leave out for all named
            columns.
        first_row: Optional first row to format.
        last_row: Optional final row. Requires first_row.
        number_format: Google Sheets number-format pattern, for example
            "#,##0.00", "$#,##0.00", "0%", or "dd/mm/yyyy".
        clear_number_format: True to remove the explicitly set number format.
            Cannot be combined with number_format.
        bold: True for bold text or False to remove bold.
        background: Fill colour by common name or "#RRGGBB".
        spreadsheet: Spreadsheet name, not an ID. Omit for the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit for the
            first sheet.
        italic: True to italicize text, False to remove italics.
        underline: True to underline text, False to remove underlining.
        strikethrough: True to strike through text, False to remove it.
        font_size: Size of the text in points, for example 14.
        font_color: Text colour as a common name or "#RRGGBB".
        horizontal_alignment: LEFT, CENTER, or RIGHT.
        vertical_alignment: TOP, MIDDLE, or BOTTOM.
        wrap: WRAP, CLIP, or OVERFLOW_CELL.
        border_style: Apply the same border style to all four sides.
        border_color: Optional border colour. Requires border_style.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)


    if (
        number_format is None
        and not clear_number_format
        and bold is None
        and italic is None
        and underline is None
        and strikethrough is None
        and font_size is None
        and font_color is None
        and background is None
        and not clear_background
        and horizontal_alignment is None
        and vertical_alignment is None
        and wrap is None
        and border_style is None
        and border_color is None
    ):
        return _error(
            "no_format_change",
            "No formatting change was supplied.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if number_format is not None and clear_number_format:
        return _error(
            "conflicting_number_format",
            "number_format and clear_number_format cannot be used together.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if background is not None and clear_background:
        return _error(
            "conflicting_background",
            "background and clear_background cannot be used together.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if border_color is not None and border_style is None:
        return _error(
            "border_style_required",
            "border_color requires border_style.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if last_row is not None and first_row is None:
        return _error(
            "missing_first_row",
            "last_row cannot be used without first_row.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    fill = None

    if background is not None:
        fill = _as_colour(background)

        if fill is None:
            return _error(
                "invalid_colour",
                "The background colour is not a supported name or #RRGGBB value.",
                spreadsheet=spreadsheet,
                sheet=sheet,
                background=background,
                named_colours=sorted(set(COLOURS)),
            )

    font_fill = None

    if font_color is not None:
        font_fill = _as_colour(font_color)

        if font_fill is None:
            return _error(
                "invalid_font_colour",
                (
                    "The font colour is not a supported name "
                    "or #RRGGBB value."
                ),
                spreadsheet=spreadsheet,
                sheet=sheet,
                font_color=font_color,
                named_colours=sorted(set(COLOURS)),
            )


    border_fill = None

    if border_color is not None:
        border_fill = _as_colour(border_color)

        if border_fill is None:
            return _error(
                "invalid_border_colour",
                (
                    "The border colour is not a supported name "
                    "or #RRGGBB value."
                ),
                spreadsheet=spreadsheet,
                sheet=sheet,
                border_color=border_color,
                named_colours=sorted(set(COLOURS)),
            )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            header_row,
            headers,
            end_of_data,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]


        if columns:
            unknown = [
                column
                for column in columns
                if column not in headers
            ]

            if unknown:
                return _error(
                    "unknown_columns",
                    "One or more requested columns do not exist.",
                    spreadsheet=spreadsheet_name,
                    sheet=sheet_name,
                    unknown_columns=unknown,
                    available_columns=addressable(headers),
                )

            selected_columns = list(dict.fromkeys(columns))

        else:
            selected_columns = list(headers)

        # No row bounds means the whole used table.
        if first_row is None:
            first = header_row
            end = end_of_data

        else:
            first = first_row
            end = (
                last_row
                if last_row is not None
                else first_row
            )

        if first < 1 or end < first:
            return _error(
                "invalid_row_range",
                "The requested row range is invalid.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                first_row=first,
                last_row=end,
            )

        # Formatting may legitimately extend below the current data, but it
        # must still fit inside the sheet's allocated grid.
        grid_rows = properties.get(
            "gridProperties",
            {},
        ).get("rowCount")

        if grid_rows is not None and end > grid_rows:
            return _error(
                "row_out_of_grid",
                "The requested row is beyond the sheet grid.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                last_row=end,
                grid_row_count=grid_rows,
            )

        cell_format: dict = {}
        fields: list[str] = []
        changes: dict[str, Any] = {}


        # Number format
        if number_format is not None:
            cell_format["numberFormat"] = _as_number_format(
                number_format
            )

            fields.append(
                "userEnteredFormat.numberFormat"
            )

            changes["number_format"] = number_format

        elif clear_number_format:
            cell_format["numberFormat"] = {}

            fields.append(
                "userEnteredFormat.numberFormat"
            )

            changes["number_format"] = None


        # Text formatting
        text_format: dict = {}

        if bold is not None:
            text_format["bold"] = bold

            fields.append(
                "userEnteredFormat.textFormat.bold"
            )

            changes["bold"] = bold


        if font_size is not None:
            if font_size < 1:
                return _error(
                    "invalid_font_size",
                    "Font size must be at least 1.",
                    spreadsheet=spreadsheet_name,
                    sheet=sheet_name,
                    font_size=font_size,
                )

            text_format["fontSize"] = font_size

            fields.append(
                "userEnteredFormat.textFormat.fontSize"
            )

            changes["font_size"] = font_size


        if italic is not None:
            text_format["italic"] = italic

            fields.append(
                "userEnteredFormat.textFormat.italic"
            )

            changes["italic"] = italic


        if underline is not None:
            text_format["underline"] = underline

            fields.append(
                "userEnteredFormat.textFormat.underline"
            )

            changes["underline"] = underline


        if strikethrough is not None:
            text_format["strikethrough"] = strikethrough

            fields.append(
                "userEnteredFormat.textFormat.strikethrough"
            )

            changes["strikethrough"] = strikethrough


        if font_fill is not None:
            text_format["foregroundColorStyle"] = {
                "rgbColor": font_fill,
            }

            fields.append(
                "userEnteredFormat.textFormat.foregroundColorStyle"
            )

            changes["font_color"] = font_color


        if text_format:
            cell_format["textFormat"] = text_format


        # Background
        if fill is not None:
            cell_format["backgroundColor"] = fill

            fields.append(
                "userEnteredFormat.backgroundColor"
            )

            changes["background"] = background

        elif clear_background:
            fields.append(
                "userEnteredFormat.backgroundColor"
            )

            changes["background"] = None


        # Alignment
        if horizontal_alignment is not None:
            cell_format[
                "horizontalAlignment"
            ] = horizontal_alignment

            fields.append(
                "userEnteredFormat.horizontalAlignment"
            )

            changes[
                "horizontal_alignment"
            ] = horizontal_alignment


        if vertical_alignment is not None:
            cell_format[
                "verticalAlignment"
            ] = vertical_alignment

            fields.append(
                "userEnteredFormat.verticalAlignment"
            )

            changes[
                "vertical_alignment"
            ] = vertical_alignment


        # Wrapping
        if wrap is not None:
            cell_format["wrapStrategy"] = wrap

            fields.append(
                "userEnteredFormat.wrapStrategy"
            )

            changes["wrap"] = wrap


        # Borders
        if border_style is not None:
            border: dict = {
                "style": border_style,
            }

            if border_fill is not None:
                border["colorStyle"] = {
                    "rgbColor": border_fill,
                }

            cell_format["borders"] = {
                "top": dict(border),
                "bottom": dict(border),
                "left": dict(border),
                "right": dict(border),
            }

            fields.extend(
                [
                    "userEnteredFormat.borders.top",
                    "userEnteredFormat.borders.bottom",
                    "userEnteredFormat.borders.left",
                    "userEnteredFormat.borders.right",
                ]
            )

            changes["border"] = {
                "style": border_style,
                "color": border_color,
            }
        ranges = [
            to_grid_range(
                properties["sheetId"],
                first,
                end,
                headers[column],
                headers[column],
            )
            for column in selected_columns
        ]

        spreadsheet_service.format_range(
            spreadsheet_id=spreadsheet_id,
            ranges=ranges,
            cell_format=cell_format,
            fields=fields,
        )

        return {
            "ok": True,
            "operation": "format_range",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "columns": selected_columns,
            "first_row": first,
            "last_row": end,
            "changes": changes,
            "values_changed": False,
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
def copy_format(
    source_column: str | None = None,
    source_last_column: str | None = None,
    source_first_row: int | None = None,
    source_last_row: int | None = None,
    destination_column: str | None = None,
    destination_last_column: str | None = None,
    destination_first_row: int | None = None,
    destination_last_row: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Copy existing formatting from one range to another.

    Values and formulas are not copied.

    Several columns side by side are a span: name the first in
    source_column and the last in source_last_column. To copy the look of
    the Title..Rating headers onto the Pages..Review Count headers, that is
    source_column="Title", source_last_column="Rating",
    destination_column="Pages", destination_last_column="Review Count",
    with both row bounds set to the header row.

    Leave the columns out to use every named column in the table. Leave row
    bounds out to use the whole used table. Giving only first_row means one
    row.

    A smaller source is repeated across a larger destination, the way
    Sheets itself pastes.

    Args:
        source_column: Optional first source column, by header name.
        source_last_column: Optional final source column, for a span.
        source_first_row: Optional first source row.
        source_last_row: Optional final source row.
        destination_column: Optional first destination column, by header
            name.
        destination_last_column: Optional final destination column.
        destination_first_row: Optional first destination row.
        destination_last_row: Optional final destination row.
        spreadsheet: Spreadsheet name, not an ID. Omit for the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit for the
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
            header_row,
            headers,
            end_of_data,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]


        unknown = [
            column
            for column in (
                source_column,
                source_last_column,
                destination_column,
                destination_last_column,
            )
            if column is not None
            and column not in headers
        ]

        if unknown:
            return _error(
                "unknown_columns",
                "One or more requested columns do not exist.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                unknown_columns=unknown,
                available_columns=addressable(headers),
            )

        (
            source_range,
            source_first,
            source_last,
            source_height,
            source_width,
        ) = _format_area(
            properties=properties,
            headers=headers,
            header_row=header_row,
            end_of_data=end_of_data,
            column=source_column,
            last_column=source_last_column,
            first_row=source_first_row,
            last_row=source_last_row,
        )

        # A destination given only a starting point means "the same shape,
        # from here". Asked to copy the formatting of rows 2 and 3 to row 5,
        # a person means rows 5 and 6; taking it as row 5 alone left a
        # two-row source that could not fit in a one-row destination, and the
        # most natural way to ask for this was refused.
        if (
            destination_first_row is not None
            and destination_last_row is None
        ):
            destination_last_row = (
                destination_first_row
                + source_height
                - 1
            )

        (
            destination_range,
            destination_first,
            destination_last,
            destination_height,
            destination_width,
        ) = _format_area(
            properties=properties,
            headers=headers,
            header_row=header_row,
            end_of_data=end_of_data,
            column=destination_column,
            last_column=destination_last_column,
            first_row=destination_first_row,
            last_row=destination_last_row,
        )

        spreadsheet_service.copy_paste(
            spreadsheet_id=spreadsheet_id,
            source=source_range,
            destination=destination_range,
            paste_type="PASTE_FORMAT",
        )

        return {
            "ok": True,
            "operation": "copy_format",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "source": {
                "column": source_column,
                "first_row": source_first,
                "last_row": source_last,
            },
            "destination": {
                "column": destination_column,
                "first_row": destination_first,
                "last_row": destination_last,
            },
            "values_changed": False,
            "formulas_changed": False,
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
def set_sheet_layout(
    freeze_rows: int | None = None,
    freeze_columns: int | None = None,
    column: str | None = None,
    last_column: str | None = None,
    column_width: int | None = None,
    fit_columns: bool = False,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> dict:
    """Freeze rows or columns, and set how wide columns are.

    Freezing keeps the top rows or leftmost columns on screen while the rest
    scrolls, which is how a header row is kept in view. Freeze 0 to unfreeze.

    Width is given in pixels, or fit_columns makes each column exactly as
    wide as its contents, the way double-clicking its edge does. Name one
    column, or a span from column to last_column; leave both out for every
    column in the sheet.

    Args:
        freeze_rows: How many rows to keep on screen. 1 is the header row.
        freeze_columns: How many columns to keep on screen.
        column: First column to resize, by header name or letter.
        last_column: Final column to resize, for a span.
        column_width: Width in pixels.
        fit_columns: True to size the columns to their contents instead.
        spreadsheet: Spreadsheet name, not an ID. Omit for the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit for the
            first sheet.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    sizing = column_width is not None or fit_columns

    if freeze_rows is None and freeze_columns is None and not sizing:
        return _error(
            "no_layout_change",
            "Give something to freeze or a width to set.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if column_width is not None and fit_columns:
        return _error(
            "conflicting_width",
            "Give either column_width or fit_columns, not both.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            header_row,
            headers,
            end_of_data,
        ) = _load_table(spreadsheet, sheet)

        sheet_name = properties["title"]
        changed: dict = {}

        if freeze_rows is not None or freeze_columns is not None:
            spreadsheet_service.freeze(
                spreadsheet_id=spreadsheet_id,
                sheet_id=properties["sheetId"],
                rows=freeze_rows,
                columns=freeze_columns,
            )

            if freeze_rows is not None:
                changed["frozen_rows"] = freeze_rows

            if freeze_columns is not None:
                changed["frozen_columns"] = freeze_columns

        if sizing:
            unknown = [
                one
                for one in (column, last_column)
                if one is not None and one not in headers
            ]

            if unknown:
                return _error(
                    "unknown_columns",
                    "One or more requested columns do not exist.",
                    spreadsheet=spreadsheet_name,
                    sheet=sheet_name,
                    unknown_columns=unknown,
                    available_columns=addressable(headers),
                )

            width = headers.width or max(headers.values(), default=1)

            first = headers[column] if column else 1
            last = (
                headers[last_column]
                if last_column
                else (first if column else width)
            )

            spreadsheet_service.size_columns(
                spreadsheet_id=spreadsheet_id,
                sheet_id=properties["sheetId"],
                first_column=min(first, last),
                last_column=max(first, last),
                pixels=column_width,
            )

            changed["first_column"] = min(first, last)
            changed["last_column"] = max(first, last)
            changed["column_width"] = (
                "fitted" if fit_columns else column_width
            )

        return {
            "ok": True,
            "operation": "set_sheet_layout",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            **changed,
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
