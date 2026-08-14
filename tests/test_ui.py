"""Tests for the parts of the browser front end that are not Streamlit.

Drawing is left to a run by hand. What is tested here is that the page draws at
all, and that a turn survives everything that can happen to one: a tool call, a
move to another spreadsheet part way through, silence, and a failure.
"""

import pathlib

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel, calling
from streamlit.testing.v1 import AppTest

from excel_agent import config
from excel_agent.runner import Session
from excel_agent.tools import TOOLS

import excel_agent.ui

# AppTest resolves a relative path against this file, so the page is found
# through the module rather than by guessing at the layout of the repo.
PAGE = str(pathlib.Path(excel_agent.ui.__file__))

SPREADSHEET = "TEST - Sales Orders"
OTHER = "TEST - Raw Contacts"

# The page holds what it reads from Drive for a minute, and that cache outlives
# a test. Every test here is therefore given the same two spreadsheets, so
# which one ran first cannot change what a later one sees.
FILES = (("one", SPREADSHEET), ("two", OTHER))


@pytest.fixture(autouse=True)
def a_spreadsheet_in_use(monkeypatch, a_drive):
    """Every test here draws the page, and the page reads Drive to draw it."""
    monkeypatch.setattr(config, "SPREADSHEET", SPREADSHEET)
    a_drive(files=FILES)


# The page itself


def test_the_page_draws_without_falling_over():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    # No turn is asked for, so this builds the agent and draws the page and
    # nothing else. It is the check that would catch a page that raises the
    # moment anyone opens it.
    assert [error.value for error in page.exception] == []
    assert [title.value for title in page.title] == ["Sheets agent"]
    assert page.chat_input


def test_the_sidebar_offers_the_spreadsheets_and_the_two_variants():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    offered = {one.label: list(one.options) for one in page.sidebar.selectbox}
    assert offered["Working on"] == [SPREADSHEET, OTHER]

    variants = {radio.label: list(radio.options) for radio in page.sidebar.radio}
    assert variants["Agents"] == ["single", "multi"]
    # The spreadsheet in use is named on the page, so it is never a guess where
    # a change would land.
    assert any(SPREADSHEET in caption.value for caption in page.caption)


def test_a_spreadsheet_cannot_be_uploaded():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    # A spreadsheet is added in Drive, by Google, and nothing here has the
    # scope to do it. The page says so rather than offering a picker that
    # could not work.
    assert any("come from your Drive" in caption.value for caption in page.sidebar.caption)


def page_with_a_scripted_agent(says: str, script=None, tools=()) -> AppTest:
    """The page with an agent already in place, so no Groq request is made.

    Given a script it runs that instead, which is how a turn that calls a tool
    is put through the page: the plain form holds no tools and so can only
    ever answer straight away.
    """
    page = AppTest.from_file(PAGE, default_timeout=60)
    page.session_state["variant"] = "single"
    page.session_state["transcript"] = []
    page.session_state["session"] = Session(
        create_agent(
            ScriptedModel(script=list(script) if script else [AIMessage(says)]),
            list(tools),
            system_prompt="terse",
            checkpointer=InMemorySaver(),
        )
    )
    return page


def test_a_turn_that_calls_a_tool_is_drawn_whole(a_spreadsheet):
    a_spreadsheet()
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage("Set row 2 to 99."),
        ],
        tools=TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # The answer reaches the page, and the call behind it is counted. Every
    # scripted page before this one held no tools, so a turn that used one had
    # never been drawn at all.
    said = page.session_state["transcript"][-1]
    assert said["text"] == "Set row 2 to 99."
    assert said["calls"] == ["modify_row(action='edit', row=2, values={'Units': 99})"]
    assert any("Set row 2 to 99." in one.value for one in page.markdown)


def test_a_turn_that_moves_to_another_spreadsheet_is_still_drawn(
    monkeypatch, a_spreadsheet
):
    # The move has to happen while the turn is running, which is the whole
    # point, so the real tool is what does it.
    monkeypatch.setattr(config, "SPREADSHEET", OTHER)
    a_spreadsheet()

    # Written out rather than through calling(), whose own first argument is
    # called name and would take this one.
    moving = AIMessage(
        content="",
        tool_calls=[
            {"name": "use_spreadsheet", "args": {"name": "sales orders"}, "id": "1"}
        ],
    )

    page = page_with_a_scripted_agent(
        "", script=[moving, AIMessage("Moved along.")], tools=TOOLS
    ).run()

    page.chat_input[0].set_value("work on the other spreadsheet").run()

    assert config.SPREADSHEET == SPREADSHEET
    # Redrawing must not throw the turn away: what was asked and what came
    # back are both in the transcript, and both are on the page.
    assert [said["text"] for said in page.session_state["transcript"]] == [
        "work on the other spreadsheet",
        "Moved along.",
    ]
    assert any("Moved along." in one.value for one in page.markdown)


def test_a_turn_that_says_nothing_says_that(a_spreadsheet):
    a_spreadsheet()
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage(""),
        ],
        tools=TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # An empty bubble reads as the page having lost the answer. It must not
    # claim nothing happened either: the call above it did edit the sheet.
    said = page.session_state["transcript"][-1]
    assert "ended without anything being said" in said["text"]
    assert said["calls"] == ["modify_row(action='edit', row=2, values={'Units': 99})"]


def test_a_turn_that_falls_over_stays_on_the_page(monkeypatch):
    page = page_with_a_scripted_agent("never reached").run()

    def fall_over(question):
        raise RuntimeError("Groq said no")

    monkeypatch.setattr(page.session_state["session"], "ask", fall_over)

    page.chat_input[0].set_value("do something").run()

    # Dropped, it would leave the question with nothing under it the next time
    # anything redrew, which reads as an answer going missing.
    assert [said["text"] for said in page.session_state["transcript"]] == [
        "do something",
        "That went wrong: Groq said no",
    ]


def test_clicking_a_suggestion_asks_it():
    page = page_with_a_scripted_agent("Five rows.").run()
    asked = page.button[0].label

    page.button[0].click().run()

    assert [said["text"] for said in page.session_state["transcript"]] == [
        asked,
        "Five rows.",
    ]


def test_the_suggestions_are_taken_off_the_page_once_one_is_used():
    page = page_with_a_scripted_agent("Five rows.").run()

    page.button[0].click().run()

    # Left on screen they would belong to a run that has ended, and clicking
    # one again would do nothing at all.
    assert [button.label for button in page.button] == ["New conversation"]
