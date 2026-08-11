"""subagents registry.

The description is what the orchestrator reads when it decides where to send a
piece of work, so these four sentences are the routing itself. inspect_sheet
belongs to several of them on purpose: an agent that writes without reading
first is guessing, and a row number handed between two agents is stale before
it arrives.
"""

from dataclasses import dataclass

from excel_agent.subagents.prompts import (
    ANALYST_PROMPT,
    CHART_PROMPT,
    ROW_EDITOR_PROMPT,
    STRUCTURE_PROMPT,
)
from excel_agent.tools.charts import modify_chart
from excel_agent.tools.columns import modify_column
from excel_agent.tools.inspect import inspect_sheet
from excel_agent.tools.modify import modify_sheet
from excel_agent.tools.stats import sheet_stats


@dataclass(frozen=True)
class SubagentSpec:
    """One subagent: what it is called, what it is for, and what it holds."""

    name: str
    description: str
    system_prompt: str
    tools: tuple


SUBAGENTS = (
    SubagentSpec(
        "analyst",
        "Reads the sheet and answers questions about it: what is in it, which "
        "rows match, how many, how much, the largest and smallest. Changes "
        "nothing. Send it anything that only needs looking.",
        ANALYST_PROMPT,
        (inspect_sheet, sheet_stats),
    ),
    SubagentSpec(
        "row_editor",
        "Adds a row, changes the values in a row, or removes a row. Send it "
        "one row's worth of work, and say which row by number or by what is "
        "in it.",
        ROW_EDITOR_PROMPT,
        (inspect_sheet, modify_sheet),
    ),
    SubagentSpec(
        "structure_editor",
        "Adds, renames and deletes whole columns. It does not put values into "
        "a column: a new column arrives empty, and filling it is row work.",
        STRUCTURE_PROMPT,
        (inspect_sheet, modify_column),
    ),
    SubagentSpec(
        "chart_maker",
        "Draws a bar, line or pie chart of one column, and takes charts off a "
        "sheet again. Send it the column to plot and what to label it by.",
        CHART_PROMPT,
        (inspect_sheet, modify_chart),
    ),
)