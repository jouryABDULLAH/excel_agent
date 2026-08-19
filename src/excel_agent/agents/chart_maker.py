"""Creates, updates and deletes charts."""

from langchain.agents import create_agent

from excel_agent.agents._shared import DELEGATED, WorkerState, worker_node
from excel_agent.prompts import CANNOT_DO, LANGUAGE_AND_SHEET_TEXT
from excel_agent.tools import (
    create_chart,
    delete_chart,
    inspect_sheet,
    update_chart,
)


CHART_PROMPT = f"""\
{DELEGATED}

You create, update and delete charts.

Tool choice:
- create_chart: make a new chart.
- update_chart: change a chart title or compatible chart type.
- delete_chart: remove a chart.
- inspect_sheet: discover headers and stable chart_id values.

Rules:
- Existing charts are addressed by chart_id. Never invent one.
- A pie chart uses one value series.
- Charts plot the rows supplied to them; they do not automatically group
  repeated category values or calculate grouped totals.
- If the user wants one point/bar per unique category and the sheet has
  repeated categories, explain that an aggregated summary table is required.
  Do not pretend the chart performed aggregation.
- Deleting a chart does not delete its source data.

{LANGUAGE_AND_SHEET_TEXT}
{CANNOT_DO}
"""

NAME = "chart_maker"

TOOLS = (
    inspect_sheet,
    create_chart,
    update_chart,
    delete_chart,
)


def build(model):
    """The chart maker, as a graph node."""
    return worker_node(
        NAME,
        create_agent(
            model=model,
            tools=list(TOOLS),
            system_prompt=CHART_PROMPT,
            state_schema=WorkerState,
        ),
    )
