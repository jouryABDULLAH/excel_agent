"""Tool for reading rows from a spreadsheet."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import (
    Cell,
    spreadsheet_service,
)
from excel_agent.sheets import (
    cell,
    chart_kind,
    chart_title,
    find_header_row,
    header_map,
    last_data_row,
    resolve_spreadsheet,
)


ROW_LIMIT = 200


def _as_text(one: Cell) -> str:
    """Render a cell the way the spreadsheet displays it."""
    if one.displayed is not None:
        return str(one.displayed)

    if one.formula:
        return one.formula

    return ""


def _error(
    code: str,
    message: str,
    *,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    **details: Any,
) -> tuple[str, dict]:
    """Return readable tool content plus structured failure metadata."""
    artifact = {
        "ok": False,
        "operation": "inspect_sheet",
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }

    return message, artifact


@tool(response_format="content_and_artifact")
def inspect_sheet(
    columns: list[str] | None = None,
    start_row: int = 1,
    max_rows: int = 20,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> tuple[str, dict]:
    """Read rows with their real Google Sheets row numbers.

    Args:
        columns: Optional column names to return. Omit for every named column.
        start_row: First spreadsheet row to consider.
        max_rows: Maximum number of data rows to return. Hard-capped at 200.
        spreadsheet: Spreadsheet name. Omit for the current spreadsheet.
        sheet: Sheet name. Omit for the first sheet.

    Returns:
        Readable table content plus structured metadata describing the page.
    """
    if max_rows < 1:
        return _error(
            "invalid_max_rows",
            "max_rows must be at least 1.",
            spreadsheet=spreadsheet,
            sheet=sheet,
            max_rows=max_rows,
        )

    try:
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

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)

    if not headers:
        return _error(
            "headers_not_found",
            "No column headers were found.",
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
            header_row=header_row,
        )

    available_columns = list(headers)

    if columns:
        unknown = [
            name
            for name in columns
            if name not in headers
        ]

        if unknown:
            return _error(
                "unknown_columns",
                "One or more requested columns do not exist.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                unknown_columns=unknown,
                available_columns=available_columns,
            )

        selected_columns = list(dict.fromkeys(columns))

    else:
        selected_columns = available_columns

    last_row = last_data_row(rows, header_row)
    total_rows = max(last_row - header_row, 0)

    if total_rows == 0:
        content = (
            f"Sheet: {sheet_name} in {spreadsheet_name}. "
            "It has column names but no rows of data."
        )

        return content, {
            "ok": True,
            "operation": "inspect_sheet",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "header_row": header_row,
            "columns": selected_columns,
            "total_rows": 0,
            "rows": [],
            "returned_rows": 0,
            "has_more": False,
            "next_start_row": None,
            "rendered": content,
        }

    first_data_row = header_row + 1
    first = max(start_row, first_data_row)

    if first > last_row:
        return _error(
            "start_row_after_data",
            (
                f"The sheet ends at row {last_row}, so there is "
                f"nothing to read from row {start_row}."
            ),
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
            total_rows=total_rows,
            last_data_row=last_row,
            requested_start_row=start_row,
        )

    effective_limit = min(max_rows, ROW_LIMIT)
    last = min(
        first + effective_limit - 1,
        last_row,
    )

    result_rows = []

    for row_number in range(first, last + 1):
        result_rows.append(
            {
                "row": row_number,
                "values": {
                    name: _as_text(
                        cell(
                            rows,
                            row_number,
                            headers[name],
                        )
                    )
                    for name in selected_columns
                },
            }
        )

    has_more = last < last_row
    next_start_row = last + 1 if has_more else None

    lines = [
        (
            f"Sheet: {sheet_name} in {spreadsheet_name} "
            f"({total_rows} rows of data, column names in row "
            f"{header_row})."
        ),
        "",
        "| row | " + " | ".join(selected_columns) + " |",
        "|" + "---|" * (len(selected_columns) + 1),
    ]

    for result_row in result_rows:
        values = [
            result_row["values"][name]
            for name in selected_columns
        ]

        lines.append(
            f'| {result_row["row"]} | '
            + " | ".join(values)
            + " |"
        )

    # Deliberately NOT adding:
    #
    # "Rows N to M were not shown. Call again..."
    #
    # That is agent control metadata, not user-facing spreadsheet data.

    try:
        charts = spreadsheet_service.list_charts(
            spreadsheet_id,
            sheet_name,
        )
    except HttpError:
        charts = []

    chart_results = []

    if charts:
        lines.append("")
        lines.append(
            f"{len(charts)} chart(s) on this sheet:"
        )

        for chart in charts:
            spec = chart.get("spec", {})

            chart_result = {
                "chart_id": chart.get("chartId"),
                "title": chart_title(spec),
                "kind": chart_kind(spec),
            }

            chart_results.append(chart_result)

            lines.append(
                f'  chart_id={chart_result["chart_id"]}: '
                f'{chart_result["title"]} '
                f'({chart_result["kind"]})'
            )

    rendered = "\n".join(lines)

    return rendered, {
        "ok": True,
        "operation": "inspect_sheet",
        "spreadsheet": spreadsheet_name,
        "sheet": sheet_name,
        "header_row": header_row,
        "columns": selected_columns,
        "total_rows": total_rows,
        "first_row": first,
        "last_row": last,
        "returned_rows": len(result_rows),
        "rows": result_rows,
        "has_more": has_more,
        "next_start_row": next_start_row,
        "charts": chart_results,
        "rendered": rendered,
    }