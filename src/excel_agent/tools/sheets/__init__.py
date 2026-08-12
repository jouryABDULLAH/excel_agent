"""The tools the agent is allowed to use on Google spreadsheets.

The names match the local tools one for one, so a prompt or a subagent that
names a tool reads the same whichever backend is in use. modify_style is the
exception: it has no openpyxl equivalent.
"""

from excel_agent.tools.sheets.charts import modify_chart
from excel_agent.tools.sheets.columns import modify_column
from excel_agent.tools.sheets.inspect import inspect_sheet
from excel_agent.tools.sheets.modify import modify_sheet
from excel_agent.tools.sheets.spreadsheets import list_workbooks
from excel_agent.tools.sheets.stats import sheet_stats
from excel_agent.tools.sheets.style import modify_style

SHEETS_TOOLS = [
    list_workbooks,
    inspect_sheet,
    sheet_stats,
    modify_sheet,
    modify_column,
    modify_chart,
    modify_style,
]
