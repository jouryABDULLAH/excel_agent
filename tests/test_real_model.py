"""The paths a scripted model cannot reach.

Run deliberately, because these cost money and are not deterministic:

    pytest -m real

Real model, stubbed Google. Every bug these exist for was in how the model is
asked and how its answer is read, not in how Google is called -- and the
no_google guard has to keep standing so no test can write to a real
spreadsheet.

ScriptedModel answers whatever it was scripted to answer, whether or not the
binding allowed it. Only a real model shows what the model actually does when
asked to delegate or to write a sentence.
"""

import fake_sheets
import pytest

from excel_agent.graph.graph import build_graph
from excel_agent.graph.supervisor import build_supervisor, supervisor_node
from excel_agent.model import build_model
from excel_agent.runner import Answer, Session, ToolCall
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import inspect as inspect_tool

pytestmark = pytest.mark.real


SPREADSHEET = "TEST - Sales Orders"


@pytest.fixture
def a_sheet(monkeypatch):
    """A sheet built by hand, so a real model reads real-shaped data."""
    monkeypatch.setattr(
        inspect_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
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


def asked(**state) -> dict:
    return {
        "messages": [{"role": "user", "content": "how many rows are in the sheet?"}],
        "spreadsheet_name": SPREADSHEET,
        "spreadsheet_id": "an-id",
        "worker_results": [],
        **state,
    }


# The supervisor's two decisions, against the real model


def test_a_question_about_the_sheet_is_delegated_not_answered():
    decided = supervisor_node(build_supervisor(build_model()))(asked())

    # It cannot know the row count without looking, so it must delegate.
    assert decided["route"] == "analyst"
    assert decided["task"].strip()


def test_the_supervisor_finishes_once_the_work_is_reported():
    """REGRESSION: asked for the answer inside a schema, this model emitted

    {"name": "answer", "arguments": There are 5 rows...} -- the wrong tool name
    and unquoted arguments -- and about half of these turns died there. Writing
    the answer as prose is what fixed it, so this is the path that broke.
    """
    decided = supervisor_node(build_supervisor(build_model()))(
        asked(worker_results=["[analyst] The sheet has 5 rows of data."])
    )

    assert decided["route"] == "end"
    assert decided["final_answer"].strip()


# A whole turn


def test_a_read_question_goes_through_the_graph_and_comes_back_answered(a_sheet):
    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    events = list(session.ask("How many rows of data are in the sheet?"))

    answer = next(one.text for one in events if isinstance(one, Answer))
    work = [one.name for one in events if isinstance(one, ToolCall)]

    assert "inspect_sheet" in work
    assert answer.strip()

    # REGRESSION: with only the task and no context, the analyst passed the
    # spreadsheet name as a sheet name and the turn came back as a question.
    assert "5" in answer


def test_the_worker_is_told_which_spreadsheet_and_does_not_confuse_it(a_sheet):
    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    events = list(session.ask("What are the column names?"))

    for call in (one for one in events if isinstance(one, ToolCall)):
        # "TEST - Sales Orders" is a spreadsheet; "Sales Orders" is a sheet.
        assert call.arguments.get("sheet") != SPREADSHEET


def test_one_add_writes_one_row(a_sheet, monkeypatch):
    """REGRESSION: one add-a-row request wrote the row 19 times.

    The supervisor could not see it had already delegated, so the row editor
    was sent out again and again, and each pass wrote the row again. The
    write is stubbed here; what is measured is how many times it happens.
    """
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    written: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "update_cells",
        lambda **sent: written.append(sent) or {"totalUpdatedCells": 4},
    )
    monkeypatch.setattr(
        spreadsheet_service,
        "insert_rows",
        lambda **sent: None,
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    list(
        session.ask(
            "Add a row: Order ID ORD-1100, Region West, Product Desk, Units 3."
        )
    )

    assert len(written) == 1
