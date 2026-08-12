"""subagents registry.

Four subagents, whichever backend is in use. What changes between backends is
what each one holds and what it is told, and both of those are looked up
rather than named here: the tools by the names they answer to, the prompts and
descriptions from the module for that backend.

inspect_sheet belongs to several of them on purpose: an agent that writes
without reading first is guessing, and a row number handed between two agents
is stale before it arrives.
"""

from dataclasses import dataclass

from excel_agent.config import BACKEND
from excel_agent.subagents.prompts import prompts_for
from excel_agent.tools import select_tools


@dataclass(frozen=True)
class SubagentSpec:
    """One subagent: what it is called, what it is for, and what it holds."""

    name: str
    description: str
    system_prompt: str
    tools: tuple


def subagents_for(backend: str) -> tuple[SubagentSpec, ...]:
    """The four subagents, holding one backend's tools and told its rules.

    The tools are picked out by name, which works for either backend because
    the Google tools answer to the same names as the local ones. A backend
    that grows a tool no subagent holds would leave the multi agent variant
    unable to do something the single agent can, which is what the test on
    that coverage is for.
    """
    tools = {tool.name: tool for tool in select_tools(backend)}
    prompts = prompts_for(backend)

    # Held by whoever might change something, and by the analyst as its whole
    # reason for being.
    reading = tools["inspect_sheet"]

    # Styling is structural work, so it goes to the subagent that already
    # changes the shape of the sheet rather than to a fifth one.
    structural = [tools["modify_column"]]
    if "modify_style" in tools:
        structural.append(tools["modify_style"])

    return (
        SubagentSpec(
            "analyst",
            prompts.ANALYST_DESCRIPTION,
            prompts.ANALYST_PROMPT,
            (reading, tools["sheet_stats"]),
        ),
        SubagentSpec(
            "row_editor",
            prompts.ROW_EDITOR_DESCRIPTION,
            prompts.ROW_EDITOR_PROMPT,
            (reading, tools["modify_sheet"]),
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


# The four the orchestrator is built from. Which backend's they are comes from
# config, so nothing downstream has to know there is more than one.
SUBAGENTS = subagents_for(BACKEND)