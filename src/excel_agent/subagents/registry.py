"""subagents registry.

Four subagents. The tools are looked up by the names they answer to rather
than imported one by one, so this reads as a division of labour and not as a
second copy of the tool list.

inspect_sheet belongs to several of them on purpose: an agent that writes
without reading first is guessing, and a row number handed between two agents
is stale before it arrives.
"""

from dataclasses import dataclass

from langchain.tools import BaseTool

from excel_agent.subagents import prompts
from excel_agent.tools import TOOLS


@dataclass(frozen=True)
class SubagentSpec:
    """Configuration for a specialized subagent."""

    name: str
    description: str
    system_prompt: str
    tools: tuple[BaseTool, ...]
    return_tool_results: bool = False

def subagents() -> tuple[SubagentSpec, ...]:
    """Define the subagents and the capabilities each one receives."""

    tools = {tool.name: tool for tool in TOOLS}


    reading = tools["inspect_sheet"]

    searching_tools = (
        tools["list_workbooks"],
        tools["find_spreadsheet"],
        tools["resolve_spreadsheet_choice"],
    )

    reading_tools = (
        reading, 
        tools["sheet_stats"], 
        tools["find_data"],
    )

    row_tools = (
        reading,
        tools["find_data"],
        tools["update_row"],
        tools["insert_row"],
        tools["append_row"],
        tools["delete_row"],
        tools["move_row"],
    )

    structural = (
        tools["insert_column"],
        tools["rename_column"],
        tools["delete_column"],
        tools["move_column"],
        tools["set_column_formula"],
        tools["format_range"],
        tools["copy_format"],
    )

    return (
        SubagentSpec(
            name="file_manager",
            description=prompts.FILE_MANAGER_DESCRIPTION,
            system_prompt=prompts.FILE_MANAGER_PROMPT,
            tools=searching_tools,
        ),
        SubagentSpec(
            name="analyst",
            description=prompts.ANALYST_DESCRIPTION,
            system_prompt=prompts.ANALYST_PROMPT,
            tools=reading_tools,
            return_tool_results=True,
        ),
        SubagentSpec(
            name="row_editor",
            description=prompts.ROW_EDITOR_DESCRIPTION,
            system_prompt=prompts.ROW_EDITOR_PROMPT,
            tools=row_tools,
        ),
        SubagentSpec(
            name="structure_editor",
            description=prompts.STRUCTURE_DESCRIPTION,
            system_prompt=prompts.STRUCTURE_PROMPT,
            tools=(reading, *structural),
        ),
        SubagentSpec(
            "chart_maker",
            prompts.CHART_DESCRIPTION,
            prompts.CHART_PROMPT,
            (
                reading,
                tools["create_chart"],
                tools["update_chart"],
                tools["delete_chart"],
            ),
        ),
    )


SUBAGENTS = subagents()
