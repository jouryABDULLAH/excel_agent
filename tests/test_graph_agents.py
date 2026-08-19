"""Tests for the worker boundary.

Every specialist is handed a task and hands back one line. What it did to get
there stays inside it: its own messages, tool calls and tool results never
reach the graph.
"""

import fake_sheets
import pytest
from langchain_core.messages import AIMessage
from scripted import ScriptedModel, calling

from excel_agent.agents import analyst
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
    assert working(READS) == {
        "worker_results": ["[analyst] There are 5 rows."]
    }


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
    assert set(written) == {"worker_results"}


def test_a_worker_that_falls_over_does_not_take_the_turn_with_it(a_sheet):
    written = working([], worker_results=["[file_manager] Selected it."])

    reported = written["worker_results"][-1]
    assert reported.startswith("[analyst] could not finish")

    # And what was already done is still there.
    assert written["worker_results"][0] == "[file_manager] Selected it."


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
