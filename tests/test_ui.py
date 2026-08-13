"""Tests for the parts of the browser front end that are not Streamlit.

Drawing is left to a run by hand. What is tested here is the upload, because
that writes to the folder the agent works in and is the one place the front
end could damage something.
"""

import pathlib

import make_fixtures
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel, calling
from streamlit.testing.v1 import AppTest

from excel_agent.runner import Session
from excel_agent.tools import LOCAL_TOOLS

import excel_agent.ui
from excel_agent.ui import save_upload

# AppTest resolves a relative path against this file, so the page is found
# through the module rather than by guessing at the layout of the repo.
PAGE = str(pathlib.Path(excel_agent.ui.__file__))


class Upload:
    """Stands in for what Streamlit hands over from a file picker."""

    def __init__(self, name: str, content: bytes = b"pretend workbook"):
        self.name = name
        self._content = content

    def getbuffer(self) -> bytes:
        return self._content


def test_a_workbook_is_saved_under_its_own_name(tmp_path):
    answer = save_upload(Upload("orders.xlsx"), tmp_path)

    assert answer == "Saved orders.xlsx."
    assert (tmp_path / "orders.xlsx").read_bytes() == b"pretend workbook"


def test_a_file_that_is_not_a_workbook_is_turned_away(tmp_path):
    answer = save_upload(Upload("notes.txt"), tmp_path)

    assert "not a .xlsx file" in answer
    assert list(tmp_path.iterdir()) == []


def test_a_name_already_taken_is_not_written_over(tmp_path):
    (tmp_path / "sample.xlsx").write_bytes(b"the file the user works in")

    answer = save_upload(Upload("sample.xlsx"), tmp_path)

    assert "already a workbook called sample.xlsx" in answer
    # The point of refusing: what was there is still there.
    assert (tmp_path / "sample.xlsx").read_bytes() == b"the file the user works in"


def test_an_upload_cannot_write_outside_the_folder(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()

    answer = save_upload(Upload("../../escaped.xlsx"), folder)

    assert answer == "Saved escaped.xlsx."
    assert (folder / "escaped.xlsx").exists()
    # The name was cut back to its last part, so nothing landed above the
    # folder the agent works in.
    assert not (tmp_path.parent / "escaped.xlsx").exists()
    assert not (tmp_path / "escaped.xlsx").exists()


def test_the_folder_is_made_if_it_is_not_there(tmp_path):
    folder = tmp_path / "not yet"

    assert save_upload(Upload("orders.xlsx"), folder) == "Saved orders.xlsx."
    assert (folder / "orders.xlsx").exists()


# The page itself


def test_the_page_draws_without_falling_over(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    make_fixtures.multi_sheet(tmp_path)

    page = AppTest.from_file(PAGE, default_timeout=60).run()

    # No turn is asked for, so this builds the agent and draws the page and
    # nothing else. It is the check that would catch a page that raises the
    # moment anyone opens it.
    assert [error.value for error in page.exception] == []
    assert [title.value for title in page.title] == ["Excel agent"]
    assert page.chat_input


def test_the_sidebar_offers_the_workbooks_and_the_two_variants(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    make_fixtures.multi_sheet(tmp_path)

    page = AppTest.from_file(PAGE, default_timeout=60).run()

    files = {one.label: list(one.options) for one in page.sidebar.selectbox}
    assert files["Working on"] == ["clean_table.xlsx", "multi_sheet.xlsx"]

    offered = {radio.label: list(radio.options) for radio in page.sidebar.radio}
    assert offered["Agents"] == ["single", "multi"]
    # The workbook in use is named on the page, so it is never a guess which
    # file a change would land in.
    assert any("clean_table.xlsx" in caption.value for caption in page.caption)


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


def test_a_turn_that_calls_a_tool_is_drawn_whole(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage("Set row 2 to 99."),
        ],
        tools=LOCAL_TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # The answer reaches the page, and the call behind it is counted. Every
    # scripted page before this one held no tools, so a turn that used one had
    # never been drawn at all.
    said = page.session_state["transcript"][-1]
    assert said["text"] == "Set row 2 to 99."
    assert said["calls"] == ["modify_row(action='edit', row=2, values={'Units': 99})"]
    assert any("Set row 2 to 99." in one.value for one in page.markdown)


def test_a_turn_that_moves_to_another_file_is_still_drawn(
    tmp_path, use_workbook, monkeypatch
):
    use_workbook(make_fixtures.clean_table(tmp_path))

    # The agent can move to another file part way through a turn, and the page
    # redraws when it does. Standing in for that here, because no local tool
    # moves anywhere: the move has to happen while the turn is running, which
    # is the whole point, so a tool is what does it.
    where = {"file": "clean_table.xlsx"}

    @tool
    def move_along() -> str:
        """Work on another file from now on."""
        where["file"] = "multi_sheet.xlsx"
        return "Moved."

    monkeypatch.setitem(excel_agent.ui.IN_USE, "in_use", lambda: where["file"])

    page = page_with_a_scripted_agent(
        "",
        script=[calling("move_along", "1"), AIMessage("Moved along.")],
        tools=[move_along],
    ).run()

    page.chat_input[0].set_value("work on the other file").run()

    # Redrawing must not throw the turn away: what was asked and what came
    # back are both in the transcript, and both are on the page.
    assert [said["text"] for said in page.session_state["transcript"]] == [
        "work on the other file",
        "Moved along.",
    ]
    assert any("Moved along." in one.value for one in page.markdown)


def test_a_turn_that_says_nothing_says_that(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    page = page_with_a_scripted_agent(
        "",
        script=[
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage(""),
        ],
        tools=LOCAL_TOOLS,
    ).run()

    page.chat_input[0].set_value("set row 2 units to 99").run()

    # An empty bubble reads as the page having lost the answer. It must not
    # claim nothing happened either: the call above it did edit the sheet.
    said = page.session_state["transcript"][-1]
    assert "ended without anything being said" in said["text"]
    assert said["calls"] == ["modify_row(action='edit', row=2, values={'Units': 99})"]


def test_a_turn_that_falls_over_stays_on_the_page(tmp_path, use_workbook, monkeypatch):
    use_workbook(make_fixtures.clean_table(tmp_path))
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


def test_clicking_a_suggestion_asks_it(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    page = page_with_a_scripted_agent("Five rows.").run()
    asked = page.button[0].label

    page.button[0].click().run()

    assert [said["text"] for said in page.session_state["transcript"]] == [
        asked,
        "Five rows.",
    ]


def test_the_suggestions_are_taken_off_the_page_once_one_is_used(
    tmp_path, use_workbook
):
    use_workbook(make_fixtures.clean_table(tmp_path))
    page = page_with_a_scripted_agent("Five rows.").run()

    page.button[0].click().run()

    # Left on screen they would belong to a run that has ended, and clicking
    # one again would do nothing at all.
    assert [button.label for button in page.button] == ["New conversation"]
