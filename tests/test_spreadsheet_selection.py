"""Tests for choosing a spreadsheet.

The name the user says is rarely the name Drive holds: "books" does not reach
"TEST - Book Collection", because Drive matches a prefix rather than a part of
a word. Nothing here tries to close that gap by matching strings harder. The
tool answers a miss with the names that do exist, and the orchestrator picks
the one that was meant. So what these check is that a miss comes back as an
answer carrying those names, rather than as an exception nobody can act on.

Written against the plain function rather than through an agent, so a failure
here names the tool rather than the model that called it.
"""

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from excel_agent.tools import spreadsheets
from excel_agent.tools.spreadsheets import use_spreadsheet


class Runtime:
    """The part of a ToolRuntime this tool reads."""

    tool_call_id = "call-1"
    state: dict = {}


def choosing(name: str):
    """Call the tool as its caller does, with a runtime supplied."""
    return use_spreadsheet.func(name, Runtime())


def said(answer: Command) -> str:
    """What the tool told the orchestrator."""
    return answer.update["messages"][0].content


@pytest.fixture(autouse=True)
def a_resolvable_name(monkeypatch):
    """Drive answers with one file, unless a test says otherwise."""
    monkeypatch.setattr(
        spreadsheets, "resolve_spreadsheet", lambda name: ("an-id", "TEST - Sales Orders")
    )




# Settling on a file


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

    assert isinstance(answer.update["messages"][0], ToolMessage)
    assert "TEST - Sales Orders" in said(answer)


def test_the_answer_is_tied_to_the_call_that_asked():
    """A ToolMessage without the id of its call is not an answer to anything.

    LangGraph matches them up by id, and a model handed a tool result it did
    not ask for is a turn that ends in an error rather than an answer.
    """
    answer = choosing("sales orders")

    assert answer.update["messages"][0].tool_call_id == Runtime.tool_call_id


def test_the_choice_is_recorded_in_the_state_and_nowhere_else():
    """There is one record of which spreadsheet is in hand, and this is it.

    It used to be written to a module global as well, which every browser
    session in the process shared. Where a tool reads it from now is the
    worker state seeded from this update, covered in test_graph.
    """
    answer = choosing("sales orders")

    assert answer.update["spreadsheet_name"] == "TEST - Sales Orders"


def test_a_tool_with_no_spreadsheet_anywhere_refuses_rather_than_guessing():
    """Nothing chosen used to mean a process-wide global; now it means nothing.

    Refusing is the only safe answer: silently picking up a file some other
    conversation had chosen is how a write lands in the wrong spreadsheet.
    """
    from excel_agent import sheets

    with pytest.raises(ValueError) as refusal:
        sheets.resolve_spreadsheet()

    assert "No spreadsheet has been chosen yet" in str(refusal.value)


# A name that reaches no single file


def refusing(monkeypatch, *titles: str):
    """Drive holds these files, and resolving any name fails."""
    def refuse(name):
        raise ValueError('There is no spreadsheet called "Nonsense".')

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", refuse)
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [(f"id-{one}", one) for one in titles]
    )


def test_a_name_that_reaches_nothing_is_answered_with_the_names_that_exist(monkeypatch):
    """The whole point: a miss has to be recoverable without the user.

    Raising left the model with an error and no facts. The names come back
    instead, so the next call can be the right one.
    """
    refusing(monkeypatch, "TEST - Book Collection", "TEST - Sales Orders")

    answer = choosing("books")

    assert isinstance(answer, Command)
    assert "TEST - Book Collection" in said(answer)
    assert "TEST - Sales Orders" in said(answer)


def test_a_miss_settles_nothing(monkeypatch):
    """Nothing is chosen, so nothing downstream may think one was."""
    refusing(monkeypatch, "TEST - Book Collection")

    answer = choosing("books")

    assert "spreadsheet_id" not in answer.update
    assert "spreadsheet_name" not in answer.update


def test_the_miss_says_to_choose_and_call_again(monkeypatch):
    refusing(monkeypatch, "TEST - Book Collection")

    assert "use_spreadsheet" in said(choosing("books"))


def test_the_dead_ends_drive_offers_are_not_passed_on(monkeypatch):
    """resolve_spreadsheet's own wording sends the reader nowhere useful.

    It says to call list_workbooks, which is what this call already answers,
    or to name a file by its id, which no tool accepts. Neither reaches the
    user in a state they can act on, so the message is written here instead.
    """
    refusing(monkeypatch, "TEST - Book Collection")

    answer = said(choosing("books"))

    assert "list_workbooks" not in answer
    assert "by its ID" not in answer


def test_two_files_sharing_a_name_come_back_the_same_way(monkeypatch):
    """The ambiguous refusal is a ValueError too, and needs the same answer."""
    def refuse(name):
        raise ValueError('More than one spreadsheet is called "Budget".')

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", refuse)
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("one", "Budget"), ("two", "Budget")]
    )

    answer = choosing("Budget")

    assert "did not match exactly one spreadsheet" in said(answer)


def test_a_google_failure_is_turned_into_a_sentence(monkeypatch):
    """Every other tool answers an HttpError with readable(). So does this."""
    from fake_google import error

    def fail(name):
        raise error(403, "Insufficient permission")

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", fail)

    answer = choosing("sales orders")

    assert isinstance(answer, Command)
    assert "Google refused the request" in said(answer)


def test_a_google_failure_while_listing_is_answered_too(monkeypatch):
    """The recovery reaches Drive a second time, and that call can fail as well."""
    from fake_google import error

    def refuse(name):
        raise ValueError("nothing matched")

    def fail(name=None):
        raise error(401)

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", refuse)
    monkeypatch.setattr(spreadsheets, "search", fail)

    assert "token.json" in said(choosing("books"))


# A name that is not a name


@pytest.mark.parametrize("nothing", ("", "   "))
def test_a_blank_name_is_refused_before_drive_is_asked(nothing, monkeypatch):
    """A round trip bought for nothing, and an error message worse than this one."""
    asked: list[str] = []
    monkeypatch.setattr(
        spreadsheets,
        "resolve_spreadsheet",
        lambda name: asked.append(name) or ("an-id", "Something"),
    )

    answer = choosing(nothing)

    assert asked == []
    assert "No spreadsheet was named" in said(answer)


# The shape of the tool the model is shown


def test_the_argument_the_model_fills_in_is_the_spreadsheet_name():
    assert list(use_spreadsheet.args) == ["spreadsheet"]


def test_the_runtime_is_not_something_the_model_has_to_supply():
    schema = use_spreadsheet.tool_call_schema.model_json_schema()

    assert list(schema.get("properties", {})) == ["spreadsheet"]


def test_the_description_documents_the_argument_that_exists():
    """The description is what the model reads to decide what to send.

    It documented "name" while the argument was called "spreadsheet", so a
    model following it wrote the wrong key.
    """
    assert "spreadsheet:" in use_spreadsheet.description
    assert "spreadsheet" in use_spreadsheet.args


def test_the_description_says_an_exact_name_is_not_required():
    """A model that thinks it needs the exact name asks the user for it."""
    assert "does not have to be exact" in use_spreadsheet.description
