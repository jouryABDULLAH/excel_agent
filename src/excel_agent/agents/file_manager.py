"""Finds and selects the spreadsheet to work on."""

from langchain.agents import create_agent

from excel_agent.subagents.prompts import FILE_MANAGER_PROMPT
from excel_agent.tools import (
    find_spreadsheet,
    list_workbooks,
    resolve_spreadsheet_choice,
)


def build_file_manager(model):
    return create_agent(
        model=model,
        tools=[list_workbooks, find_spreadsheet, resolve_spreadsheet_choice],
        system_prompt=FILE_MANAGER_PROMPT,
    )
