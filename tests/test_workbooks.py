"""Tests for the workbook listing tool.

It reads the names of the files in the data folder, so the tests point that
folder at a temporary one and put files in it.
"""

import make_fixtures
import pytest

from excel_agent import config
from excel_agent.tools.workbooks import list_workbooks


@pytest.fixture
def data_folder(tmp_path, monkeypatch):
    """Make an empty folder the one the tools list and read from."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "WORKBOOK_PATH", tmp_path / "sales.xlsx")
    return tmp_path


def test_every_workbook_in_the_folder_is_named(data_folder):
    make_fixtures.clean_table(data_folder)
    make_fixtures.multi_sheet(data_folder)

    answer = list_workbooks.invoke({})

    assert "2 workbooks" in answer
    assert "clean_table.xlsx" in answer
    assert "multi_sheet.xlsx" in answer


def test_the_workbook_being_worked_on_is_marked(data_folder, monkeypatch):
    path = make_fixtures.clean_table(data_folder)
    make_fixtures.multi_sheet(data_folder)
    monkeypatch.setattr(config, "WORKBOOK_PATH", path)

    answer = list_workbooks.invoke({})

    # Leaving the workbook argument out of the other tools reaches this one,
    # so the model has to be able to tell which it is.
    assert "clean_table.xlsx (the one in use)" in answer
    assert "multi_sheet.xlsx\n" in answer + "\n"
    assert "multi_sheet.xlsx (the one in use)" not in answer


def test_one_workbook_is_not_described_as_several(data_folder):
    make_fixtures.clean_table(data_folder)

    assert "1 workbook in" in list_workbooks.invoke({})


def test_an_empty_folder_says_so_rather_than_nothing(data_folder):
    answer = list_workbooks.invoke({})

    assert "no workbooks" in answer


def test_files_that_are_not_workbooks_are_left_out(data_folder):
    make_fixtures.clean_table(data_folder)
    (data_folder / "notes.txt").write_text("not a workbook")
    (data_folder / "sales.xlsx.bak").write_bytes(b"an old backup")

    answer = list_workbooks.invoke({})

    # A backup and a text file are not things the other tools could open.
    assert "1 workbook in" in answer
    assert "notes.txt" not in answer
    assert ".bak" not in answer


def test_the_names_it_gives_are_names_the_other_tools_accept(data_folder):
    make_fixtures.multi_sheet(data_folder)

    answer = list_workbooks.invoke({})

    # The listing is only useful if what it prints can be passed straight back
    # in, so the round trip is what is checked here.
    for line in answer.splitlines()[1:]:
        name = line.strip().replace(" (the one in use)", "")
        assert config.resolve_workbook(name).name == name