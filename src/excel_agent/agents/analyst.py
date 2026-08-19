"""Reads the spreadsheet and answers questions about it. Changes nothing."""

from langchain.agents import create_agent

from excel_agent.agents._shared import WorkerState, worker_node
from excel_agent.subagents.prompts import ANALYST_PROMPT
from excel_agent.tools import find_data, inspect_sheet, sheet_stats


NAME = "analyst"

TOOLS = (
    inspect_sheet,
    find_data,
    sheet_stats,
)


def build(model):
    """The analyst, as a graph node."""
    return worker_node(
        NAME,
        create_agent(
            model=model,
            tools=list(TOOLS),
            system_prompt=ANALYST_PROMPT,
            state_schema=WorkerState,
        ),
    )