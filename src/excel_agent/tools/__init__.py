"""The tools the agent is allowed to use."""

from excel_agent.tools.charts import modify_chart
from excel_agent.tools.columns import modify_column
from excel_agent.tools.modify import modify_sheet
from excel_agent.tools.inspect import inspect_sheet
from excel_agent.tools.stats import sheet_stats
from excel_agent.tools.workbooks import list_workbooks

TOOLS = [
    list_workbooks,
    inspect_sheet,
    sheet_stats,
    modify_sheet,
    modify_column,
    modify_chart,
]
