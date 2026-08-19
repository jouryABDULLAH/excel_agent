"""The same question through both architectures.

The migration is only safe while the two agree. This puts one question through
the old orchestrator and the new graph and checks they answer the same, and do
the same work getting there.
"""

import fake_sheets
import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph
from excel_agent.runner import Answer, Session, ToolCall
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.subagents import SUBAGENTS
from excel_agent.subagents.factory import OrchestratorState, as_tool
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.tools import inspect as inspect_tool


ANSWER = "The sheet has 5 rows."


@pytest.fixture
def a_sheet(monkeypatch):
    """A sheet both architectures can read, without Google."""
    monkeypatch.setattr(
        inspect_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or "TEST - Sales Orders"),
    )
    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {"title": "Sales Orders", "sheetId": 0},
    )
    monkeypatch.setattr(
        spreadsheet_service, "read_sheet", lambda id, name: fake_sheets.orders()
    )
    monkeypatch.setattr(
        spreadsheet_service, "list_charts", lambda id, name=None: []
    )


def through(session) -> tuple[str, list[str]]:
    """One question, and what came back: the answer and the work."""
    events = list(session.ask("how many rows?"))

    answer = next(
        one.text for one in events if isinstance(one, Answer)
    )
    work = [
        one.name for one in events if isinstance(one, ToolCall)
    ]

    return answer, work


def the_old_way():
    """The orchestrator, delegating to the analyst through a tool."""
    analyst = next(one for one in SUBAGENTS if one.name == "analyst")

    inside = ScriptedModel(
        script=[
            calling("inspect_sheet", "a", max_rows=5),
            AIMessage("There are 5 rows."),
        ]
    )
    outside = ScriptedModel(
        script=[
            calling("analyst", "1", instruction="count the rows"),
            AIMessage(ANSWER),
        ]
    )

    return Session(
        create_agent(
            outside,
            [as_tool(analyst, inside)],
            system_prompt=ORCHESTRATOR_PROMPT,
            state_schema=OrchestratorState,
            checkpointer=InMemorySaver(),
        )
    )


def the_new_way():
    """The graph, routing to the analyst as a node."""
    return Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="count the rows"),
                    calling("inspect_sheet", "a", max_rows=5),
                    AIMessage("There are 5 rows."),
                    AIMessage(ANSWER),
                ]
            )
        )
    )


def test_both_architectures_answer_the_same(a_sheet):
    old_answer, _ = through(the_old_way())
    new_answer, _ = through(the_new_way())

    assert old_answer == new_answer == ANSWER


def test_both_do_the_same_work_on_the_spreadsheet(a_sheet):
    _, old_work = through(the_old_way())
    _, new_work = through(the_new_way())

    # The delegation itself is not work: the old way calls a tool named after a
    # subagent, the new way enters a node. What touches the sheet is the same.
    assert [one for one in old_work if one != "analyst"] == new_work == [
        "inspect_sheet"
    ]
