"""Tool for the charts on a sheet."""

from typing import Literal

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    batch,
    chart_kind,
    chart_title,
    charts_in,
    find_header_row,
    grid,
    header_map,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
    to_grid_range,
)

# A pie chart is described differently from the rest, so it is the one kind
# that does not go through basicChart.
BASIC_KINDS = {"column": "COLUMN", "bar": "BAR", "line": "LINE", "scatter": "SCATTER"}
KINDS = (*BASIC_KINDS, "pie")

# Where a new chart is anchored, and how far the next one sits below it, so
# two charts do not land on top of each other.
CHART_ROWS = 18


def source(sheet_id: int, column: int, first_row: int, last_row: int) -> dict:
    """One column of a sheet, as a chart reads it.

    The header row is taken in on purpose: Google names a series after the
    first cell of its range, so including it is what gives the legend the
    column's own name instead of "Series 1".
    """
    return {
        "sourceRange": {
            "sources": [to_grid_range(sheet_id, first_row, last_row, column, column)]
        }
    }


def spec_for(
    kind: str,
    title: str,
    sheet_id: int,
    labels: int,
    values: list[int],
    header_row: int,
    last_row: int,
) -> dict:
    """What to draw, in the shape the API wants it."""
    domain = source(sheet_id, labels, header_row, last_row)
    series = [source(sheet_id, column, header_row, last_row) for column in values]

    if kind == "pie":
        # A pie has one ring, so only the first column asked for is drawn.
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
            "domains": [{"domain": domain}],
            "series": [{"series": one, "targetAxis": "LEFT_AXIS"} for one in series],
        },
    }


@tool
def modify_chart(
    action: Literal["add", "remove", "retitle"],
    kind: Literal["column", "bar", "line", "pie", "scatter"] | None = None,
    labels_column: str | None = None,
    value_columns: list[str] | None = None,
    title: str | None = None,
    chart: int | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Add a chart to the sheet, remove one, or change its title.

    Call inspect_sheet first, so the column names you use are the real ones,
    and so the chart numbers are the ones the sheet has now.

    Args:
        action: What to do.
        kind: What sort of chart to draw. Needed for add only.
        labels_column: The column whose values label the chart, by header
            name. Needed for add only.
        value_columns: The columns to plot, by header name. Needed for add
            only. A pie chart draws the first of them.
        title: What the chart should be called. Needed for add and retitle.
        chart: Which chart to change, by the number inspect_sheet gives it.
            Needed for remove and retitle.
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed. Nothing is drawn when the answer is an explanation.

    Examples:
        modify_chart(action="add", kind="column", labels_column="Product",
                     value_columns=["Units"], title="Units by product")
        modify_chart(action="retitle", chart=1, title="Sales by region")
        modify_chart(action="remove", chart=1)
    """
    try:
        spreadsheet_id, name = resolve_spreadsheet(spreadsheet)
        properties = resolve_sheet(spreadsheet_id, sheet)
        drawn = charts_in(spreadsheet_id, properties["title"])
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    sheet_id = properties["sheetId"]
    where = f"({properties['title']} in {name})"

    # Everything is checked before anything is drawn, so a rejected call
    # leaves the sheet exactly as it was.
    if action in ("remove", "retitle"):
        if chart is None:
            return (
                f"The {action} action needs the number of a chart. Call "
                "inspect_sheet to see which charts there are."
            )
        if not drawn:
            return f"There are no charts on this sheet. {where}"
        if chart < 1 or chart > len(drawn):
            return (
                f"There is no chart {chart}. This sheet has "
                f"{len(drawn)} of them, numbered 1 to {len(drawn)}. {where}"
            )

    if action in ("add", "retitle") and (not title or not title.strip()):
        return f"The {action} action needs a title to give the chart."

    if action == "add":
        # A kind that is not one of these is refused by the schema before the
        # tool runs, so what is left to catch here is not naming one at all.
        if kind is None:
            return f'The add action needs a kind: {", ".join(KINDS)}.'
        if not labels_column:
            return "The add action needs labels_column, the column to label by."
        if not value_columns:
            return "The add action needs value_columns, the columns to plot."

        try:
            rows = grid(spreadsheet_id, properties["title"])
        except HttpError as failure:
            return readable(failure)

        header_row = find_header_row(rows)
        headers = header_map(rows, header_row)
        if not headers:
            return (
                f"No column names were found, so there is nothing to plot "
                f"by name. {where}"
            )

        unknown = [
            column
            for column in [labels_column, *value_columns]
            if column not in headers
        ]
        if unknown:
            return (
                f"Unknown column(s): {', '.join(unknown)}. "
                f"The sheet has: {', '.join(headers)}. {where}"
            )

        last_row = last_data_row(rows, header_row)
        if last_row <= header_row:
            return f"There are no rows of data to draw a chart from. {where}"

    try:
        if action == "add":
            assert title and kind and labels_column and value_columns
            # Anchored clear of the data, and one chart's depth lower for each
            # one already there, so a second chart does not cover the first.
            # An anchor counts from zero, while a column number counts from
            # one, so the last column's number is already the first free one.
            anchor = {
                "sheetId": sheet_id,
                "rowIndex": len(drawn) * CHART_ROWS,
                "columnIndex": max(headers.values()),
            }
            batch(
                spreadsheet_id,
                [
                    {
                        "addChart": {
                            "chart": {
                                "spec": spec_for(
                                    kind,
                                    title.strip(),
                                    sheet_id,
                                    headers[labels_column],
                                    [headers[column] for column in value_columns],
                                    header_row,
                                    last_row,
                                ),
                                "position": {
                                    "overlayPosition": {"anchorCell": anchor}
                                },
                            }
                        }
                    }
                ],
            )
            plotted = ", ".join(value_columns)
            drawn_note = (
                " A pie draws one ring, so only the first was used."
                if kind == "pie" and len(value_columns) > 1
                else ""
            )
            return (
                f'Drew a {kind} chart called "{title.strip()}", plotting '
                f"{plotted} against {labels_column}, over rows "
                f"{header_row + 1} to {last_row}.{drawn_note} It covers the "
                "rows that were there when it was drawn: rows added later are "
                f"not in it. {where}"
            )

        if action == "remove":
            assert chart is not None
            going = drawn[chart - 1]
            spec = going.get("spec", {})
            batch(
                spreadsheet_id,
                [{"deleteEmbeddedObject": {"objectId": going["chartId"]}}],
            )
            return (
                f'Removed chart {chart}, "{chart_title(spec)}" '
                f"({chart_kind(spec)}). The data it was drawn from is "
                f"untouched, and the charts after it have moved up a number. "
                f"{where}"
            )

        if action == "retitle":
            assert chart is not None and title is not None
            changing = drawn[chart - 1]
            # The whole spec goes back with one thing altered, because there
            # is no way to change a title on its own.
            spec = dict(changing.get("spec", {}))
            was = chart_title(spec)
            spec["title"] = title.strip()
            batch(
                spreadsheet_id,
                [
                    {
                        "updateChartSpec": {
                            "chartId": changing["chartId"],
                            "spec": spec,
                        }
                    }
                ],
            )
            return (
                f'Renamed chart {chart} from "{was}" to "{title.strip()}". '
                f"What it draws is unchanged. {where}"
            )
    except HttpError as failure:
        return readable(failure)

    return f'Unknown action "{action}". Use add, remove or retitle.'


CASES = [
    ("add with no kind", {"action": "add", "title": "X",
                          "labels_column": "Region", "value_columns": ["Units"]}),
    ("add with no title", {"action": "add", "kind": "column",
                           "labels_column": "Region", "value_columns": ["Units"]}),
    ("a column that does not exist", {"action": "add", "kind": "column", "title": "X",
                                      "labels_column": "Nonsense", "value_columns": ["Units"]}),
    ("remove with no number", {"action": "remove"}),
    ("a chart number that does not exist", {"action": "remove", "chart": 99}),
]


def main() -> None:
    """Try the refusals by hand with `python -m excel_agent.tools.charts`.

    Only the calls that draw nothing are here. Anything that writes belongs in
    a scratch spreadsheet, run by hand.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(modify_chart.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
