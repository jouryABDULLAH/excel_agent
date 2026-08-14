"""The tools the agent is allowed to use on Google spreadsheets."""

from excel_agent.tools.charts import modify_chart
from excel_agent.tools.columns import modify_column
from excel_agent.tools.find import find_data
from excel_agent.tools.inspect import inspect_sheet
from excel_agent.tools.modify import modify_row
from excel_agent.tools.spreadsheets import (
    find_spreadsheet,
    list_workbooks,
    use_spreadsheet,
)
from excel_agent.tools.stats import sheet_stats
from excel_agent.tools.style import modify_style

TOOLS = [
    list_workbooks,
    find_spreadsheet,
    use_spreadsheet,
    inspect_sheet,
    find_data,
    sheet_stats,
    modify_row,
    modify_column,
    modify_chart,
    modify_style,
]
