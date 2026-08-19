"""Finds which spreadsheet to work on, and settles the choice.

The only specialist that writes something other than its own report: what it
chooses is what every step after it operates on.
"""

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage

from excel_agent.agents._shared import WorkerState, reported, run_worker
from excel_agent.graph.state import State
from excel_agent.subagents.prompts import FILE_MANAGER_PROMPT
from excel_agent.tools import (
    find_spreadsheet,
    list_workbooks,
    resolve_spreadsheet_choice,
)


NAME = "file_manager"

TOOLS = (
    list_workbooks,
    find_spreadsheet,
    resolve_spreadsheet_choice,
)


def selected_spreadsheet(messages: list) -> dict | None:
    """The choice it settled on, if it settled one."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue

        artifact = message.artifact

        if not isinstance(artifact, dict):
            continue

        if (
            artifact.get("operation") == "resolve_spreadsheet_choice"
            and artifact.get("ok") is True
        ):
            return artifact

    return None


def build(model):
    """The file manager, as a graph node."""
    agent = create_agent(
        model=model,
        tools=list(TOOLS),
        system_prompt=FILE_MANAGER_PROMPT,
        state_schema=WorkerState,
    )

    def choose(state: State) -> dict:
        said, result = run_worker(NAME, agent, state)

        written = {"worker_results": reported(NAME, said, state)}

        selected = (
            selected_spreadsheet(result["messages"])
            if result is not None
            else None
        )

        if selected is not None:
            written["spreadsheet_id"] = selected["spreadsheet_id"]
            written["spreadsheet_name"] = selected["spreadsheet_name"]

        return written

    return choose
