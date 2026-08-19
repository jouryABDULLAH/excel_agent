"""Creates, updates and deletes charts."""

from langchain.agents import create_agent

from excel_agent.agents._shared import WorkerState, worker_node
from excel_agent.subagents.prompts import CHART_PROMPT
from excel_agent.tools import (
    create_chart,
    delete_chart,
    inspect_sheet,
    update_chart,
)


NAME = "chart_maker"

TOOLS = (
    inspect_sheet,
    create_chart,
    update_chart,
    delete_chart,
)


def build(model):
    """The chart maker, as a graph node."""
    return worker_node(
        NAME,
        create_agent(
            model=model,
            tools=list(TOOLS),
            system_prompt=CHART_PROMPT,
            state_schema=WorkerState,
        ),
    )
