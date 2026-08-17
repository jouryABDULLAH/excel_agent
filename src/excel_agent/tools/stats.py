"""Tool for summarising one spreadsheet column."""

from collections import Counter
from typing import Any

from googleapiclient.errors import HttpError
from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import (
    Cell,
    spreadsheet_service,
)
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


COMMON_LIMIT = 3


def _is_number(one: Cell) -> bool:
    """Return whether the cell contains an arithmetic number."""
    return (
        isinstance(one.value, (int, float))
        and not isinstance(one.value, bool)
        and not one.is_date
    )


def _rounded(number: float) -> int | float:
    """Keep totals compact without turning everything into text."""
    result = round(number, 2)

    if result == int(result):
        return int(result)

    return result


def _shown(one: Cell) -> str:
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
        "operation": "sheet_stats",
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }

    return _with_columns(message, details), artifact


@tool(response_format="content_and_artifact")
def sheet_stats(
    column: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    runtime: ToolRuntime = None,
) -> tuple[str, dict]:
    """Summarise one complete spreadsheet column.

    Args:
        column: Column name.
        spreadsheet: Spreadsheet name, not an ID. Omit for the current
            spreadsheet.
        sheet: Sheet/tab name, not the spreadsheet name. Omit for the
            first sheet.

    Returns:
        Readable statistics plus structured numerical/textual metadata.
    """
    # Left out, the file is the one the orchestrator handed this
    # specialist, which lives in its state rather than in a global.
    spreadsheet = spreadsheet or chosen(runtime)

    if not column or not column.strip():
        return _error(
            "missing_column",
            "The column to summarise must be named.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    column = column.strip()

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

    if column not in headers:
        return _error(
            "column_not_found",
            "The requested column does not exist.",
            spreadsheet=spreadsheet_name,
            sheet=sheet_name,
            column=column,
            available_columns=list(headers),
        )

    last_row = last_data_row(
        rows,
        header_row,
    )

    if last_row <= header_row:
        content = (
            f'"{column}" in {sheet_name} in '
            f"{spreadsheet_name} has no data rows."
        )

        return content, {
            "ok": True,
            "operation": "sheet_stats",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "column": column,
            "filled": 0,
            "blank": 0,
            "different": 0,
            "formula_count": 0,
            "kind": "empty",
            "rendered": content,
        }

    column_number = headers[column]

    values = [
        cell(
            rows,
            row_number,
            column_number,
        )
        for row_number in range(
            header_row + 1,
            last_row + 1,
        )
    ]

    filled = [
        one
        for one in values
        if not is_blank(one.displayed)
    ]

    blank_count = len(values) - len(filled)

    different = len(
        {
            _shown(one).strip()
            for one in filled
        }
    )

    formula_count = sum(
        1
        for one in filled
        if one.formula
    )

    base = {
        "ok": True,
        "operation": "sheet_stats",
        "spreadsheet": spreadsheet_name,
        "sheet": sheet_name,
        "column": column,
        "filled": len(filled),
        "blank": blank_count,
        "different": different,
        "formula_count": formula_count,
    }

    if not filled:
        content = (
            f'"{column}" in {sheet_name} in '
            f"{spreadsheet_name}: 0 filled, "
            f"{blank_count} blank."
        )

        return content, {
            **base,
            "kind": "empty",
            "rendered": content,
        }

    if all(_is_number(one) for one in filled):
        least = min(
            filled,
            key=lambda one: one.value,
        ) # type: ignore

        greatest = max(
            filled,
            key=lambda one: one.value,
        )

        total = sum(
            one.value
            for one in filled
        )

        content = (
            f'"{column}" in {sheet_name} in '
            f"{spreadsheet_name}: "
            f"{len(filled)} filled, "
            f"{blank_count} blank, "
            f"{different} different. "
            f"{_shown(least)} to {_shown(greatest)}, "
            f"adding up to {_rounded(total)}."
        )

        if formula_count:
            content += (
                f" {formula_count} of them are worked "
                "out by a formula in the sheet."
            )

        return content, {
            **base,
            "kind": "number",
            "minimum": {
                "value": least.value,
                "displayed": _shown(least),
            },
            "maximum": {
                "value": greatest.value,
                "displayed": _shown(greatest),
            },
            "total": _rounded(total),
            "rendered": content,
        }

    if all(one.is_date for one in filled):
        earliest = min(
            filled,
            key=lambda one: one.value,
        )

        latest = max(
            filled,
            key=lambda one: one.value,
        )

        content = (
            f'"{column}" in {sheet_name} in '
            f"{spreadsheet_name}: "
            f"{len(filled)} filled, "
            f"{blank_count} blank, "
            f"{different} different. "
            f"{_shown(earliest)} to {_shown(latest)}."
        )

        return content, {
            **base,
            "kind": "date",
            "earliest": {
                "value": earliest.value,
                "displayed": _shown(earliest),
            },
            "latest": {
                "value": latest.value,
                "displayed": _shown(latest),
            },
            "rendered": content,
        }

    counts = Counter(
        _shown(one).strip()
        for one in filled
    )

    common = counts.most_common(
        COMMON_LIMIT
    )

    repeated = [
        {
            "value": value,
            "count": count,
        }
        for value, count in common
        if count > 1
    ]

    if repeated:
        common_text = ", ".join(
            f'"{item["value"]}" {item["count"]} times'
            for item in repeated
        )

        description = (
            f"most often {common_text}"
        )

    else:
        description = "every value different"

    content = (
        f'"{column}" in {sheet_name} in '
        f"{spreadsheet_name}: "
        f"{len(filled)} filled, "
        f"{blank_count} blank, "
        f"{different} different. "
        f"{description}."
    )

    if formula_count:
        content += (
            f" {formula_count} of them are worked out "
            "by a formula in the sheet."
        )

    return content, {
        **base,
        "kind": "text",
        "most_common": repeated,
        "rendered": content,
    }