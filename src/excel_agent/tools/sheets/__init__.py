"""The tools the agent is allowed to use on Google spreadsheets.

The names match the local tools one for one, so a prompt or a subagent that
names a tool reads the same whichever backend is in use. modify_style is the
exception: it has no openpyxl equivalent.
"""

from excel_agent.tools.sheets.charts import modify_chart
from excel_agent.tools.sheets.columns import modify_column
from excel_agent.tools.sheets.find import find_data
from excel_agent.tools.sheets.inspect import inspect_sheet
from excel_agent.tools.sheets.modify import modify_row
from excel_agent.tools.sheets.spreadsheets import (
    find_spreadsheet,
    list_workbooks,
    use_spreadsheet,
)
from excel_agent.tools.sheets.stats import sheet_stats
from excel_agent.tools.sheets.style import modify_style

SHEETS_TOOLS = [
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
