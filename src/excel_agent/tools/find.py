"""Tool for finding rows by their cell contents."""

from typing import Any

from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools.runtime import chosen
from excel_agent.sheets import (
    cell,
    find_header_row,
    addressable,
    header_map,
    sheet_width,
    is_blank,
    last_data_row,
    resolve_spreadsheet,
)
from excel_agent.tools.inspect import _with_columns


MATCH_LIMIT = 30


# How a comparison is written, longest first so ">=" is not read as ">".
COMPARISONS = (">=", "<=", "!=", ">", "<", "=")


def _compared(wanted: str) -> tuple[str, float] | None:
    """A numeric comparison the query asks for, or None.

    "> 10" is a comparison; "10" on its own is text to look for, because
    that is what someone searching an order number means.
    """
    asked = wanted.strip()

    for sign in COMPARISONS:
        if asked.startswith(sign):
            try:
                return sign, float(
                    asked[len(sign):].strip().replace(",", "").lstrip("$")
                )
            except ValueError:
                return None

    return None


def _number(one) -> float | None:
    """A cell's value as a number, however the sheet displays it."""
    if isinstance(one.value, bool):
        return None

    if isinstance(one.value, (int, float)):
        return float(one.value)

    try:
        return float(
            str(one.displayed or "").strip().replace(",", "").lstrip("$")
        )
    except ValueError:
        return None


def _passes(value: float, sign: str, against: float) -> bool:
    """Whether one number stands in the asked relation to another."""
    return {
        ">": value > against,
        "<": value < against,
        ">=": value >= against,
        "<=": value <= against,
        "=": value == against,
        "!=": value != against,
    }[sign]


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
    render_data: bool = False,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> tuple[str, dict]:
    """Find rows by their cell text, or by a numeric comparison.

    Text is looked for inside a cell. A query beginning >, <, >=, <=, = or
    != compares numbers instead: "> 10" finds every row whose value in the
    searched column is greater than ten, and a cell that is not a number
    never matches.

    Args:
        text: Text to find, or a comparison such as "> 10" or "<= 2024".
        column: Optional column name to search only there.
        whole_cell: Require an exact whole-cell match when True.
        render_data: True when the user asked to see the matching rows
            themselves, so the application draws them as a table. False for a
            search that only informs your answer or a later step.
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

    # "> 10" compares; "10" is looked for as text, because a bare number is
    # what someone searching for an order number types.
    comparison = _compared(text)

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
    headers = header_map(rows, header_row, sheet_width(properties))


    available_columns = addressable(headers)

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

            if (
                _passes(number, comparison[0], comparison[1])
                if comparison and (number := _number(one)) is not None
                else (
                    False
                    if comparison
                    else _matches(_shown(one), text, whole_cell)
                )
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
            "render_data": render_data,
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
        "render_data": render_data,
    }