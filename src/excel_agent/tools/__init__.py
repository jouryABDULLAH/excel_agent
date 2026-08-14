"""The tools the agent is allowed to use."""

from excel_agent.config import BACKEND
from excel_agent.tools.charts import modify_chart
from excel_agent.tools.columns import modify_column
from excel_agent.tools.modify import modify_row
from excel_agent.tools.inspect import inspect_sheet
from excel_agent.tools.stats import sheet_stats
from excel_agent.tools.workbooks import list_workbooks

# The workbooks in the data folder, through openpyxl.
LOCAL_TOOLS = [
    list_workbooks,
    inspect_sheet,
    sheet_stats,
    modify_row,
    modify_column,
    modify_chart,
]


def select_tools(backend: str) -> list:
    """The tools for one backend.

    The Google set is imported only when it is asked for, so working on a
    local workbook needs no credentials, no token and no network.

    Raises ValueError, naming both backends, when asked for neither.
    """
    if backend == "local":
        return LOCAL_TOOLS
    if backend == "sheets":
        from excel_agent.tools.sheets import SHEETS_TOOLS

        return SHEETS_TOOLS

    raise ValueError(f'EXCEL_AGENT_BACKEND is "{backend}". Use "local" or "sheets".')


# What the agent is handed. Which set that is comes from config, so nothing
# downstream has to know there is more than one.
TOOLS = select_tools(BACKEND)
