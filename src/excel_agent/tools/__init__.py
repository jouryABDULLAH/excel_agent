"""The tools the agent is allowed to use."""

from excel_agent.tools.columns import modify_column
from excel_agent.tools.modify import modify_sheet
from excel_agent.tools.inspect import inspect_sheet

TOOLS = [inspect_sheet, modify_sheet, modify_column]