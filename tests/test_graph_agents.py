"""Tests for the worker boundary.

Every specialist is handed a task and hands back one line. What it did to get
there stays inside it: its own messages, tool calls and tool results never
reach the graph.
"""

import fake_sheets
import pytest
from langchain_core.messages import AIMessage
from scripted import ScriptedModel, calling

from excel_agent.agents import analyst, file_manager
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


def working(script, **state) -> dict:
    """Run the analyst once on a scripted conversation."""
    node = analyst.build(ScriptedModel(script=script))

    return node(
        {
            "task": "count the rows",
            "spreadsheet_name": "TEST - Sales Orders",
            "worker_results": [],
            **state,
        }
    )


READS = [
    calling("inspect_sheet", "1", max_rows=5),
    AIMessage("There are 5 rows."),
]


def test_a_worker_reports_what_it_found(a_sheet):
    written = working(READS)

    assert written["worker_results"] == ["[analyst] There are 5 rows."]


def test_a_worker_adds_to_the_work_already_done(a_sheet):
    written = working(READS, worker_results=["[file_manager] Selected it."])

    # The supervisor composes its answer from all of them, so an earlier
    # report must survive a later one.
    assert written["worker_results"] == [
        "[file_manager] Selected it.",
        "[analyst] There are 5 rows.",
    ]


def test_a_worker_writes_nothing_the_supervisor_owns(a_sheet):
    written = working(READS)

    # route, task and final_answer are the supervisor's. A worker that set one
    # would be deciding where to go next, or answering the user itself.
    # messages is here because the report answers the supervisor's delegate
    # call in the thread.
    assert set(written) == {"worker_results", "messages"}


def test_a_worker_that_falls_over_does_not_take_the_turn_with_it(a_sheet):
    written = working([], worker_results=["[file_manager] Selected it."])

    reported = written["worker_results"][-1]
    assert reported.startswith("[analyst] could not finish")

    # And what was already done is still there.
    assert written["worker_results"][0] == "[file_manager] Selected it."


TABLE_IN_PROSE = AIMessage(
    "Here are the rows:\n"
    "| Order ID | Region |\n"
    "|---|---|\n"
    "| ORD-1 | West |\n"
    "That is all of them."
)


def test_a_drawn_table_is_cut_from_the_report(a_sheet):
    """The application draws the read; the same table in prose shows the data
    twice. The prompt forbids it and the model does it anyway."""
    written = working(
        [calling("inspect_sheet", "1", max_rows=5, render_data=True),
         TABLE_IN_PROSE]
    )

    reported = written["worker_results"][-1]

    assert "Here are the rows:" in reported
    assert "That is all of them." in reported
    assert "|" not in reported


def test_a_report_that_was_only_a_table_still_says_something(a_sheet):
    written = working(
        [calling("inspect_sheet", "1", max_rows=5, render_data=True),
         AIMessage("| Order ID | Region |\n|---|---|\n| ORD-1 | West |")]
    )

    reported = written["worker_results"][-1]

    # Stripped to nothing, the supervisor would compose an answer from an
    # empty report.
    assert reported == "[analyst] The requested rows are shown in the table."


def test_a_table_the_model_wrote_itself_is_kept(a_sheet):
    # Nothing is being drawn, so the prose table is the only copy.
    written = working(
        [calling("inspect_sheet", "1", max_rows=5), TABLE_IN_PROSE]
    )

    assert "| ORD-1 | West |" in written["worker_results"][-1]


class Refusing(ScriptedModel):
    """A model that fails the way the provider fails."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError(
            "Error code: 400 - {'error': {'code': 'tool_use_failed', "
            "'failed_generation': '{\"name\": \"inspect_sheet\"}'}}"
        )


def test_a_failure_does_not_repeat_the_provider_back_at_the_model(a_sheet):
    node = analyst.build(Refusing(script=[]))

    written = node(
        {
            "task": "count the rows",
            "spreadsheet_name": "TEST - Sales Orders",
            "worker_results": [],
        }
    )

    reported = written["worker_results"][-1]

    # The provider hands back the model's own bad output inside its error. Fed
    # to the supervisor it is noise, and the model may copy it.
    assert "Error code: 400" in reported
    assert "failed_generation" not in reported


# The file manager, which is the one that changes what everything else works on


@pytest.fixture
def a_drive(monkeypatch):
    """Drive answers with one file, whatever it is asked for."""
    from excel_agent.tools import spreadsheets

    monkeypatch.setattr(
        spreadsheets,
        "resolve_spreadsheet",
        lambda name=None: ("bk-id", "TEST - Book Collection"),
    )


CHOOSES = [
    calling("resolve_spreadsheet_choice", "1", spreadsheet="TEST - Book Collection"),
    AIMessage('Selected "TEST - Book Collection".'),
]


def choosing(script, **state) -> dict:
    node = file_manager.build(ScriptedModel(script=script))

    return node(
        {
            "task": "work on the books file",
            "spreadsheet_name": None,
            "worker_results": [],
            **state,
        }
    )


def test_the_file_manager_writes_the_choice_it_settled(a_drive):
    written = choosing(CHOOSES)

    assert written["spreadsheet_id"] == "bk-id"
    assert written["spreadsheet_name"] == "TEST - Book Collection"


def test_the_file_manager_reports_back_like_any_other_worker(a_drive):
    written = choosing(CHOOSES)

    assert written["worker_results"] == [
        '[file_manager] Selected "TEST - Book Collection".'
    ]


def test_settling_nothing_leaves_the_file_in_hand_alone(a_drive):
    written = choosing(
        [AIMessage("I could not find it.")],
        spreadsheet_name="TEST - Sales Orders",
    )

    # Writing None here would drop the file the user was already working on.
    assert "spreadsheet_name" not in written
    assert "spreadsheet_id" not in written


def test_a_file_manager_that_falls_over_changes_no_spreadsheet(a_drive):
    written = choosing([], spreadsheet_name="TEST - Sales Orders")

    assert "spreadsheet_name" not in written
    assert written["worker_results"][-1].startswith("[file_manager] could not finish")
