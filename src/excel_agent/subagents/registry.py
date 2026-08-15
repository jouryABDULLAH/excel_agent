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
    """The four subagents, each holding the tools its work needs.

    Every tool the orchestrator does not hold itself has to reach some subagent,
    or it can never be called at all. That is what the test on coverage is for.
    """
    tools = {tool.name: tool for tool in TOOLS}

    # Held by whoever might change something, and by the analyst as its whole
    # reason for being.
    reading = tools["inspect_sheet"]

    # Finding a row by what is in it is reading, and it gives back a row
    # number: whoever will act on that number should be the one who asked for
    # it, which is why this is not the orchestrator's.
    reading_tools = [reading, tools["sheet_stats"], tools["find_data"]]

    # Styling is structural work, so it goes to the subagent that already
    # changes the shape of the sheet rather than to a fifth one.
    structural = [tools["modify_column"], tools["modify_style"]]

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
            (reading, tools["modify_row"]),
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
