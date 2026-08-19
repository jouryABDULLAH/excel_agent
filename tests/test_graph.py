"""Tests for the shape of a turn.

The model is a script, so what is checked is where control goes and what the
state looks like when it stops, not whether the routing was sensible.
"""

import fake_sheets
import pytest
from langchain_core.messages import AIMessage
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph, route_worker
from excel_agent.runner import Artifact, Session, ToolCall
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
    calling("delegate", "1", next="analyst", task="count the rows"),
    calling("inspect_sheet", "2", max_rows=5),
    AIMessage("There are 5 rows."),
    AIMessage("The sheet has 5 rows."),
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
        [AIMessage("Nothing to look up.")]
    )

    assert ended["final_answer"] == "Nothing to look up."
    assert ended["worker_results"] == []


def test_the_spreadsheet_in_hand_survives_the_turn(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    # Workers write only worker_results, so what the file manager settled has
    # to still be there afterwards.
    assert ended["spreadsheet_name"] == "TEST - Sales Orders"


def test_delegating_is_never_shown_to_the_user_as_work(a_sheet):
    """The delegate call is how the supervisor speaks, not work on a sheet.

    Only runner.DECISION_NAMES keeps it out of the action list, and it holds
    the tool's name as a string: rename one without the other and the user is
    told about a delegation on every turn.
    """
    session = Session(build_graph(ScriptedModel(script=DELEGATES_THEN_ANSWERS)))
    session.use("TEST - Sales Orders")

    work = [
        one.name
        for one in session.ask("how many rows?")
        if isinstance(one, ToolCall)
    ]

    assert work == ["inspect_sheet"]


def a_read(render_data: bool) -> list:
    """One turn whose analyst reads the sheet, asked to draw it or not."""
    session = Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="show 5 rows"),
                    calling("inspect_sheet", "a", max_rows=5, render_data=render_data),
                    AIMessage("Here are the rows."),
                    AIMessage("Here are the first 5 rows."),
                ]
            )
        )
    )
    session.use("TEST - Sales Orders")

    return [
        one for one in session.ask("show me 5 rows") if isinstance(one, Artifact)
    ]


def test_rows_the_user_asked_to_see_are_drawn(a_sheet):
    """REGRESSION: nothing was drawn at all on this path.

    The runner used to unwrap artifacts from the old delegate tool's own
    artifact. A worker's tool reports its artifact directly, so every table
    was silently dropped -- and no test noticed, because they all drove the
    orchestrator.
    """
    drawn = a_read(render_data=True)

    assert [one.data["operation"] for one in drawn] == ["inspect_sheet"]
    assert len(drawn[0].data["rows"]) == 5


def test_a_read_taken_only_to_answer_is_not_drawn(a_sheet):
    # Counting rows to answer "how many?" should not dump the table underneath.
    assert a_read(render_data=False) == []


def test_a_turn_with_no_route_is_a_broken_supervisor_not_a_finished_turn():
    with pytest.raises(ValueError, match="no route"):
        route_worker({"route": None})

# Who exists, and what they can reach


def test_every_worker_the_supervisor_can_name_has_a_node():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.graph.state import WORKERS

    # Delegate.next is Literal[WORKERS], so a name here without a node is a
    # KeyError the moment the supervisor picks it.
    assert {one.NAME for one in SPECIALISTS} == set(WORKERS)


def test_every_tool_reaches_some_worker():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.tools import TOOLS

    held = {tool.name for one in SPECIALISTS for tool in one.TOOLS}
    offered = {tool.name for tool in TOOLS}

    # use_spreadsheet is wired to nobody: the file manager settles a choice
    # through resolve_spreadsheet_choice instead.
    assert offered - held == {"use_spreadsheet"}


def test_no_worker_holds_a_tool_that_is_not_offered():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.tools import TOOLS

    held = {tool.name for one in SPECIALISTS for tool in one.TOOLS}

    assert held <= {tool.name for tool in TOOLS}


def test_everyone_who_writes_can_read_first():
    from excel_agent.agents import SPECIALISTS

    writes = {
        "update_row", "insert_row", "append_row", "delete_row", "move_row",
        "insert_column", "delete_column", "move_column", "set_column_formula",
        "create_chart", "delete_chart",
    }

    for one in SPECIALISTS:
        names = {tool.name for tool in one.TOOLS}

        if names & writes:
            # A row number from another agent is stale before it arrives, so
            # whoever writes has to be able to look first.
            assert "inspect_sheet" in names, one.NAME
