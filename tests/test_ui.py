"""Tests for the parts of the browser front end that are not Streamlit.

Drawing is left to a run by hand. What is tested here is the upload, because
that writes to the folder the agent works in and is the one place the front
end could damage something.
"""

import pathlib

import make_fixtures
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel
from streamlit.testing.v1 import AppTest

from excel_agent.runner import Session

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


def page_with_a_scripted_agent(says: str) -> AppTest:
    """The page with an agent already in place, so no Groq request is made."""
    page = AppTest.from_file(PAGE, default_timeout=60)
    page.session_state["variant"] = "single"
    page.session_state["transcript"] = []
    page.session_state["session"] = Session(
        create_agent(
            ScriptedModel(script=[AIMessage(says)]),
            [],
            system_prompt="terse",
            checkpointer=InMemorySaver(),
        )
    )
    return page


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
