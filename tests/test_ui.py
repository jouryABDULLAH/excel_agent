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

from excel_agent import browsing, cli, runner
from excel_agent.runner import Session
from excel_agent.graph.state import State
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
    """Every test here draws the page, and the page reads Drive to draw it.

    The spreadsheet a conversation opens on is seeded from the environment by
    Session, which is what stands in here for a user who has already chosen
    one.
    """
    monkeypatch.setattr(runner, "START_SPREADSHEET", SPREADSHEET)
    a_drive(files=FILES)


# The page itself


def test_the_page_draws_without_falling_over():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    # No turn is asked for, so this builds the agent and draws the page and
    # nothing else. It is the check that would catch a page that raises the
    # moment anyone opens it.
    assert [error.value for error in page.exception] == []
    # The title is drawn as markup rather than with st.title, so that it can
    # be sized to sit above the conversation instead of over a dashboard.
    assert any(browsing.TITLE in one.value for one in page.markdown)
    assert page.chat_input


def test_the_sidebar_offers_the_spreadsheets():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    offered = [list(one.options) for one in page.sidebar.selectbox]
    assert offered == [[SPREADSHEET, OTHER]]

    # The spreadsheet in use is named on the page, so it is never a guess
    # where a change would land.
    assert any(SPREADSHEET in one.value for one in page.markdown)


def test_a_spreadsheet_cannot_be_uploaded():
    page = AppTest.from_file(PAGE, default_timeout=60).run()

    # A spreadsheet is added in Drive, by Google, and nothing here has the
    # scope to do it. Offering a picker that could not work would be worse
    # than offering nothing.
    assert page.get("file_uploader") == []


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
            state_schema=State,
            checkpointer=InMemorySaver(),
        )
    )
    return page


def test_a_turn_that_calls_a_tool_is_drawn_whole(a_spreadsheet):
    a_spreadsheet()
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("update_row", "1", row=2, values={"Units": 99}),
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
    assert said["calls"] == ["update_row(row=2, values={'Units': 99})"]
    assert any("Set row 2 to 99." in one.value for one in page.markdown)


def test_a_turn_that_moves_to_another_spreadsheet_is_still_drawn(
    monkeypatch, a_spreadsheet
):
    # The move has to happen while the turn is running, which is the whole
    # point, so the real tool is what does it.
    monkeypatch.setattr(runner, "START_SPREADSHEET", OTHER)
    a_spreadsheet()

    # Written out rather than through calling(), whose own first argument is
    # called name and would take this one.
    moving = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "use_spreadsheet",
                "args": {"spreadsheet": "sales orders"},
                "id": "1",
            }
        ],
    )

    page = page_with_a_scripted_agent(
        "", script=[moving, AIMessage("Moved along.")], tools=TOOLS
    ).run()

    page.chat_input[0].set_value("work on the other spreadsheet").run()

    assert page.session_state["session"].in_use() == SPREADSHEET
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
            calling("update_row", "1", row=2, values={"Units": 99}),
            AIMessage(""),
        ],
        tools=TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # An empty bubble reads as the page having lost the answer. It must not
    # claim nothing happened either: the call above it did edit the sheet.
    assert any(
        "finished without a written response" in one.value
        for one in page.markdown
    )
    assert any("did run" in one.value for one in page.markdown)

    # Drawn, not stored. Stored, it came back on the next rerun sitting above
    # the table of a turn whose answer was the table.
    said = page.session_state["transcript"][-1]
    assert said["text"] == ""
    assert said["calls"] == ["update_row(row=2, values={'Units': 99})"]


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
    assert [button.label.strip("＋ ") for button in page.button] == [
        "New conversation"
    ]


# Starting again


def starting_again(page):
    """Click New conversation, wherever it sits among the buttons."""
    button = next(
        one
        for one in page.button
        if "New conversation" in one.label
    )
    return button.click().run()


def test_a_new_conversation_lets_go_of_the_spreadsheet():
    """REGRESSION: the file outlived the conversation that chose it.

    A new conversation is a new thread, and the spreadsheet now lives in that
    thread's state, so it goes when the thread does. It used to live in a
    module global that outlived both, and the first question of a fresh
    conversation was answered about a file nobody in it had named.
    """
    page = page_with_a_scripted_agent("Five rows.").run()
    assert page.session_state["session"].in_use() == SPREADSHEET

    page = starting_again(page)

    assert page.session_state["session"].in_use() is None


def test_a_new_conversation_empties_the_transcript():
    page = page_with_a_scripted_agent("Five rows.").run()
    page.chat_input[0].set_value("how many rows?").run()
    assert page.session_state["transcript"]

    page = starting_again(page)

    assert page.session_state["transcript"] == []


def test_the_picker_stops_naming_a_spreadsheet_that_was_let_go_of():
    """The picker draws what it holds, whatever index the code passes it.

    Left alone it would go on showing the old file, and choosing it again
    would be a no-op, since the page compares what is picked with what is in
    use and they would already agree.
    """
    page = page_with_a_scripted_agent("Five rows.").run()

    page = starting_again(page)

    # Cleared, then drawn again from an index of None: the picker offers every
    # spreadsheet and names none of them.
    assert page.session_state["workbook_choice"] is None
    # And nothing on the page still names it, which is the half a user sees.
    assert not any(SPREADSHEET in one.value for one in page.markdown)


# What a read hands to whoever draws it


def test_the_rows_a_read_returns_are_drawn_as_a_table(a_spreadsheet, capsys):
    """The artifact a read produces has to be the one the front ends read.

    Both of them looked for the columns under a key inspect_sheet does not
    use, so the table was silently dropped and every read came back as prose
    alone. Built from the real tool rather than by hand, so renaming the key
    on either side fails here.
    """
    from excel_agent.tools import inspect

    a_spreadsheet()

    message = inspect.inspect_sheet.invoke(
        {
            "name": "inspect_sheet",
            "args": {},
            "id": "a-call",
            "type": "tool_call",
        }
    )

    drawn: list = []

    class Box:
        def dataframe(self, table, **named):
            drawn.append(table)

    excel_agent.ui.draw_artifact(message.artifact, Box())

    assert [row["row"] for row in drawn[0]] == [2, 3, 4, 5, 6]
    assert drawn[0][0]["Product"] == "Laptop"

    # The terminal draws the same artifact, and read it under the same key.
    cli.print_artifact(message.artifact)

    printed = capsys.readouterr().out
    assert "| row | Order ID | Region | Units | Product |" in printed
    assert "| 2 | ORD-1001 | North | 1 | Laptop |" in printed


def test_the_actions_behind_a_turn_are_drawn_with_it(a_spreadsheet):
    """REGRESSION: they arrived a rerun late.

    draw_turn collected the tool calls and returned them, but only
    draw_transcript drew them, and that runs on the next rerun. The answer
    appeared and what produced it turned up later, or not until the next
    question was asked.
    """
    a_spreadsheet()
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("update_row", "1", row=2, values={"Units": 99}),
            AIMessage("Set row 2 to 99."),
        ],
        tools=TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # Same run as the answer, not the one after it.
    assert [one.label for one in page.expander] == ["1 action"]

    # And exactly one collapsible bar. The status that reported progress while
    # the turn ran is cleared once it finishes: left behind, it sits above the
    # actions as a second grey bar saying "Done", which reads as the block
    # having been drawn twice.
    assert page.get("status") == []


def test_progress_is_described_by_the_specialist_at_work():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.ui import activity_label

    # Every specialist needs its own line; an unnamed one falls back.
    for one in SPECIALISTS:
        assert activity_label(one.NAME) != "Working on it...", one.NAME

    assert activity_label(None) == "Working on it..."


# What a finished turn keeps, and how it reads the second time


class Recording:
    """A container that remembers what was drawn in it."""

    def __init__(self):
        self.markdown_calls: list[str] = []
        self.tables: list = []

    def markdown(self, text, **named):
        self.markdown_calls.append(text)

    def dataframe(self, table, **named):
        self.tables.append(table)

    def empty(self):
        return self

    def container(self, **named):
        return self

    def status(self, *arguments, **named):
        return self

    def update(self, **named):
        pass

    def expander(self, *arguments, **named):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False


A_TABLE = {
    "operation": "inspect_sheet",
    "headers": ["Title"],
    "rows": [{"row": 2, "values": {"Title": "Dune"}}],
}


def finished(events) -> dict:
    """One turn's worth of events, as draw_turn stores it."""
    return excel_agent.ui.draw_turn(events, Recording())


def test_a_turn_that_only_drew_a_table_is_not_stored_as_no_answer():
    """REGRESSION: NO_ANSWER was stored whenever the answer was empty.

    A turn whose whole point was the table it drew looked right, and the
    next rerun redrew it from the transcript with "finished without a
    written response" sitting above that same table.
    """
    kept = finished([runner.Answer(""), runner.Artifact(A_TABLE)])

    assert kept["text"] == ""
    assert kept["artifacts"] == [A_TABLE]
    assert excel_agent.ui.NO_ANSWER not in kept["text"]


def test_a_turn_that_said_nothing_at_all_still_reads_the_same_on_a_rerun():
    kept = finished([runner.Answer("")])

    # Nothing to say and nothing drawn: the transcript holds no text, so the
    # redraw has to supply the same line the live turn showed.
    assert kept["text"] == ""
    assert kept["artifacts"] == []


def test_the_answer_itself_is_what_gets_stored():
    kept = finished([runner.Answer("There are 51 rows.")])

    assert kept["text"] == "There are 51 rows."


def test_a_turn_that_stopped_to_ask_is_one_turn_again_afterwards():
    """REGRESSION: the half before the question was left as its own turn, so
    deciding replaced the buttons with "finished without a written response"
    and the real answer arrived underneath it."""
    from excel_agent.ui import joined

    paused = {
        "role": "assistant",
        "text": "",
        "calls": ["delete_row(row=3)"],
        "artifacts": [],
        "waiting": [{"tool": "delete_row", "arguments": {"row": 3}, "id": "a"}],
    }

    whole = joined(
        paused,
        {
            "role": "assistant",
            "text": "Deleted row 3.",
            "calls": ["inspect_sheet()"],
            "artifacts": [{"operation": "inspect_sheet"}],
            "waiting": [],
        },
    )

    assert whole["text"] == "Deleted row 3."
    assert whole["calls"] == ["delete_row(row=3)", "inspect_sheet()"]
    assert whole["artifacts"] == [{"operation": "inspect_sheet"}]

    # Nothing is left waiting, and the turn is no longer the empty-looking
    # one that drew NO_ANSWER.
    assert whole["waiting"] == []
    assert whole["text"] or whole["artifacts"]


def test_a_turn_that_stops_twice_keeps_asking():
    from excel_agent.ui import joined

    still = [{"tool": "delete_row", "arguments": {"row": 9}, "id": "b"}]

    whole = joined(
        {
            "role": "assistant",
            "text": "",
            "calls": [],
            "artifacts": [],
            "waiting": [{"tool": "delete_row", "arguments": {"row": 3}, "id": "a"}],
        },
        {
            "role": "assistant",
            "text": "",
            "calls": ["delete_row(row=3)"],
            "artifacts": [],
            "waiting": still,
        },
    )

    assert whole["waiting"] == still


def test_the_permission_card_reads_task_action_rows_spreadsheet():
    from excel_agent.ui import permission_card

    card = permission_card(
        {
            "tool": "delete_row",
            "task": "Remove the 18 duplicate rows",
            "arguments": {
                "rows": [20, 15, 16, 17, 18, 19, 32],
                "spreadsheet": "TEST - Book Collection",
            },
        }
    )

    # The card answers why, what and where, in that order, with row lists
    # written the way a person would say them.
    assert card.splitlines()[0] == "**Task:** Remove the 18 duplicate rows"
    assert "**Action:** delete_row" in card
    assert "**Rows:** 15–20, 32" in card
    assert "**Spreadsheet:** TEST - Book Collection" in card
    assert card.strip().endswith("There is no undo.")


def test_the_permission_card_stands_without_a_task(monkeypatch):
    import streamlit as st
    from excel_agent.ui import permission_card

    class Chosen:
        def in_use(self):
            return "TEST - Sales Orders"

    class Holding:
        session = Chosen()

    monkeypatch.setattr(st, "session_state", Holding())

    card = permission_card(
        {"tool": "update_row", "arguments": {"row": 3}}
    )

    # No task line rather than an empty one, and the spreadsheet falls back
    # to the one the conversation is working on.
    assert "Task" not in card
    assert card.splitlines()[0] == "**Action:** update_row"
    assert "**Row:** 3" in card
    assert "**Spreadsheet:** TEST - Sales Orders" in card


def test_the_card_survives_every_shape_of_argument():
    """REGRESSION: fill_rows carries rows as a list of dicts, and the card
    tried to collapse them into ranges. The unhashable dict crashed the
    whole page, not just the card."""
    from excel_agent.ui import permission_card

    card = permission_card(
        {
            "tool": "fill_rows",
            "task": "Fill the Notes column",
            "arguments": {
                "start_row": 2,
                "rows": [{"Notes": "classic"}, {"Notes": "romance"}],
                "spreadsheet": "TEST - Book Collection",
            },
        }
    )

    assert "**Action:** fill_rows" in card
    assert "2 row(s), each with its own values" in card
    assert "**Start row:** 2" in card


def test_an_empty_row_list_is_shown_rather_than_crashing():
    from excel_agent.ui import permission_card

    card = permission_card(
        {
            "tool": "delete_row",
            "arguments": {"rows": [], "spreadsheet": "TEST - Book Collection"},
        }
    )

    assert "**Rows:**" in card


def gated_tools() -> list[str]:
    """Every tool that stops to ask, read off the agents themselves."""
    import re

    from excel_agent.agents import row_editor, structure_editor

    found = []

    for module in (row_editor, structure_editor):
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        found += re.findall(r'"(\w+)": CONFIRMED', source)

    return found


# One realistic call per gated tool, in the shape the tool really takes.
GATED_CALLS = {
    "delete_row": {"rows": [15, 16, 17, 32]},
    "update_row": {"rows": [3, 5], "values": {"Region": "West"}},
    "fill_rows": {"start_row": 2, "rows": [{"Notes": "a"}, {"Notes": "b"}]},
    "sort_rows": {"column": "Rating", "descending": True},
    "move_row": {"row": 3, "to_row": 9999},
    "delete_column": {"column": "Notes"},
    "set_column_formula": {
        "column": "Profit",
        "formula": "=F2*H2",
        "mode": "fill_down",
    },
    "rename_column": {"column": "Notes", "new_name": "Comments"},
    "move_column": {"column": "Notes", "to_position": 2},
}


@pytest.mark.parametrize("tool", gated_tools())
def test_every_gated_tool_can_be_drawn_as_a_card(tool):
    """REGRESSION: fill_rows reused the argument name "rows" with a list of
    dicts where delete_row had a list of ints, and the card crashed the
    whole page. A gated tool the card cannot draw is a page that dies at the
    moment it asks permission."""
    from excel_agent.ui import permission_card

    assert tool in GATED_CALLS, (
        f"{tool} is gated but has no example call here. Add one, or the "
        "card that asks about it is never drawn in a test."
    )

    card = permission_card(
        {
            "tool": tool,
            "task": "do the thing",
            "arguments": {
                **GATED_CALLS[tool],
                "spreadsheet": "TEST - Book Collection",
            },
        }
    )

    assert f"**Action:** {tool}" in card
    assert "There is no undo." in card


def test_an_approved_action_is_listed_once_not_twice():
    """REGRESSION: resuming re-runs the specialist, so the approved call was
    made again and the actions list showed delete_row(row=500) twice for one
    deletion."""
    from excel_agent.ui import joined

    whole = joined(
        {
            "role": "assistant",
            "text": "",
            "calls": ["inspect_sheet()", "delete_row(row=500)"],
            "artifacts": [],
            "waiting": [{"tool": "delete_row", "arguments": {}, "id": "a"}],
        },
        {
            "role": "assistant",
            "text": "Row 500 is gone.",
            "calls": ["delete_row(row=500)"],
            "artifacts": [],
            "waiting": [],
        },
    )

    assert whole["calls"] == ["inspect_sheet()", "delete_row(row=500)"]


def test_a_genuinely_different_action_after_approval_is_kept():
    from excel_agent.ui import joined

    whole = joined(
        {
            "role": "assistant",
            "text": "",
            "calls": ["delete_row(row=500)"],
            "artifacts": [],
            "waiting": [{"tool": "delete_row", "arguments": {}, "id": "a"}],
        },
        {
            "role": "assistant",
            "text": "Done.",
            "calls": ["delete_row(row=500)", "inspect_sheet()"],
            "artifacts": [],
            "waiting": [],
        },
    )

    # Only the replay is dropped; work done after it still counts.
    assert whole["calls"] == ["delete_row(row=500)", "inspect_sheet()"]
