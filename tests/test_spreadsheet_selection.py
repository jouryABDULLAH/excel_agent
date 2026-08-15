"""Tests for what choosing a spreadsheet does now, and what it stopped doing.

use_spreadsheet was changed to update the orchestrator's state instead of
returning a sentence. The state half works. The half that was dropped on the
way is what these are mostly about: four other things were riding on this one
tool, and nothing else does them now.

Written against the plain function rather than through an agent, so a failure
here names the tool rather than the model that called it.
"""

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from excel_agent import config
from excel_agent.tools import spreadsheets
from excel_agent.tools.spreadsheets import use_spreadsheet


class Runtime:
    """The part of a ToolRuntime this tool reads."""

    tool_call_id = "call-1"
    state: dict = {}


def choosing(name: str, resolves=("an-id", "TEST - Sales Orders"), monkeypatch=None):
    """Call the tool as its caller does, with a runtime supplied."""
    return use_spreadsheet.func(name, Runtime())


@pytest.fixture(autouse=True)
def a_resolvable_name(monkeypatch):
    """Drive answers with one file, unless a test says otherwise."""
    monkeypatch.setattr(
        spreadsheets, "resolve_spreadsheet", lambda name: ("an-id", "TEST - Sales Orders")
    )


# What it does now


def test_choosing_a_spreadsheet_updates_the_state():
    answer = choosing("sales orders")

    assert isinstance(answer, Command)
    assert answer.update["spreadsheet_id"] == "an-id"
    assert answer.update["spreadsheet_name"] == "TEST - Sales Orders"


def test_the_state_carries_the_real_title_not_what_was_typed():
    answer = choosing("sales orders")

    # What reaches the subagents has to be the name Drive really holds, since
    # they pass it back to tools that resolve it again.
    assert answer.update["spreadsheet_name"] == "TEST - Sales Orders"


def test_the_model_is_told_the_choice_landed():
    answer = choosing("sales orders")

    said = answer.update["messages"][0]
    assert isinstance(said, ToolMessage)
    assert said.tool_call_id == "call-1"
    assert "TEST - Sales Orders" in said.content


def test_the_answer_is_tied_to_the_call_that_asked():
    """A ToolMessage without the id of its call is not an answer to anything.

    LangGraph matches them up by id, and a model handed a tool result it did
    not ask for is a turn that ends in an error rather than an answer.
    """
    answer = choosing("sales orders")

    assert answer.update["messages"][0].tool_call_id == Runtime.tool_call_id


# What it stopped doing


def test_it_no_longer_records_the_spreadsheet_being_worked_on(monkeypatch):
    """REGRESSION: config.SPREADSHEET is what every other tool falls back to.

    sheets.resolve_spreadsheet(None) reads it when a tool is called without a
    spreadsheet argument, which is how a whole conversation used to work on
    one file after being told which once. Nothing sets it now.
    """
    monkeypatch.setattr(config, "SPREADSHEET", None)

    choosing("sales orders")

    assert config.SPREADSHEET is None


def test_a_tool_called_afterwards_still_does_not_know_which_file_to_use(monkeypatch):
    """The consequence of the line above, followed through to where it lands."""
    from excel_agent import sheets

    monkeypatch.setattr(config, "SPREADSHEET", None)
    choosing("sales orders")

    with pytest.raises(ValueError, match="No spreadsheet has been chosen yet"):
        sheets.resolve_spreadsheet()


def test_a_name_that_reaches_nothing_now_raises_instead_of_explaining(monkeypatch):
    """REGRESSION: every other tool answers a bad argument with a sentence.

    The ValueError resolve_spreadsheet raises carries a message written for
    the model to act on. Uncaught, it leaves this tool as the only one that
    throws, and what the model is shown depends on whoever catches it.
    """
    def refuse(name):
        raise ValueError('There is no spreadsheet called "Nonsense".')

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", refuse)

    with pytest.raises(ValueError):
        choosing("Nonsense")


def test_a_google_failure_is_no_longer_turned_into_a_sentence_either(monkeypatch):
    from fake_google import error

    def fail(name):
        raise error(403, "Insufficient permission")

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", fail)

    with pytest.raises(Exception):
        choosing("sales orders")


def test_the_sheets_inside_are_no_longer_named(monkeypatch):
    """REGRESSION: told only the file name, an agent invents a sheet called after it.

    The old answer listed them and said which one a call naming none would
    work on. Nothing says it now, and no other tool does either.
    """
    said = choosing("sales orders").update["messages"][0].content

    assert "sheet(s)" not in said
    assert "Sales Orders" in said


def test_nothing_is_refused_before_drive_is_asked(monkeypatch):
    """A blank name used to be answered without a round trip.

    Now it goes to resolve_spreadsheet, which raises for it instead. The
    message differs, and the tool raises where it used to answer.
    """
    asked: list[str] = []
    monkeypatch.setattr(
        spreadsheets,
        "resolve_spreadsheet",
        lambda name: asked.append(name) or ("an-id", "Something"),
    )

    choosing("   ")

    assert asked == ["   "]


# The shape of the tool the model is shown


def test_the_argument_the_model_fills_in_is_the_spreadsheet_name():
    assert list(use_spreadsheet.args) == ["spreadsheet"]


def test_the_runtime_is_not_something_the_model_has_to_supply():
    schema = use_spreadsheet.tool_call_schema.model_json_schema()

    assert list(schema.get("properties", {})) == ["spreadsheet"]


def test_the_docstring_still_describes_an_argument_that_is_gone():
    """The description is what the model reads to decide what to send.

    It documents "name", and the argument is called "spreadsheet". A model
    following the description writes the wrong key.
    """
    assert "name:" in use_spreadsheet.description
    assert "spreadsheet" in use_spreadsheet.args
