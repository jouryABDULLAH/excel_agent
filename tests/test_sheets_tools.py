"""Tests for the Google reading tools, without Google.

Each tool imports what it needs from sheets.py by name, so what is replaced
here is the name inside the tool's own module. Patching excel_agent.sheets
would not reach them: the tool holds its own reference, taken at import.

What is asserted on is the string the model reads back, because that is the
whole of what a tool gives it.
"""

import fake_sheets
import pytest

from excel_agent.tools import columns, find, inspect, modify, spreadsheets, stats

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


@pytest.fixture
def a_sheet(monkeypatch):
    """Point the reading tools at a sheet built by hand."""

    def use(rows, module=inspect, title=SHEET, spreadsheet=SPREADSHEET):
        monkeypatch.setattr(
            module, "resolve_spreadsheet", lambda name=None: ("an-id", spreadsheet)
        )
        monkeypatch.setattr(
            module, "resolve_sheet", lambda id, name=None: {"title": title, "sheetId": 0}
        )
        monkeypatch.setattr(module, "grid", lambda id, title: rows)
        # A sheet with no charts on it. Without this, reading one would go out
        # to Google to ask, which is the one thing these tests must not do.
        if hasattr(module, "charts_in"):
            monkeypatch.setattr(module, "charts_in", lambda id, title: [])
        return rows

    return use


# Reading a sheet


def test_the_rows_come_back_with_the_numbers_the_sheet_shows(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({})

    assert "Sheet: Sales Orders in TEST - Sales Orders (5 rows of data" in answer
    assert "| row | Order ID | Region | Units | Product |" in answer
    # Row 2 in the table is row 2 in the sheet, which is what a change needs.
    assert "| 2 | ORD-1001 | North | 1 | Laptop |" in answer


def test_only_the_columns_asked_for_come_back_and_in_that_order(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({"columns": ["Product", "Region"]})

    assert "| row | Product | Region |" in answer
    assert "| 2 | Laptop | North |" in answer


def test_a_column_that_is_not_there_is_named_with_the_ones_that_are(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({"columns": ["Profit"]})

    assert "Unknown column(s): Profit" in answer
    assert "Order ID, Region, Units, Product" in answer


def test_a_shortened_read_says_how_to_see_the_rest(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({"max_rows": 2})

    assert "Showing rows 2 to 3." in answer
    assert "Rows 4 to 6 were not shown" in answer
    assert "start_row=4" in answer


def test_reading_past_the_end_says_where_the_data_ends(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({"start_row": 500})

    assert "ending at row 6" in answer
    assert "nothing to read from row 500" in answer


def test_asking_for_no_rows_explains_itself(a_sheet):
    a_sheet(fake_sheets.orders())

    assert "no rows were read" in inspect.inspect_sheet.invoke({"max_rows": 0})


def test_what_the_sheet_displays_is_what_is_shown(a_sheet):
    rows = [
        [fake_sheets.text("Order"), fake_sheets.text("Placed"), fake_sheets.text("Paid")],
        [
            fake_sheets.text("ORD-1"),
            fake_sheets.date("2026-01-03"),
            fake_sheets.number(1234, "$1,234.00"),
        ],
    ]
    a_sheet(rows)

    answer = inspect.inspect_sheet.invoke({})

    # Google formats a date and a currency the way the sheet shows them, so
    # nothing here has to know about either.
    assert "| 2 | ORD-1 | 2026-01-03 | $1,234.00 |" in answer


def test_a_cell_still_being_worked_out_shows_its_formula(a_sheet):
    rows = [
        [fake_sheets.text("Product"), fake_sheets.text("Total")],
        [fake_sheets.text("Laptop"), fake_sheets.calculated(None, "=B2*C2")],
    ]
    a_sheet(rows)

    answer = inspect.inspect_sheet.invoke({})

    assert "=B2*C2" in answer


def test_a_sheet_with_no_data_says_so(a_sheet):
    a_sheet([[fake_sheets.text("Order ID"), fake_sheets.text("Region")]])

    assert "column names but no rows of data yet" in inspect.inspect_sheet.invoke({})


def test_a_name_that_reaches_no_spreadsheet_comes_back_as_the_explanation(
    a_sheet, monkeypatch
):
    a_sheet(fake_sheets.orders())

    def refuse(name=None):
        raise ValueError('There is no spreadsheet called "Nonsense".')

    monkeypatch.setattr(inspect, "resolve_spreadsheet", refuse)

    # A tool answers the model in prose, so a ValueError from the layer below
    # becomes the answer rather than a traceback.
    assert "no spreadsheet called" in inspect.inspect_sheet.invoke({})


# Finding the spreadsheets


def test_every_spreadsheet_is_listed(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)

    answer = spreadsheets.list_workbooks.invoke({})

    assert "2 spreadsheets:" in answer
    assert "  Sales" in answer
    assert "  Returns" in answer
    # Nothing chosen yet, so the model is told to ask rather than to pick.
    assert "No spreadsheet has been chosen yet" in answer


def test_the_one_being_worked_on_is_marked(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", "Returns")

    answer = spreadsheets.list_workbooks.invoke({})

    assert "Returns (the one being worked on)" in answer
    assert "No spreadsheet has been chosen yet" not in answer


def test_two_files_sharing_a_name_are_flagged_as_unusable(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("1", "Budget"), ("2", "Budget")]
    )
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)

    answer = spreadsheets.list_workbooks.invoke({})

    # Naming either would reach neither, so it is said here rather than left
    # for the next call to fail on.
    assert "more than one file has this name" in answer


def test_a_search_that_finds_nothing_says_what_was_looked_for(monkeypatch):
    monkeypatch.setattr(spreadsheets, "search", lambda name=None: [])

    assert 'No spreadsheet has "sales" in its name.' in (
        spreadsheets.list_workbooks.invoke({"name": "sales"})
    )
    assert "There are no spreadsheets in this Drive." in (
        spreadsheets.list_workbooks.invoke({})
    )


# Finding data


def test_a_value_is_found_with_its_row_number(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke({"text": "North"})

    assert '2 row(s) in Sales Orders in TEST - Sales Orders hold "North"' in answer
    assert "| 2 | Region | ORD-1001 | North | 1 | Laptop |" in answer
    assert "| 4 | Region |" in answer


def test_looking_in_one_column_only(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke({"text": "Laptop", "column": "Product"})

    # Laptop Stand contains Laptop, so both match unless the whole cell is
    # asked for.
    assert "2 row(s)" in answer


def test_a_whole_cell_match_is_stricter(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke(
        {"text": "Laptop", "column": "Product", "whole_cell": True}
    )

    assert "1 row(s)" in answer
    assert "Laptop Stand" not in answer.split("\n")[0]


def test_case_does_not_matter(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    assert "2 row(s)" in find.find_data.invoke({"text": "north"})


def test_nothing_matching_says_where_it_looked(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke({"text": "Antarctica"})

    assert 'Nothing in any column holds "Antarctica"' in answer


def test_a_column_that_is_not_there_is_refused(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke({"text": "North", "column": "Nonsense"})

    assert 'There is no column called "Nonsense"' in answer
    assert "Order ID, Region, Units, Product" in answer


def test_asking_for_nothing_is_refused(a_sheet):
    a_sheet(fake_sheets.orders(), module=find)

    assert find.find_data.invoke({"text": "   "}) == "Say what to look for."


def test_which_file_holds_something_is_a_different_tool(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "containing", lambda text: [("1", "Sales"), ("2", "Returns")]
    )

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    assert '2 spreadsheet(s) hold "quarterly"' in answer
    assert "  Sales" in answer
    # It says nothing about rows, which is what keeps it out of find_data's
    # territory and safe for the orchestrator to hold.
    assert "| row |" not in answer


def test_a_search_does_not_push_a_change_of_file_when_one_is_in_hand(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "containing", lambda text: [("1", "Sales"), ("2", "Returns")]
    )
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", "Sales")

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    # Asking which files mention a word is a question, not a request to move
    # off the spreadsheet already being worked on.
    assert "Sales (the one being worked on)" in answer
    assert "Nothing is being worked on yet" not in answer


def test_a_search_says_what_to_do_next_when_no_file_is_settled(monkeypatch):
    monkeypatch.setattr(spreadsheets, "containing", lambda text: [("1", "Sales")])
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    assert "Nothing is being worked on yet" in answer


def test_a_drive_search_that_finds_nothing_says_why_it_might_not_have(monkeypatch):
    monkeypatch.setattr(spreadsheets, "containing", lambda text: [])

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    # Drive indexes a file after it is written, so a change made a moment ago
    # is genuinely not findable yet, and that is worth saying.
    assert "indexes a file after it is written" in answer
    assert "whole words" in answer


def test_find_spreadsheet_needs_something_to_look_for(monkeypatch):
    monkeypatch.setattr(spreadsheets, "containing", lambda text: [("1", "Sales")])

    assert spreadsheets.find_spreadsheet.invoke({"text": "  "}) == "Say what to look for."


def test_find_data_no_longer_searches_the_drive():
    # The two questions are asked of different things: Drive knows which file,
    # the sheet knows which row. Keeping them in one tool put a row number
    # within the orchestrator's reach.
    assert "across_drive" not in find.find_data.args


def test_a_long_list_of_matches_is_cut_short(a_sheet, monkeypatch):
    monkeypatch.setattr(find, "MATCH_LIMIT", 2)
    a_sheet(fake_sheets.orders(), module=find)

    answer = find.find_data.invoke({"text": "ORD", "column": "Order ID"})

    assert "5 row(s)" in answer
    assert "3 more row(s) matched and are not shown" in answer


# Summarising a column


def test_a_column_of_numbers_gets_its_range_and_total(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    answer = stats.sheet_stats.invoke({"column": "Units"})

    assert '"Units" in Sales Orders in TEST - Sales Orders' in answer
    assert "5 filled, 0 blank, 5 different" in answer
    assert "1 to 5, adding up to 15." in answer


def test_money_keeps_its_symbol_at_the_ends_of_the_range(a_sheet):
    rows = [
        [fake_sheets.text("Product"), fake_sheets.text("Revenue")],
        [fake_sheets.text("Laptop"), fake_sheets.number(1200, "$1,200.00")],
        [fake_sheets.text("Dock"), fake_sheets.number(55, "$55.00")],
    ]
    a_sheet(rows, module=stats)

    answer = stats.sheet_stats.invoke({"column": "Revenue"})

    # The ends are shown the way the sheet shows them; the total is worked out
    # from the numbers underneath, where the formatting cannot reach.
    assert "$55.00 to $1,200.00, adding up to 1255." in answer


def test_a_column_of_dates_gets_a_range_and_no_total(a_sheet):
    rows = [
        [fake_sheets.text("Order"), fake_sheets.text("Placed")],
        [fake_sheets.text("A"), fake_sheets.date("2026-01-03", 46025)],
        [fake_sheets.text("B"), fake_sheets.date("2026-04-11", 46123)],
    ]
    a_sheet(rows, module=stats)

    answer = stats.sheet_stats.invoke({"column": "Placed"})

    # A date is a count of days underneath, so adding them up would give a
    # five figure number meaning nothing.
    assert "2026-01-03 to 2026-04-11." in answer
    assert "adding up to" not in answer


def test_a_column_of_words_gets_what_turns_up_most(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    answer = stats.sheet_stats.invoke({"column": "Region"})

    assert '4 different' in answer
    assert 'most often "North" 2 times' in answer


def test_words_that_never_repeat_say_so(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    assert "every value different" in stats.sheet_stats.invoke({"column": "Order ID"})


def test_blank_cells_are_counted_rather_than_ignored(a_sheet):
    rows = fake_sheets.orders()
    rows[2][1] = fake_sheets.EMPTY
    rows[3][1] = fake_sheets.Cell(displayed="   ")
    a_sheet(rows, module=stats)

    answer = stats.sheet_stats.invoke({"column": "Region"})

    assert "3 filled, 2 blank" in answer


def test_a_calculated_column_says_how_many_the_sheet_works_out(a_sheet):
    rows = [
        [fake_sheets.text("Product"), fake_sheets.text("Total")],
        [fake_sheets.text("Laptop"), fake_sheets.calculated("100", "=B2*C2", 100)],
        [fake_sheets.text("Dock"), fake_sheets.calculated("50", "=B3*C3", 50)],
    ]
    a_sheet(rows, module=stats)

    answer = stats.sheet_stats.invoke({"column": "Total"})

    # Unlike the local backend, Google has already worked these out, so they
    # can be summarised. Saying they are calculated matters because they must
    # not be written to.
    assert "50 to 100, adding up to 150." in answer
    assert "2 of them are worked out by a formula" in answer


def test_a_column_that_is_not_there_is_named_with_the_ones_that_are(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    answer = stats.sheet_stats.invoke({"column": "Profit"})

    assert 'There is no column called "Profit"' in answer
    assert "Order ID, Region, Units, Product" in answer


def test_a_sheet_with_no_rows_yet_says_so(a_sheet):
    a_sheet([[fake_sheets.text("Order ID"), fake_sheets.text("Region")]], module=stats)

    assert "no rows of data yet" in stats.sheet_stats.invoke({"column": "Region"})


# Settling on a spreadsheet


def test_choosing_a_spreadsheet_makes_it_the_one_in_use(monkeypatch):
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)
    monkeypatch.setattr(
        spreadsheets, "resolve_spreadsheet", lambda name=None: ("an-id", "Sales Orders")
    )
    monkeypatch.setattr(spreadsheets, "sheets_in", lambda id: {"Orders": {}, "Q1": {}})

    answer = spreadsheets.use_spreadsheet.invoke({"name": "sales orders"})

    # The name the file really has, not the one that was typed.
    assert 'Now working on "Sales Orders"' in answer
    assert spreadsheets.config.SPREADSHEET == "Sales Orders"


def test_choosing_a_spreadsheet_says_what_sheets_are_in_it(monkeypatch):
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)
    monkeypatch.setattr(
        spreadsheets,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", "TEST - Employee Attendance"),
    )
    monkeypatch.setattr(spreadsheets, "sheets_in", lambda id: {"Attendance": {}})

    answer = spreadsheets.use_spreadsheet.invoke({"name": "attendance"})

    # Nothing else says what the sheets are called, and the name of a file is
    # not the name of a sheet in it: told only the file name, an agent asks for
    # a sheet called "Employee Attendance", which does not exist.
    assert "It holds 1 sheet(s): Attendance." in answer
    assert "Calls that name no sheet work on Attendance." in answer


def test_a_name_that_reaches_nothing_settles_nothing(monkeypatch):
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", "Sales Orders")

    def refuse(name=None):
        raise ValueError('There is no spreadsheet called "Nonsense".')

    monkeypatch.setattr(spreadsheets, "resolve_spreadsheet", refuse)

    answer = spreadsheets.use_spreadsheet.invoke({"name": "Nonsense"})

    # Refused here rather than by every call that came after it.
    assert "no spreadsheet called" in answer
    assert spreadsheets.config.SPREADSHEET == "Sales Orders"


def test_choosing_needs_a_name(monkeypatch):
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)

    assert spreadsheets.use_spreadsheet.invoke({"name": "  "}) == (
        "Say which spreadsheet to work on."
    )


# Numbers are searched the way a sheet shows them


def test_a_number_is_looked_for_in_the_ways_a_sheet_might_show_it(monkeypatch):
    asked = []

    def only_the_formatted_one(text):
        asked.append(text)
        return [("1", "Sales")] if text == "12,240.00" else []

    monkeypatch.setattr(spreadsheets, "containing", only_the_formatted_one)
    monkeypatch.setattr(spreadsheets.config, "SPREADSHEET", None)

    answer = spreadsheets.find_spreadsheet.invoke({"text": "12240"})

    # Drive indexes what a cell shows, so a bare 12240 misses a sheet showing
    # $12,240.00. Measured against the real account before this was written.
    assert asked[0] == "12240"
    assert "12,240.00" in asked
    assert '1 spreadsheet(s) hold "12240"' in answer


def test_words_are_searched_once_and_not_reshaped(monkeypatch):
    asked = []

    def watch(text):
        asked.append(text)
        return []

    monkeypatch.setattr(spreadsheets, "containing", watch)

    spreadsheets.find_spreadsheet.invoke({"text": "Laptop"})

    # Anything that is not a number costs exactly one call.
    assert asked == ["Laptop"]


def test_a_number_that_is_nowhere_says_what_was_tried(monkeypatch):
    monkeypatch.setattr(spreadsheets, "containing", lambda text: [])

    answer = spreadsheets.find_spreadsheet.invoke({"text": "999"})

    assert "999, 999.00" in answer
    assert "the way the sheet displays it" in answer


# Changing rows


@pytest.fixture
def a_writable_sheet(a_sheet, monkeypatch):
    """A sheet to change, recording what would have been sent to Google."""
    sent: dict[str, list] = {"values": [], "requests": []}

    def use(rows=None):
        a_sheet(rows or fake_sheets.orders(), module=modify)
        monkeypatch.setattr(
            modify, "write_values", lambda id, data: sent["values"].append(data)
        )
        monkeypatch.setattr(
            modify, "batch", lambda id, requests: sent["requests"].append(requests)
        )
        return sent

    return use


def test_a_new_row_goes_under_the_last_one(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke(
        {"action": "add", "values": {"Product": "Dock", "Region": "EU"}}
    )

    assert "Added row 7 with Product = Dock, Region = EU" in answer
    # Written a cell at a time, so the columns in between are left alone.
    written = {entry["range"]: entry["values"][0][0] for entry in sent["values"][0]}
    assert written == {"'Sales Orders'!D7:D7": "Dock", "'Sales Orders'!B7:B7": "EU"}


def test_editing_writes_only_the_columns_given(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke(
        {"action": "edit", "row": 3, "values": {"Region": "West"}}
    )

    assert "Updated row 3: Region = West" in answer
    assert sent["values"][0] == [
        {"range": "'Sales Orders'!B3:B3", "values": [["West"]]}
    ]


def test_a_cell_is_cleared_by_writing_nothing_into_it(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke(
        {"action": "edit", "row": 3, "values": {"Region": None}}
    )

    # There is no way to send "no value", so an empty string is what empties
    # a cell.
    assert "Region = (blank)" in answer
    assert sent["values"][0][0]["values"] == [[""]]


def test_removing_a_row_asks_google_to_delete_that_row_alone(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke({"action": "remove", "row": 4})

    assert "Removed row 4" in answer
    assert "now out of date" in answer
    # Row 4 alone: Google counts from 0 and leaves the end out.
    assert sent["requests"][0] == [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": 0,
                    "dimension": "ROWS",
                    "startIndex": 3,
                    "endIndex": 4,
                }
            }
        }
    ]


def test_moving_a_row_down_lands_it_where_it_was_asked_for(a_writable_sheet):
    sent = a_writable_sheet()

    modify.modify_row.invoke({"action": "move", "row": 2, "to_row": 5})

    # Google counts the destination before the row is lifted out, so moving
    # down asks for the number the row should end up at.
    moved = sent["requests"][0][0]["moveDimension"]
    assert moved["source"]["startIndex"] == 1
    assert moved["destinationIndex"] == 5


def test_moving_a_row_up_lands_it_where_it_was_asked_for(a_writable_sheet):
    sent = a_writable_sheet()

    modify.modify_row.invoke({"action": "move", "row": 5, "to_row": 2})

    # Moving up, the destination is the row before the one asked for, because
    # nothing above it has been lifted out yet.
    moved = sent["requests"][0][0]["moveDimension"]
    assert moved["source"]["startIndex"] == 4
    assert moved["destinationIndex"] == 1


def test_a_row_that_does_not_exist_is_refused_and_nothing_is_sent(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke(
        {"action": "edit", "row": 9999, "values": {"Region": "EU"}}
    )

    assert "Row 9999 does not exist. The sheet has rows 2 to 6." in answer
    assert sent["values"] == [] and sent["requests"] == []


def test_the_header_row_is_not_a_row_that_can_be_changed(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke(
        {"action": "edit", "row": 1, "values": {"Region": "EU"}}
    )

    assert "Row 1 does not exist" in answer
    assert sent["values"] == []


def test_an_unknown_column_is_refused_and_nothing_is_sent(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke({"action": "add", "values": {"Profit": 10}})

    assert "Unknown column(s): Profit" in answer
    assert sent["values"] == []


def test_editing_needs_something_to_change(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke({"action": "edit", "row": 2, "values": {}})

    assert "needs at least one column in values" in answer
    assert sent["values"] == []


def test_moving_needs_somewhere_to_go(a_writable_sheet):
    sent = a_writable_sheet()

    assert "needs to_row" in modify.modify_row.invoke({"action": "move", "row": 2})
    assert sent["requests"] == []


def test_moving_a_row_to_where_it_already_is_changes_nothing(a_writable_sheet):
    sent = a_writable_sheet()

    answer = modify.modify_row.invoke({"action": "move", "row": 3, "to_row": 3})

    assert "already where it should be" in answer
    assert sent["requests"] == []


def test_a_formula_is_written_as_a_formula(a_writable_sheet):
    sent = a_writable_sheet()

    modify.modify_row.invoke(
        {"action": "edit", "row": 2, "values": {"Units": "=B2*2"}}
    )

    # USER_ENTERED is what makes Google read this as a formula rather than
    # storing the characters.
    assert sent["values"][0][0]["values"] == [["=B2*2"]]


# Changing columns


@pytest.fixture
def a_writable_columns_sheet(a_sheet, monkeypatch):
    """A sheet whose columns can be changed, recording what would be sent."""
    sent: dict[str, list] = {"values": [], "requests": []}

    def use(rows=None, width=26):
        a_sheet(rows or fake_sheets.orders(), module=columns)
        monkeypatch.setattr(
            columns,
            "resolve_sheet",
            lambda id, name=None: {
                "title": SHEET,
                "sheetId": 0,
                "gridProperties": {"columnCount": width},
            },
        )
        monkeypatch.setattr(
            columns, "write_values", lambda id, data: sent["values"].append(data)
        )
        monkeypatch.setattr(
            columns, "batch", lambda id, requests: sent["requests"].append(requests)
        )
        return sent

    return use


def test_a_new_column_goes_past_the_last_named_one(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke({"action": "add", "column": "Profit"})

    # Four named columns, so the new one is E, which is empty already: nothing
    # shifts and no formula moves.
    assert 'Added a column called "Profit", at E' in answer
    assert sent["values"][0][0]["range"] == "'Sales Orders'!E1:E1"
    assert sent["requests"] == []


def test_a_new_column_past_the_edge_of_the_grid_widens_it_first(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet(width=4)

    columns.modify_column.invoke({"action": "add", "column": "Profit"})

    # The grid is only four columns wide, so writing into the fifth would be
    # outside it until the sheet is widened.
    assert sent["requests"][0][0]["insertDimension"]["range"]["startIndex"] == 4


def test_renaming_writes_the_header_cell_and_nothing_else(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke(
        {"action": "rename", "column": "Region", "new_name": "Area"}
    )

    assert 'Renamed the column "Region" to "Area"' in answer
    assert sent["values"][0] == [
        {"range": "'Sales Orders'!B1:B1", "values": [["Area"]]}
    ]


def test_removing_a_column_says_what_happens_to_formulas(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke({"action": "remove", "column": "Region"})

    # Google rewrites what it can and leaves #REF! where it cannot, which is
    # what happens if a person deletes the column by hand.
    assert "#REF!" in answer
    assert sent["requests"][0][0]["deleteDimension"]["range"] == {
        "sheetId": 0,
        "dimension": "COLUMNS",
        "startIndex": 1,
        "endIndex": 2,
    }


def test_moving_a_column_left_and_right_lands_where_asked(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    columns.modify_column.invoke(
        {"action": "move", "column": "Product", "to_position": 1}
    )
    columns.modify_column.invoke(
        {"action": "move", "column": "Order ID", "to_position": 3}
    )

    # Moving left, the destination is the place before the one asked for;
    # moving right, it is the place itself, because the column has not been
    # lifted out yet when Google reads the number.
    assert sent["requests"][0][0]["moveDimension"]["destinationIndex"] == 0
    assert sent["requests"][1][0]["moveDimension"]["destinationIndex"] == 3


def test_a_formula_is_copied_down_rather_than_repeated(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke(
        {"action": "set_formula", "column": "Units", "formula": "=A2&B2"}
    )

    assert 'Filled "Units" with =A2&B2, down 5 row(s)' in answer
    # The first row is written, then copied: copying is what shifts =A2&B2
    # into =A3&B3 as it goes down. Repeating the text would leave every row
    # reading row 2.
    assert sent["values"][0][0]["range"] == "'Sales Orders'!C2:C2"
    pasted = sent["requests"][0][0]["copyPaste"]
    assert pasted["pasteType"] == "PASTE_FORMULA"
    assert pasted["source"] == {
        "sheetId": 0,
        "startRowIndex": 1,
        "endRowIndex": 2,
        "startColumnIndex": 2,
        "endColumnIndex": 3,
    }
    assert pasted["destination"]["startRowIndex"] == 2
    assert pasted["destination"]["endRowIndex"] == 6


def test_something_that_is_not_a_formula_is_refused(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke(
        {"action": "set_formula", "column": "Units", "formula": "B2*C2"}
    )

    assert 'A formula starts with "="' in answer
    assert sent["values"] == [] and sent["requests"] == []


def test_a_column_that_is_not_there_is_refused(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke(
        {"action": "rename", "column": "Nonsense", "new_name": "X"}
    )

    assert 'There is no column called "Nonsense"' in answer
    assert "Order ID, Region, Units, Product" in answer
    assert sent["values"] == []


def test_a_second_column_of_the_same_name_is_refused(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke({"action": "add", "column": "Region"})

    assert 'There is already a column called "Region"' in answer
    assert sent["values"] == []


def test_renaming_needs_a_new_name(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.modify_column.invoke({"action": "rename", "column": "Region"})

    assert "needs a new_name" in answer
    assert sent["values"] == []


def test_moving_needs_somewhere_to_go(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    assert "needs to_position" in columns.modify_column.invoke(
        {"action": "move", "column": "Region"}
    )
    assert "not somewhere a column can go" in columns.modify_column.invoke(
        {"action": "move", "column": "Region", "to_position": 99}
    )
    assert sent["requests"] == []
