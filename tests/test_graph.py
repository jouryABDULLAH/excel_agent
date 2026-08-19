"""Tests for the shape of a turn.

The model is a script, so what is checked is where control goes and what the
state looks like when it stops, not whether the routing was sensible.
"""

import fake_sheets
import pytest
from langchain_core.messages import AIMessage
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph, route_worker
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import inspect as inspect_tool


@pytest.fixture
def a_sheet(monkeypatch):
    """A sheet the reading tools can be pointed at, without Google."""
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


def a_turn(script, **state) -> dict:
    """Put one question through the graph."""
    graph = build_graph(ScriptedModel(script=script))

    return graph.invoke(
        {
            "messages": [{"role": "user", "content": "how many rows?"}],
            "spreadsheet_name": "TEST - Sales Orders",
            **state,
        },
        config={"configurable": {"thread_id": "one"}},
    )


DELEGATES_THEN_ANSWERS = [
    calling("Delegate", "1", next="analyst", task="count the rows"),
    calling("inspect_sheet", "2", max_rows=5),
    AIMessage("There are 5 rows."),
    calling("Finish", "3", final_answer="The sheet has 5 rows."),
]


def test_a_turn_goes_supervisor_worker_supervisor_and_stops(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    assert ended["final_answer"] == "The sheet has 5 rows."
    assert ended["route"] == "end"


def test_the_work_is_forgotten_once_the_answer_is_written(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    # The analyst did report back, and the supervisor cleared it on finishing.
    assert ended["worker_results"] == []


def test_a_supervisor_that_answers_at_once_never_reaches_a_worker(a_sheet):
    ended = a_turn(
        [calling("Finish", "1", final_answer="Nothing to look up.")]
    )

    assert ended["final_answer"] == "Nothing to look up."
    assert ended["worker_results"] == []


def test_the_spreadsheet_in_hand_survives_the_turn(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    # Workers write only worker_results, so what the file manager settled has
    # to still be there afterwards.
    assert ended["spreadsheet_name"] == "TEST - Sales Orders"


def test_a_turn_with_no_route_is_a_broken_supervisor_not_a_finished_turn():
    with pytest.raises(ValueError, match="no route"):
        route_worker({"route": None})