"""Tool for finding rows by their cell contents."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.sheets import (
    chosen,
    cell,
    find_header_row,
    header_map,
    is_blank,
    last_data_row,
    resolve_spreadsheet,
)
from excel_agent.tools.inspect import _with_columns


MATCH_LIMIT = 30


def _matches(
    text: str,
    wanted: str,
    whole: bool,
) -> bool:
    """Return whether displayed cell text matches the query."""
    if is_blank(text):
        return False

    text = str(text).strip().lower()
    wanted = wanted.strip().lower()

    return (
        text == wanted
        if whole
        else wanted in text
    )


def _shown(one) -> str:
    """Return displayed cell text without rendering None."""
    if one.displayed is None:
        return ""

    return str(one.displayed)


def _error(
    code: str,
    message: str,
    *,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    **details: Any,
) -> tuple[str, dict]:
    artifact = {
        "ok": False,
        "operation": "find_data",
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }

    return _with_columns(message, details), artifact


@tool(response_format="content_and_artifact")
def find_data(
    text: str,
    column: str | None = None,
    whole_cell: bool = False,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> tuple[str, dict]:
    """Find rows containing some displayed cell text.

    Args:
        text: Text/value to find.
        column: Optional column name to search only there.
        whole_cell: Require an exact whole-cell match when True.
        spreadsheet: Spreadsheet name, not an ID. Omit for the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit for the
            first sheet.

    Returns:
        Readable matching rows plus structured match metadata.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    if is_blank(text):
        return _error(
            "missing_text",
            "Say what to look for.",
            spreadsheet=spreadsheet,
            sheet=sheet,
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
        )

    available_columns = list(headers)

    if column is not None:
        column = column.strip()

        if column not in headers:
            return _error(
                "column_not_found",
                "The requested column does not exist.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                column=column,
                available_columns=available_columns,
            )

        search_columns = [column]

    else:
        search_columns = available_columns

    last_row = last_data_row(
        rows,
        header_row,
    )

    hits = []

    for row_number in range(
        header_row + 1,
        last_row + 1,
    ):
        for name in search_columns:
            one = cell(
                rows,
                row_number,
                headers[name],
            )

            if _matches(
                _shown(one),
                text,
                whole_cell,
            ):
                hits.append(
                    {
                        "row": row_number,
                        "matched_in": name,
                        "values": {
                            header: _shown(
                                cell(
                                    rows,
                                    row_number,
                                    headers[header],
                                )
                            )
                            for header in available_columns
                        },
                    }
                )

                break

    if not hits:
        searched = (
            f'"{column}"'
            if column
            else "any column"
        )

        content = (
            f'Nothing in {searched} holds "{text}", '
            f"in {sheet_name} in {spreadsheet_name}."
        )

        return content, {
            "ok": True,
            "operation": "find_data",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "query": text,
            "column": column,
            "whole_cell": whole_cell,
            "match_count": 0,
            "matches": [],
            "truncated": False,
            "rendered": content,
        }

    shown_hits = hits[:MATCH_LIMIT]
    truncated = len(hits) > MATCH_LIMIT

    lines = [
        (
            f'{len(hits)} row(s) in {sheet_name} in '
            f'{spreadsheet_name} hold "{text}"'
            + (
                f" in {column}"
                if column
                else ""
            )
            + "."
        ),
        "",
        "| row | matched in | "
        + " | ".join(available_columns)
        + " |",
        "|"
        + "---|"
        * (len(available_columns) + 2),
    ]

    for hit in shown_hits:
        values = [
            hit["values"][name]
            for name in available_columns
        ]

        lines.append(
            f'| {hit["row"]} | '
            f'{hit["matched_in"]} | '
            + " | ".join(values)
            + " |"
        )

    if truncated:
        lines.extend(
            [
                "",
                (
                    f"{len(hits) - len(shown_hits)} more "
                    "matching row(s) are not displayed."
                ),
            ]
        )

    rendered = "\n".join(lines)

    return rendered, {
        "ok": True,
        "operation": "find_data",
        "spreadsheet": spreadsheet_name,
        "sheet": sheet_name,
        "query": text,
        "column": column,
        "whole_cell": whole_cell,
        "match_count": len(hits),
        "matches": shown_hits,
        "returned_matches": len(shown_hits),
        "truncated": truncated,
        "rendered": rendered,
    }