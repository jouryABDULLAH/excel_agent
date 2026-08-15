"""Tests for the Google layer that needs no Google.

Everything here is the part of sheets.py that turns Google's answers into
something the tools can use, and turns the tools' numbers into what Google
wants. None of it reaches the network, which is the point: the arithmetic that
decides which row gets deleted should not need a spreadsheet to check.
"""

import fake_sheets
import pytest

from excel_agent import sheets as sheets_module
from excel_agent.sheets import (
    Cell,
    as_cell,
    cell,
    column_letter,
    find_header_row,
    header_map,
    is_blank,
    last_data_row,
    quoted,
    resolve_spreadsheet,
    to_dimension_range,
    to_grid_range,
    a1,
)


# Turning a name into the file it means


@pytest.fixture
def a_drive(monkeypatch):
    """Stand in for what Drive returns, without asking Drive.

    Drive matches a name by what contains it, so what search gives back here
    is every file whose name holds the one asked for, the way Drive would.

    Patched on the DriveService that sheets.py holds, rather than on
    sheets.search: resolving goes straight to the service now, so the module
    level function is no longer on the path this exercises.
    """

    def use(*titles: str):
        sheets_module._drive._spreadsheet_ids.clear()

        def search(name=None):
            return [
                (f"id-{title}", title)
                for title in titles
                if not name or name.lower() in title.lower()
            ]

        monkeypatch.setattr(sheets_module._drive, "search_spreadsheets", search)

    return use


def test_a_name_that_matches_one_file_reaches_it(a_drive):
    a_drive("TEST - Sales Orders", "TEST - Raw Contacts")

    assert resolve_spreadsheet("TEST - Sales Orders") == (
        "id-TEST - Sales Orders",
        "TEST - Sales Orders",
    )


def test_a_name_another_file_begins_with_still_reaches_its_own(a_drive):
    a_drive("TEST - Sales Orders", "TEST - Sales Orders - scratch")

    # Drive returns both, because one name contains the other. Only one is
    # called this, and that is the answer: otherwise a file could be made
    # unreachable by creating another beside it with a longer name.
    assert resolve_spreadsheet("TEST - Sales Orders")[1] == "TEST - Sales Orders"
    assert (
        resolve_spreadsheet("TEST - Sales Orders - scratch")[1]
        == "TEST - Sales Orders - scratch"
    )


def test_part_of_a_name_reaches_the_only_file_holding_it(a_drive):
    a_drive("TEST - Sales Orders", "TEST - Raw Contacts")

    assert resolve_spreadsheet("raw")[1] == "TEST - Raw Contacts"


def test_part_of_a_name_that_several_hold_is_refused_by_full_name(a_drive):
    a_drive("TEST - Sales Orders", "TEST - Sales Orders - scratch")

    with pytest.raises(ValueError) as refused:
        resolve_spreadsheet("Sales")

    # Asking for a full name is something the tools can act on, which asking
    # for an id is not: no tool takes one.
    assert 'No spreadsheet is called exactly "Sales"' in str(refused.value)
    assert "by its full name" in str(refused.value)


def test_two_files_really_sharing_a_name_are_refused(a_drive):
    a_drive("TEST - Simple Budget", "TEST - Simple Budget")

    with pytest.raises(ValueError, match="More than one spreadsheet is called"):
        resolve_spreadsheet("TEST - Simple Budget")


def test_a_name_reaching_nothing_says_so(a_drive):
    a_drive("TEST - Sales Orders")

    with pytest.raises(ValueError, match="There is no spreadsheet called"):
        resolve_spreadsheet("Nonsense")


# Reading Google's answer


def test_a_cell_is_read_three_ways_at_once():
    one = as_cell(
        {
            "formattedValue": "$1,234.00",
            "effectiveValue": {"numberValue": 1234},
            "userEnteredValue": {"formulaValue": "=B2*C2"},
        }
    )

    # What a person sees, what it works out to, and what was typed in. A table
    # wants the first, a total wants the second, and only the third says the
    # sheet is calculating it.
    assert one.displayed == "$1,234.00"
    assert one.value == 1234
    assert one.formula == "=B2*C2"


def test_a_cell_that_holds_nothing_reads_as_nothing():
    one = as_cell({})

    assert one.displayed is None
    assert one.value is None
    assert one.formula is None


def test_a_date_is_known_by_how_the_sheet_formats_it():
    dated = as_cell(
        {
            "formattedValue": "2026-01-03",
            "effectiveValue": {"numberValue": 46025},
            "effectiveFormat": {"numberFormat": {"type": "DATE"}},
        }
    )
    plain = as_cell(
        {"formattedValue": "46025", "effectiveValue": {"numberValue": 46025}}
    )

    # Both are the same number underneath. Without the format, a column of
    # dates would be summarised as arithmetic on five figure numbers.
    assert dated.value == plain.value
    assert dated.is_date
    assert not plain.is_date


def test_text_and_numbers_are_told_apart_by_which_key_google_used():
    assert as_cell({"effectiveValue": {"stringValue": "North"}}).value == "North"
    assert as_cell({"effectiveValue": {"numberValue": 3}}).value == 3
    assert as_cell({"effectiveValue": {"boolValue": True}}).value is True


# Reaching into ragged rows


def test_a_cell_past_the_end_of_a_row_is_empty_rather_than_an_error():
    rows = [[fake_sheets.text("A"), fake_sheets.text("B")], [fake_sheets.text("x")]]

    # Google stops a row at its last filled cell rather than padding it out,
    # so asking beyond that is ordinary.
    assert cell(rows, 2, 2).displayed is None
    assert cell(rows, 99, 1).displayed is None
    assert cell(rows, 1, 2).displayed == "B"


def test_a_cell_before_the_first_one_is_empty_too():
    rows = [[fake_sheets.text("A")]]

    assert cell(rows, 0, 1).displayed is None
    assert cell(rows, 1, 0).displayed is None


# Finding the shape of a table


def test_a_header_in_the_first_row_is_found():
    rows = fake_sheets.orders()

    assert find_header_row(rows) == 1
    assert header_map(rows, 1) == {"Order ID": 1, "Region": 2, "Units": 3, "Product": 4}
    assert last_data_row(rows, 1) == 6


def test_a_title_above_the_table_is_stepped_over():
    rows = [
        [fake_sheets.text("Q1 Sales Report")],
        [],
        [fake_sheets.text("Order ID"), fake_sheets.text("Region")],
        [fake_sheets.text("ORD-1001"), fake_sheets.text("North")],
    ]

    # One filled cell is a title, not a header.
    assert find_header_row(rows) == 3


def test_a_row_of_years_is_not_taken_for_a_header():
    rows = [
        [fake_sheets.text("Product"), fake_sheets.number(2024), fake_sheets.number(2025)],
        [fake_sheets.text("Laptop"), fake_sheets.number(12), fake_sheets.number(15)],
    ]

    # Google formats every cell into a string for display, so 2024 shows as
    # "2024" and would pass a check made on what is displayed. Asking the cell
    # for its value is what keeps this in step with the local backend.
    assert find_header_row(rows) == 1
    assert header_map(rows, 1) == {"Product": 1, "2024": 2, "2025": 3}


def test_a_blank_row_inside_the_data_is_not_the_end_of_it():
    rows = fake_sheets.orders()
    rows.insert(3, [])

    assert last_data_row(rows, 1) == 7


def test_an_empty_string_names_no_column():
    rows = [
        [fake_sheets.text("ID"), fake_sheets.text("  "), fake_sheets.text("Region")],
        [fake_sheets.text("1"), fake_sheets.text("x"), fake_sheets.text("North")],
    ]

    # A column called "" is one nothing could ask for by name.
    assert header_map(rows, 1) == {"ID": 1, "Region": 3}


def test_a_sheet_with_only_a_header_has_no_data():
    rows = [[fake_sheets.text("ID"), fake_sheets.text("Region")]]

    assert last_data_row(rows, 1) == 1


def test_nothing_and_nothing_worth_reading_are_the_same():
    assert is_blank(None)
    assert is_blank("")
    assert is_blank("   ")
    assert not is_blank(0)
    assert not is_blank("North")


# Writing what Google wants


@pytest.mark.parametrize(
    "number, letters", [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (703, "AAA")]
)
def test_a_column_number_becomes_its_letters(number, letters):
    assert column_letter(number) == letters


def test_a_range_reads_the_way_a_person_writes_one():
    assert a1("Sales") == "'Sales'"
    assert a1("Sales", 2, 10, 1, 4) == "'Sales'!A2:D10"
    # No last row means the rest of the sheet, however far the data goes.
    assert a1("Sales", 2, None, 1, 4) == "'Sales'!A2:D"


def test_one_row_alone_becomes_the_indexes_google_counts_in():
    # Row 7 alone: Google counts from 0 and leaves the end out, so 6 to 7.
    assert to_grid_range(0, 7, 7) == {
        "sheetId": 0,
        "startRowIndex": 6,
        "endRowIndex": 7,
    }


def test_a_bound_left_out_means_the_whole_of_that_direction():
    assert to_grid_range(5, first_column=2, last_column=3) == {
        "sheetId": 5,
        "startColumnIndex": 1,
        "endColumnIndex": 3,
    }


def test_a_run_of_rows_becomes_a_dimension_range():
    assert to_dimension_range(0, "ROWS", 2, 3) == {
        "sheetId": 0,
        "dimension": "ROWS",
        "startIndex": 1,
        "endIndex": 3,
    }


def test_one_row_needs_no_end_given():
    assert to_dimension_range(0, "COLUMNS", 4) == {
        "sheetId": 0,
        "dimension": "COLUMNS",
        "startIndex": 3,
        "endIndex": 4,
    }


def test_a_quote_in_a_search_term_cannot_close_the_query():
    # Search terms reach here from the model, so this is not only about names
    # with apostrophes in them.
    assert quoted("O'Brien") == "O\\'Brien"
    assert quoted("back\\slash") == "back\\\\slash"
