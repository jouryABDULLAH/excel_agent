"""Creates and maintains charts."""

from langchain.agents import create_agent

from excel_agent.subagents.prompts import CHART_PROMPT
from excel_agent.tools import (
    create_chart,
    delete_chart,
    inspect_sheet,
    update_chart,
)


def build_chart_maker(model):
    return create_agent(
        model=model,
        tools=[inspect_sheet, create_chart, update_chart, delete_chart],
        system_prompt=CHART_PROMPT,
    )
