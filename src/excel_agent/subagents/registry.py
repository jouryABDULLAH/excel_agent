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


def subagents() -> tuple[SubagentSpec, ...]:
    """The four subagents, each holding the tools its work needs."""

    tools = {tool.name: tool for tool in TOOLS}


    reading = tools["inspect_sheet"]

    reading_tools = [
        reading, 
        tools["sheet_stats"], 
        tools["find_data"]
    ]

    row_tools = (
        reading,
        tools["find_data"],
        tools["update_row"],
        tools["insert_row"],
        tools["append_row"],
        tools["delete_row"],
        tools["move_row"],
    )

    structural = [
        tools["modify_column"], 
        tools["modify_style"]
    ]

    return (
        SubagentSpec(
            "analyst",
            prompts.ANALYST_DESCRIPTION,
            prompts.ANALYST_PROMPT,
            tuple(reading_tools),
        ),
        SubagentSpec(
            "row_editor",
            prompts.ROW_EDITOR_DESCRIPTION,
            prompts.ROW_EDITOR_PROMPT,
            row_tools,
        ),
        SubagentSpec(
            "structure_editor",
            prompts.STRUCTURE_DESCRIPTION,
            prompts.STRUCTURE_PROMPT,
            (reading, *structural),
        ),
        SubagentSpec(
            "chart_maker",
            prompts.CHART_DESCRIPTION,
            prompts.CHART_PROMPT,
            (reading, tools["modify_chart"]),
        ),
    )


# The four the orchestrator is built from.
SUBAGENTS = subagents()
