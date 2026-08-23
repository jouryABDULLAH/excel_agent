"""Tests for the Google layer that needs no Google.

Everything here is the part of sheets.py that turns Google's answers into
something the tools can use, and turns the tools' numbers into what Google
wants. None of it reaches the network, which is the point: the arithmetic that
decides which row gets deleted should not need a spreadsheet to check.
"""

import fake_sheets
import pytest

from excel_agent.services.drive import drive_service, quoted
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.sheets import (
    Cell,
    cell,
    column_letter,
    find_header_row,
    header_map,
    is_blank,
    last_data_row,
    resolve_spreadsheet,
    to_grid_range,
    a1,
)

# The one live parser of Google's cell shape; the module-level copy that
# these tests used to drive was deleted with the rest of the duplicate
# client.
as_cell = spreadsheet_service._as_cell


# Turning a name into the file it means


@pytest.fixture
def a_drive(monkeypatch):
    """Stand in for what Drive returns, without asking Drive.

    Drive matches a name by what contains it, so what search gives back here
    is every file whose name holds the one asked for, the way Drive would.

    Patched on the shared drive_service, which is what resolving goes
    through; its name-to-id cache is cleared so one test's answer cannot
    leak into the next.
    """

    def use(*titles: str):
        drive_service._spreadsheet_ids.clear()

        def search(name=None):
            return [
                (f"id-{title}", title)
                for title in titles
                if not name or name.lower() in title.lower()
            ]

        monkeypatch.setattr(drive_service, "search_spreadsheets", search)

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


def test_a_quote_in_a_search_term_cannot_close_the_query():
    # Search terms reach here from the model, so this is not only about names
    # with apostrophes in them.
    assert quoted("O'Brien") == "O\\'Brien"
    assert quoted("back\\slash") == "back\\\\slash"


# Reaching a column however its name was written


def a_header_map():
    return header_map(
        [[fake_sheets.text("Order ID"), fake_sheets.text("Profit Margin")]],
        1,
    )


def test_a_column_is_reached_however_it_was_capitalised():
    """REGRESSION: 'profit margin' missed 'Profit Margin', and the worker
    fell back to column letters and wrote to the wrong column."""
    headers = a_header_map()

    assert "profit margin" in headers
    assert headers["profit margin"] == 2
    assert headers.get("PROFIT MARGIN") == 2
    # Surrounding spaces are typing, not identity.
    assert headers["  profit margin  "] == 2


def test_a_name_no_column_has_is_still_missing():
    headers = a_header_map()

    assert "Nonsense" not in headers
    assert headers.get("Nonsense") is None

    with pytest.raises(KeyError):
        headers["Nonsense"]


def test_the_real_spelling_is_what_comes_back_out():
    headers = a_header_map()

    # What the sheet holds, for messages that name the columns that exist.
    assert list(headers) == ["Order ID", "Profit Margin"]


def test_the_exact_spelling_wins_when_a_sheet_holds_both():
    headers = header_map(
        [[fake_sheets.text("Region"), fake_sheets.text("region")]],
        1,
    )

    assert headers["Region"] == 1
    assert headers["region"] == 2


def test_two_columns_differing_only_in_case_refuse_a_third_spelling():
    headers = header_map(
        [[fake_sheets.text("Region"), fake_sheets.text("region")]],
        1,
    )

    # Neither is what was written, so there is nothing to prefer.
    assert "REGION" not in headers


# Reaching a column by its letter


def a_wide_map(width=26):
    return header_map(
        [[fake_sheets.text("Order ID"), fake_sheets.text("Profit Margin")]],
        1,
        width,
    )


def test_a_column_letter_reaches_that_column():
    """REGRESSION, from the traces: sheet_stats(column='E') came back
    column_not_found, because a letter was not an address anywhere outside
    the column tools."""
    headers = a_wide_map()

    assert "E" in headers
    assert headers["E"] == 5
    # However it was typed, spaces and case included.
    assert headers["e"] == 5
    assert headers[" e "] == 5


def test_a_name_beats_a_letter_that_spells_it():
    headers = header_map(
        [[fake_sheets.text("E"), fake_sheets.text("Region")]],
        1,
        26,
    )

    # A column really called E is the first one, not the fifth.
    assert headers["E"] == 1


def test_a_word_spelt_in_letters_is_not_an_address():
    """ID is column 238. On a 26-column sheet it is a word that missed."""
    headers = a_wide_map()

    assert "ID" not in headers
    assert "AA" not in headers
    assert headers.get("NO") is None


def test_a_letter_inside_a_wider_sheet_still_counts():
    headers = a_wide_map(width=300)

    # The same two letters on a sheet wide enough to hold them.
    assert headers["AA"] == 27


def test_what_is_listed_back_is_still_the_real_names():
    headers = a_wide_map()

    assert list(headers) == ["Order ID", "Profit Margin"]


# A sheet with no header row at all


def test_no_header_row_names_no_columns():
    """header_row 0 means there is no header. rows[0 - 1] is rows[-1], so
    this used to make column names out of the last row of data."""
    rows = [
        [fake_sheets.number(1), fake_sheets.number(2)],
        [fake_sheets.text("x"), fake_sheets.text("y")],
    ]

    headers = header_map(rows, 0, width=26)

    assert list(headers) == []
    # And the letters still reach the columns, which is what makes a sheet
    # with no names workable at all.
    assert headers["A"] == 1
    assert headers["B"] == 2


def test_every_row_is_data_when_there_is_no_header():
    rows = [
        [fake_sheets.number(1)],
        [fake_sheets.number(2)],
    ]

    # Counting from the header down, with no header, is counting from row 1.
    assert last_data_row(rows, 0) == 2


def test_a_sheet_starting_with_numbers_has_no_header():
    """A first row of values is data. Called row 1 before, which lost that
    row and named every column after one of its values."""
    rows = [
        [fake_sheets.number(1), fake_sheets.number(2)],
        [fake_sheets.number(3), fake_sheets.number(4)],
    ]

    assert find_header_row(rows) == 0


def test_a_sheet_with_nothing_in_it_has_no_header():
    assert find_header_row([]) == 0
    assert find_header_row([[fake_sheets.text("")]]) == 0


def test_a_single_column_sheet_is_still_headed():
    """One column fails the two-filled-cells test, so nothing proves it
    headerless; it keeps the answer it always had."""
    rows = [
        [fake_sheets.text("Notes")],
        [fake_sheets.text("first")],
    ]

    assert find_header_row(rows) == 1


def test_a_sheet_of_text_with_no_row_below_is_left_alone():
    # Unproven either way, so unchanged.
    assert find_header_row([[fake_sheets.text("a"), fake_sheets.text("b")]]) == 1


def test_a_header_of_years_beside_a_name_is_still_a_header():
    """"Product | 2024 | 2025" names its later columns by their year. Only
    a row with no text at all is data."""
    rows = [
        [
            fake_sheets.text("Product"),
            fake_sheets.number(2024),
            fake_sheets.number(2025),
        ],
        [fake_sheets.text("Laptop"), fake_sheets.number(12), fake_sheets.number(15)],
    ]

    assert find_header_row(rows) == 1
