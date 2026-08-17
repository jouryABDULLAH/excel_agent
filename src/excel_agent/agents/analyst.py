"""Reads and summarizes sheet data without writing to it."""

from langchain.agents import create_agent

from excel_agent.subagents.prompts import ANALYST_PROMPT
from excel_agent.tools import find_data, inspect_sheet, sheet_stats


def build_analyst(model):
    return create_agent(
        model=model,
        tools=[inspect_sheet, find_data, sheet_stats],
        system_prompt=ANALYST_PROMPT,
    )
