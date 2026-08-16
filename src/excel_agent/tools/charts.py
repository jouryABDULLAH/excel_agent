"""Tools for creating, updating and deleting spreadsheet charts."""

from typing import Any, Literal

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.services.google import readable
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.sheets import (
    find_header_row,
    header_map,
    last_data_row,
    resolve_spreadsheet,
    to_grid_range,
)


ChartKind = Literal[
    "column",
    "bar",
    "line",
    "area",
    "scatter",
    "pie",
]


BASIC_KINDS = {
    "column": "COLUMN",
    "bar": "BAR",
    "line": "LINE",
    "area": "AREA",
    "scatter": "SCATTER",
}


CHART_ROWS = 18


def _error(
    code: str,
    message: str,
    *,
    spreadsheet: str | None = None,
    sheet: str | None = None,
    **details: Any,
) -> dict:
    return {
        "ok": False,
        "error": code,
        "message": message,
        "spreadsheet": spreadsheet,
        "sheet": sheet,
        **details,
    }


def _source(
    sheet_id: int,
    column: int,
    first_row: int,
    last_row: int,
) -> dict:
    """Represent one column as a chart data source."""
    return {
        "sourceRange": {
            "sources": [
                to_grid_range(
                    sheet_id,
                    first_row,
                    last_row,
                    column,
                    column,
                )
            ]
        }
    }


def _chart_spec(
    *,
    kind: ChartKind,
    title: str,
    sheet_id: int,
    labels_column: int,
    value_columns: list[int],
    header_row: int,
    last_row: int,
) -> dict:
    """Build an EmbeddedChart specification."""
    domain = _source(
        sheet_id,
        labels_column,
        header_row,
        last_row,
    )

    series = [
        _source(
            sheet_id,
            column,
            header_row,
            last_row,
        )
        for column in value_columns
    ]

    if kind == "pie":
        return {
            "title": title,
            "pieChart": {
                "legendPosition": "RIGHT_LEGEND",
                "domain": domain,
                "series": series[0],
            },
        }

    return {
        "title": title,
        "basicChart": {
            "chartType": BASIC_KINDS[kind],
            "legendPosition": "BOTTOM_LEGEND",
            "headerCount": 1,
            "domains": [
                {
                    "domain": domain,
                }
            ],
            "series": [
                {
                    "series": one,
                    "targetAxis": "LEFT_AXIS",
                }
                for one in series
            ],
        },
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
    """Resolve the sheet and read its table/chart structure."""
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

    charts = spreadsheet_service.list_charts(
        spreadsheet_id,
        sheet_name,
    )

    return (
        spreadsheet_id,
        spreadsheet_name,
        properties,
        charts,
        header_row,
        headers,
        last_row,
    )


def _chart_by_id(
    charts: list[dict],
    chart_id: int,
) -> dict | None:
    """Find one chart by Google's stable chart ID."""
    for chart in charts:
        if chart.get("chartId") == chart_id:
            return chart

    return None


@tool
def create_chart(
    kind: ChartKind,
    labels_column: str,
    value_columns: list[str],
    title: str,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Create an embedded chart from existing table columns.

    Args:
        kind: Chart type: column, bar, line, area, scatter, or pie.
        labels_column: Column supplying category/x-axis labels.
        value_columns: One or more columns containing values to plot.
        title: Chart title.
        spreadsheet: Spreadsheet name. Omit for the current spreadsheet.
        sheet: Sheet name. Omit for the first sheet.
    """
    if not title or not title.strip():
        return _error(
            "missing_title",
            "The chart needs a title.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if not labels_column or not labels_column.strip():
        return _error(
            "missing_labels_column",
            "The chart needs a labels column.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    if not value_columns:
        return _error(
            "missing_value_columns",
            "The chart needs at least one value column.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    labels_column = labels_column.strip()
    value_columns = [
        column.strip()
        for column in value_columns
        if column and column.strip()
    ]

    if not value_columns:
        return _error(
            "missing_value_columns",
            "The chart needs at least one value column.",
            spreadsheet=spreadsheet,
            sheet=sheet,
        )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            charts,
            header_row,
            headers,
            last_row,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        if not headers:
            return _error(
                "headers_not_found",
                "No column headers were found.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
            )

        requested = [
            labels_column,
            *value_columns,
        ]

        unknown = [
            column
            for column in requested
            if column not in headers
        ]

        if unknown:
            return _error(
                "unknown_columns",
                "One or more chart columns do not exist.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                unknown_columns=unknown,
                available_columns=list(headers),
            )

        if last_row <= header_row:
            return _error(
                "no_data_rows",
                "There are no data rows to chart.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
            )

        used_value_columns = value_columns

        # A pie has only one series.
        if kind == "pie":
            used_value_columns = value_columns[:1]

        anchor = {
            "sheetId": properties["sheetId"],
            "rowIndex": len(charts) * CHART_ROWS,
            # Grid position is zero-based. The 1-based number of the final
            # named column is therefore already the first free zero-based
            # column index.
            "columnIndex": max(headers.values()),
        }

        spec = _chart_spec(
            kind=kind,
            title=title.strip(),
            sheet_id=properties["sheetId"],
            labels_column=headers[labels_column],
            value_columns=[
                headers[column]
                for column in used_value_columns
            ],
            header_row=header_row,
            last_row=last_row,
        )

        created = spreadsheet_service.add_chart(
            spreadsheet_id,
            {
                "spec": spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": anchor,
                    }
                },
            },
        )

        chart_id = created.get("chartId")

        return {
            "ok": True,
            "operation": "create_chart",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "chart_id": chart_id,
            "title": title.strip(),
            "kind": kind,
            "labels_column": labels_column,
            "value_columns": used_value_columns,
            "first_data_row": header_row + 1,
            "last_data_row": last_row,
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
def update_chart(
    chart_id: int,
    title: str | None = None,
    kind: ChartKind | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Update an existing chart's title and/or chart type.

    The chart is addressed by its stable Google Sheets chart ID.

    Changing between basic chart types such as column, bar, line, area and
    scatter preserves the chart's existing data ranges. Changing to or from a
    pie chart is not supported by this operation because pie and basic charts
    use different specification shapes.

    Args:
        chart_id: Stable ID of the chart to update.
        title: Optional new title.
        kind: Optional new chart type.
        spreadsheet: Spreadsheet name. Omit for the current spreadsheet.
        sheet: Sheet name. Omit for the first sheet.
    """
    if title is None and kind is None:
        return _error(
            "no_chart_change",
            "No chart change was supplied.",
            spreadsheet=spreadsheet,
            sheet=sheet,
            chart_id=chart_id,
        )

    if title is not None:
        title = title.strip()

        if not title:
            return _error(
                "missing_title",
                "The new chart title cannot be blank.",
                spreadsheet=spreadsheet,
                sheet=sheet,
                chart_id=chart_id,
            )

    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            charts,
            _,
            _,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        chart = _chart_by_id(
            charts,
            chart_id,
        )

        if chart is None:
            return _error(
                "chart_not_found",
                "No chart with that ID exists on this sheet.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                chart_id=chart_id,
                available_chart_ids=[
                    chart.get("chartId")
                    for chart in charts
                ],
            )

        spec = dict(chart.get("spec", {}))

        old_title = spec.get("title", "")
        old_kind: str | None = None

        if "pieChart" in spec:
            old_kind = "pie"

        elif "basicChart" in spec:
            api_kind = (
                spec.get("basicChart", {})
                .get("chartType")
            )

            old_kind = next(
                (
                    name
                    for name, api_name
                    in BASIC_KINDS.items()
                    if api_name == api_kind
                ),
                None,
            )

        if title is not None:
            spec["title"] = title

        if kind is not None and kind != old_kind:
            if kind == "pie" or old_kind == "pie":
                return _error(
                    "incompatible_chart_type_change",
                    (
                        "Changing to or from a pie chart requires recreating "
                        "the chart because pie and basic charts use different "
                        "data specifications."
                    ),
                    spreadsheet=spreadsheet_name,
                    sheet=sheet_name,
                    chart_id=chart_id,
                    current_kind=old_kind,
                    requested_kind=kind,
                )

            basic = dict(
                spec.get("basicChart", {})
            )
            basic["chartType"] = BASIC_KINDS[kind]
            spec["basicChart"] = basic

        spreadsheet_service.update_chart_spec(
            spreadsheet_id,
            chart_id,
            spec,
        )

        return {
            "ok": True,
            "operation": "update_chart",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "chart_id": chart_id,
            "old_title": old_title,
            "title": spec.get("title"),
            "old_kind": old_kind,
            "kind": kind or old_kind,
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
def delete_chart(
    chart_id: int,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> dict:
    """Delete one embedded chart by its stable chart ID.

    The source spreadsheet data is not deleted.

    Args:
        chart_id: Stable Google Sheets chart ID.
        spreadsheet: Spreadsheet name. Omit for the current spreadsheet.
        sheet: Sheet name. Omit for the first sheet.
    """
    try:
        (
            spreadsheet_id,
            spreadsheet_name,
            properties,
            charts,
            _,
            _,
            _,
        ) = _load_table(
            spreadsheet,
            sheet,
        )

        sheet_name = properties["title"]

        chart = _chart_by_id(
            charts,
            chart_id,
        )

        if chart is None:
            return _error(
                "chart_not_found",
                "No chart with that ID exists on this sheet.",
                spreadsheet=spreadsheet_name,
                sheet=sheet_name,
                chart_id=chart_id,
                available_chart_ids=[
                    chart.get("chartId")
                    for chart in charts
                ],
            )

        title = (
            chart.get("spec", {})
            .get("title", "")
        )

        spreadsheet_service.delete_chart(
            spreadsheet_id,
            chart_id,
        )

        return {
            "ok": True,
            "operation": "delete_chart",
            "spreadsheet": spreadsheet_name,
            "sheet": sheet_name,
            "chart_id": chart_id,
            "title": title,
            "data_changed": False,
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