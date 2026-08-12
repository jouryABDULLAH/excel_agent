"""Tool for the charts on a sheet."""

from typing import Literal

from langchain_core.tools import tool

from excel_agent.tracing import traced


@tool
@traced
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

    Call inspect_sheet first, so the column names you use are the real ones.

    Args:
        action: What to do.
        kind: What sort of chart to draw. Needed for add only.
        labels_column: The column whose values label the chart, by header
            name. Needed for add only.
        value_columns: The columns to plot, by header name. Needed for add
            only.
        title: What the chart should be called. Needed for add and retitle.
        chart: Which chart to change, by the number inspect_sheet gives it.
            Needed for remove and retitle.
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed.
    """
    raise NotImplementedError
