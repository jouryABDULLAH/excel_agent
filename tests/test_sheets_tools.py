"""Tests for the Google reading tools, without Google.

Each tool imports what it needs from sheets.py by name, so what is replaced
here is the name inside the tool's own module. Patching excel_agent.sheets
would not reach them: the tool holds its own reference, taken at import.

What is asserted on is the string the model reads back, because that is the
whole of what a tool gives it.
"""

import fake_sheets
import pytest
from pydantic import ValidationError

from excel_agent.services.drive import drive_service
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import columns, find, inspect, rows as row_tools, spreadsheets, stats

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


def read(**arguments) -> tuple[str, dict]:
    """Invoke inspect_sheet and give back both halves of what it returns.

    Invoking a content_and_artifact tool with plain arguments hands back the
    content alone, so the call is made the way the agent makes it: as a tool
    call, answered with a ToolMessage.
    """
    message = inspect.inspect_sheet.invoke(
        {
            "name": "inspect_sheet",
            "args": arguments,
            "id": "a-call",
            "type": "tool_call",
        }
    )

    return message.content, message.artifact


@pytest.fixture
def a_sheet(monkeypatch):
    """Point the reading tools at a sheet built by hand.

    Two seams. Which spreadsheet a name means is still resolved through the
    name each tool imported from sheets.py, so that one is replaced inside the
    tool's own module. Everything the sheet itself answers now comes from the
    one shared spreadsheet_service, so those are replaced on the object and
    reach every module at once.
    """

    def use(rows, module=inspect, title=SHEET, spreadsheet=SPREADSHEET):
        monkeypatch.setattr(
            module, "resolve_spreadsheet", lambda name=None: ("an-id", spreadsheet)
        )

        properties = {"title": title, "sheetId": 0}

        monkeypatch.setattr(
            spreadsheet_service, "resolve_sheet", lambda id, name=None: properties
        )
        monkeypatch.setattr(spreadsheet_service, "read_sheet", lambda id, name: rows)
        # A sheet with no charts on it. Without this, reading one would go out
        # to Google to ask, which is the one thing these tests must not do.
        monkeypatch.setattr(
            spreadsheet_service, "list_charts", lambda id, name=None: []
        )
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

    content, artifact = read(max_rows=2)

    # Two rows of the five, and only those two.
    assert "| 2 | ORD-1001 | North | 1 | Laptop |" in content
    assert "| 4 |" not in content

    # Where the rest is lives in the artifact rather than in the content. It
    # is the application's business how a read is continued, and a sentence
    # telling the model to call again is instruction, not spreadsheet data.
    assert (artifact["first_returned_row"], artifact["last_returned_row"]) == (2, 3)
    assert artifact["has_more"] is True
    assert artifact["next_start_row"] == 4
    assert artifact["last_data_row"] == 6


def test_reading_past_the_end_says_where_the_data_ends(a_sheet):
    a_sheet(fake_sheets.orders())

    answer = inspect.inspect_sheet.invoke({"start_row": 500})

    assert "The sheet ends at row 6" in answer
    assert "nothing to read from row 500" in answer


def test_asking_for_no_rows_explains_itself(a_sheet):
    a_sheet(fake_sheets.orders())

    assert "max_rows must be at least 1" in inspect.inspect_sheet.invoke(
        {"max_rows": 0}
    )


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

    assert "column names but no rows of data" in inspect.inspect_sheet.invoke({})


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
        drive_service, "search_spreadsheets", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )
    answer = spreadsheets.list_workbooks.invoke({})

    assert "2 spreadsheets:" in answer
    assert "  Sales" in answer
    assert "  Returns" in answer
    # Nothing chosen yet, so the model is told to pick rather than to ask:
    # working out which file the user means is the orchestrator's job.
    assert "No spreadsheet is currently selected" in answer
    # The tool it is sent to has to be one the file manager actually holds.
    # use_spreadsheet is not: naming it here bounced as an unknown tool.
    assert "call resolve_spreadsheet_choice" in answer


class Working:
    """A runtime whose subagent was handed one spreadsheet."""

    def __init__(self, name):
        self.state = {"spreadsheet_name": name}


def test_the_one_being_worked_on_is_marked(monkeypatch):
    monkeypatch.setattr(
        drive_service, "search_spreadsheets", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )

    # Which file is in hand comes from the subagent's state now, so the tool
    # is called the way an agent calls it: with a runtime.
    answer = spreadsheets.list_workbooks.func(None, Working("Returns"))

    assert "Returns (the one being worked on)" in answer
    assert "No spreadsheet has been chosen yet" not in answer


def test_two_files_sharing_a_name_are_flagged_as_unusable(monkeypatch):
    monkeypatch.setattr(
        drive_service, "search_spreadsheets", lambda name=None: [("1", "Budget"), ("2", "Budget")]
    )
    answer = spreadsheets.list_workbooks.invoke({})

    # Naming either would reach neither, so it is said here rather than left
    # for the next call to fail on.
    assert "more than one file has this name" in answer


def test_a_search_that_finds_nothing_says_what_was_looked_for(monkeypatch):
    monkeypatch.setattr(drive_service, "search_spreadsheets", lambda name=None: [])

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
        drive_service, "search_spreadsheets_by_content", lambda text: [("1", "Sales"), ("2", "Returns")]
    )

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    assert '2 spreadsheet(s) hold "quarterly"' in answer
    assert "  Sales" in answer
    # It says nothing about rows, which is what keeps it out of find_data's
    # territory and safe for the orchestrator to hold.
    assert "| row |" not in answer


def test_a_search_does_not_push_a_change_of_file_when_one_is_in_hand(monkeypatch):
    monkeypatch.setattr(
        drive_service, "search_spreadsheets_by_content", lambda text: [("1", "Sales"), ("2", "Returns")]
    )

    answer = spreadsheets.find_spreadsheet.func("quarterly", Working("Sales"))

    # Asking which files mention a word is a question, not a request to move
    # off the spreadsheet already being worked on.
    assert "Sales (the one being worked on)" in answer
    assert "Nothing is being worked on yet" not in answer


def test_a_search_says_what_to_do_next_when_no_file_is_settled(monkeypatch):
    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", lambda text: [("1", "Sales")])
    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    assert "Nothing is being worked on yet" in answer


def test_a_drive_search_that_finds_nothing_says_why_it_might_not_have(monkeypatch):
    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", lambda text: [])

    answer = spreadsheets.find_spreadsheet.invoke({"text": "quarterly"})

    # Drive indexes a file after it is written, so a change made a moment ago
    # is genuinely not findable yet, and that is worth saying.
    assert "indexes a file after it is written" in answer
    assert "whole words" in answer


def test_find_spreadsheet_needs_something_to_look_for(monkeypatch):
    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", lambda text: [("1", "Sales")])

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
    assert "3 more matching row(s) are not displayed" in answer


# Summarising a column


def test_a_column_of_numbers_gets_its_range_and_total(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    answer = stats.sheet_stats.invoke({"column": "Units"})

    assert '"Units" in Sales Orders in TEST - Sales Orders' in answer
    assert "5 filled, 0 blank, 5 different" in answer
    assert "1 to 5, adding up to 15, averaging 3, middle value 3." in answer


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
    assert "$55.00 to $1,200.00, adding up to 1255, averaging 627.5" in answer


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
    assert "50 to 100, adding up to 150, averaging 75, middle value 75." in answer
    assert "2 of them are worked out by a formula" in answer


def test_a_column_that_is_not_there_is_named_with_the_ones_that_are(a_sheet):
    a_sheet(fake_sheets.orders(), module=stats)

    answer = stats.sheet_stats.invoke({"column": "Profit"})

    assert "The requested column does not exist" in answer
    assert "Order ID, Region, Units, Product" in answer


def test_a_sheet_with_no_rows_yet_says_so(a_sheet):
    a_sheet([[fake_sheets.text("Order ID"), fake_sheets.text("Region")]], module=stats)

    assert "has no data rows" in stats.sheet_stats.invoke({"column": "Region"})


# Settling on a spreadsheet is covered in test_spreadsheet_selection.py, which
# is written against the tool as it is now: a Command carrying state rather
# than a sentence, and a name reaching nothing answered with the names that do.


# Numbers are searched the way a sheet shows them


def test_a_number_is_looked_for_in_the_ways_a_sheet_might_show_it(monkeypatch):
    asked = []

    def only_the_formatted_one(text):
        asked.append(text)
        return [("1", "Sales")] if text == "12,240.00" else []

    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", only_the_formatted_one)
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

    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", watch)

    spreadsheets.find_spreadsheet.invoke({"text": "Laptop"})

    # Anything that is not a number costs exactly one call.
    assert asked == ["Laptop"]


def test_a_number_that_is_nowhere_says_what_was_tried(monkeypatch):
    monkeypatch.setattr(drive_service, "search_spreadsheets_by_content", lambda text: [])

    answer = spreadsheets.find_spreadsheet.invoke({"text": "999"})

    assert "999, 999.00" in answer
    assert "the way the sheet displays it" in answer


# Changing rows
#
# These tools reach Google through spreadsheet_service rather than through a
# name imported from sheets.py, so what is stubbed is the service, and what is
# asserted is the call it was asked to make. The 1-based to 0-based arithmetic
# those calls turn into is the service's own, and is tested in
# test_services_spreadsheet.py against a fake Google.


@pytest.fixture
def a_writable_sheet(a_spreadsheet):
    """A sheet to change, recording what the service was asked to do."""

    def use(rows=None):
        return a_spreadsheet(rows=rows, modules=(row_tools,))

    return use


def written(sent: list) -> dict:
    """The cells of the first write, by the range each landed in."""
    updates = next(one for one in sent if one["call"] in ("update_cells", "append_rows"))
    if updates["call"] == "append_rows":
        return updates["values"]

    return {entry["range"]: entry["values"][0][0] for entry in updates["updates"]}


def test_a_new_row_goes_under_the_last_one(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.append_row.invoke({"values": {"Product": "Dock", "Region": "EU"}})

    assert answer["ok"] is True
    assert answer["row"] == 7

    # Where the row goes is worked out here, not left to Google. Handed a
    # range spanning every named column, values.append decided for itself
    # where the table inside it ended, and on a sheet with a second small
    # table sharing the header row it decided wrongly.
    assert written(sent) == {
        "'Sales Orders'!D7:D7": "Dock",
        "'Sales Orders'!B7:B7": "EU",
    }


def test_a_repeated_row_is_one_call_and_one_batch(a_writable_sheet):
    """The model loses count calling append_row once per copy; count moves
    the counting into the tool."""
    sent = a_writable_sheet()

    answer = row_tools.append_row.invoke(
        {"values": {"Product": "Dock"}, "count": 3}
    )

    assert answer["ok"] is True
    assert answer["row"] == 7
    assert answer["last_row_written"] == 9
    assert answer["count"] == 3

    # One write, one range three rows tall - not three writes.
    updates = [one for one in sent if one["call"] == "update_cells"]
    assert len(updates) == 1
    (entry,) = updates[0]["updates"]
    assert entry["range"] == "'Sales Orders'!D7:D9"
    assert entry["values"] == [["Dock"], ["Dock"], ["Dock"]]


def test_repeating_grows_the_grid_by_what_is_missing(a_writable_sheet, monkeypatch):
    # The grid ends at row 8; rows 7..11 need three more made first.
    from excel_agent.services.spreadsheet import spreadsheet_service

    sent = a_writable_sheet()

    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"rowCount": 8, "columnCount": 10},
        },
    )

    answer = row_tools.append_row.invoke(
        {"values": {"Product": "Dock"}, "count": 5}
    )

    assert answer["ok"] is True
    grown = [one for one in sent if one["call"] == "insert_rows"]
    assert len(grown) == 1
    assert grown[0]["start_row"] == 9
    assert grown[0]["count"] == 3


def test_a_count_below_one_is_refused(a_writable_sheet):
    a_writable_sheet()

    answer = row_tools.append_row.invoke(
        {"values": {"Product": "Dock"}, "count": 0}
    )

    assert answer["ok"] is False
    assert answer["error"] == "invalid_count"


def test_a_row_can_be_put_at_a_chosen_position(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.insert_row.invoke({"row": 3, "values": {"Region": "West"}})

    assert answer["ok"] is True
    assert answer["row_numbers_changed"] is True
    # The gap is made first, then filled: filling first would overwrite row 3.
    assert [one["call"] for one in sent] == ["insert_rows", "update_cells"]
    assert sent[0]["start_row"] == 3
    assert written(sent) == {"'Sales Orders'!B3:B3": "West"}


def test_editing_writes_only_the_columns_given(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 3, "values": {"Region": "West"}})

    assert answer["ok"] is True
    assert answer["updated_columns"] == ["Region"]
    assert written(sent) == {"'Sales Orders'!B3:B3": "West"}


def test_a_cell_is_cleared_by_writing_nothing_into_it(a_writable_sheet):
    sent = a_writable_sheet()

    row_tools.update_row.invoke({"row": 3, "values": {"Region": None}})

    # There is no way to send "no value", so an empty string is what empties
    # a cell.
    assert written(sent) == {"'Sales Orders'!B3:B3": ""}


def test_removing_a_row_asks_for_that_row_alone(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.delete_row.invoke({"row": 4})

    assert answer["ok"] is True
    assert answer["deleted_rows"] == [4]
    assert answer["row_numbers_changed"] is True
    assert sent[0]["call"] == "delete_rows"
    assert sent[0]["ranges"] == [(4, 4)]


def test_several_rows_are_removed_in_one_call(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.delete_row.invoke({"rows": [5, 3, 4, 6]})

    assert answer["ok"] is True
    assert answer["deleted_rows"] == [3, 4, 5, 6]
    assert answer["deleted_count"] == 4
    # One service call carrying one contiguous range.
    assert [one["call"] for one in sent] == ["delete_rows"]
    assert sent[0]["ranges"] == [(3, 6)]


def test_one_missing_row_stops_the_whole_deletion(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.delete_row.invoke({"rows": [3, 99]})

    # All together or not at all: a bad row number must not let the good
    # ones vanish around it.
    assert answer["ok"] is False
    assert answer["error"] == "row_not_found"
    assert answer["rows_not_found"] == [99]
    assert sent == []


def test_the_same_values_land_in_every_named_row(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke(
        {"rows": [3, 5], "values": {"Region": "West"}}
    )

    assert answer["ok"] is True
    assert answer["rows"] == [3, 5]
    assert written(sent) == {
        "'Sales Orders'!B3:B3": "West",
        "'Sales Orders'!B5:B5": "West",
    }


def test_naming_rows_both_ways_at_once_is_refused(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.delete_row.invoke({"row": 3, "rows": [4]})

    assert answer["error"] == "conflicting_rows"
    assert sent == []


def test_moving_a_row_says_where_it_came_from_and_went_to(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 2, "to_row": 5})

    assert (answer["from_row"], answer["to_row"]) == (2, 5)
    assert answer["row_numbers_changed"] is True
    assert sent[0]["call"] == "move_row"
    assert (sent[0]["row"], sent[0]["to_row"]) == (2, 5)


def test_a_row_past_the_data_is_written_and_said_out_loud(a_writable_sheet):
    """This used to be refused. Sheets lets anyone type in row 9999, so the
    tool does too -- and says where the write landed, because a row number
    that far out is usually a mistake and must not happen quietly."""
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 9999, "values": {"Region": "EU"}})

    assert answer["ok"] is True
    assert answer["past_end_of_data"] is True
    assert answer["empty_rows_above"] == 9992
    assert answer["last_data_row"] == 6
    assert written(sent) == {"'Sales Orders'!B9999:B9999": "EU"}


def test_a_row_just_after_the_data_is_ordinary(a_writable_sheet):
    a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 7, "values": {"Region": "EU"}})

    # The next row down is where an append lands; nothing to remark on.
    assert answer["ok"] is True
    assert "past_end_of_data" not in answer


def test_the_header_row_is_not_a_row_that_can_be_changed(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 1, "values": {"Region": "EU"}})

    assert answer["error"] == "row_not_found"
    assert sent == []


def test_an_unknown_column_is_refused_and_nothing_is_sent(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.append_row.invoke({"values": {"Profit": 10}})

    assert answer["error"] == "unknown_columns"
    assert answer["unknown_columns"] == ["Profit"]
    # Named, so the model can correct itself rather than guess again.
    assert "Region" in answer["available_columns"]
    assert sent == []


def test_editing_needs_something_to_change(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 2, "values": {}})

    assert answer["error"] == "no_values"
    assert sent == []


def test_moving_a_row_to_where_it_already_is_changes_nothing(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 3, "to_row": 3})

    assert answer["ok"] is True
    assert answer["changed"] is False
    assert sent == []


def test_a_destination_far_below_the_data_is_allowed_and_said_out_loud(
    a_writable_sheet,
):
    """This used to be refused. The destination bound was the last tool
    still refusing to reach past the data."""
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 3, "to_row": 9999})

    assert answer["ok"] is True
    assert answer["past_end_of_data"] is True
    assert answer["empty_rows_above"] == 9992
    assert calls(sent, "move_row")[0]["to_row"] == 9999


def test_a_formula_is_written_as_a_formula(a_writable_sheet):
    sent = a_writable_sheet()

    row_tools.update_row.invoke({"row": 2, "values": {"Units": "=B2*2"}})

    # USER_ENTERED is what makes Google read this as a formula rather than
    # storing the characters.
    write = next(one for one in sent if one["call"] == "update_cells")
    assert write["value_input_option"] == "USER_ENTERED"
    assert written(sent) == {"'Sales Orders'!C2:C2": "=B2*2"}


def test_a_google_failure_comes_back_as_a_structured_error(a_writable_sheet, monkeypatch):
    from excel_agent.services.spreadsheet import spreadsheet_service
    from fake_google import error

    a_writable_sheet()

    def fail(**named):
        raise error(403, "Insufficient permission")

    monkeypatch.setattr(spreadsheet_service, "update_cells", fail)

    answer = row_tools.update_row.invoke({"row": 2, "values": {"Region": "EU"}})

    assert answer["ok"] is False
    assert answer["error"] == "google_api_error"
    assert "Google refused the request" in answer["message"]


# Changing columns


@pytest.fixture
def a_writable_columns_sheet(a_sheet, monkeypatch):
    """A sheet whose columns can be changed, recording what the service is asked.

    The arithmetic each of these turns into is the service's own, and is
    covered against a Google built by hand in test_services_spreadsheet. What
    is checked here is the layer above it: which column a tool decided to act
    on, and whether it acted at all.
    """
    sent: list[dict] = []

    def use(rows=None, width=26):
        a_sheet(rows or fake_sheets.orders(), module=columns)

        monkeypatch.setattr(
            spreadsheet_service,
            "resolve_sheet",
            lambda id, name=None: {
                "title": SHEET,
                "sheetId": 0,
                "gridProperties": {"columnCount": width},
            },
        )

        def recording(name):
            def called(*arguments, **named):
                sent.append({"call": name, **named})
                return {}

            return called

        for name in (
            "insert_columns",
            "delete_columns",
            "move_column",
            "update_cells",
            "copy_paste",
            "repeat_cell",
        ):
            monkeypatch.setattr(spreadsheet_service, name, recording(name))

        return sent

    return use


def calls(sent, name):
    """Every recorded call to one service method."""
    return [one for one in sent if one["call"] == name]


def test_a_new_column_goes_past_the_last_named_one(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.insert_column.invoke({"name": "Profit"})

    # Four named columns, so the new one is the fifth: E, which is empty
    # already, so nothing that was there has to shift.
    assert answer["ok"] is True
    assert answer["position"] == 5
    assert answer["column_letter"] == "E"
    assert answer["column_positions_changed"] is False

    assert calls(sent, "insert_columns")[0]["start_column"] == 5
    assert calls(sent, "update_cells")[0]["updates"] == [
        {"range": "'Sales Orders'!E1:E1", "values": [["Profit"]]}
    ]


def test_a_new_column_put_between_two_others_moves_the_ones_after_it(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.insert_column.invoke({"name": "Profit", "position": 2})

    # Everything from the old second column rightwards is now one place over,
    # which is worth saying: a position read before this call is stale after it.
    assert answer["position"] == 2
    assert answer["column_positions_changed"] is True
    assert calls(sent, "insert_columns")[0]["start_column"] == 2


def test_a_column_can_be_added_without_a_name(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.insert_column.invoke({})

    # No header is written, because none was asked for. Writing an empty
    # string instead would make an unnamed column look like a named one.
    assert answer["ok"] is True
    assert answer["has_header"] is False
    assert calls(sent, "update_cells") == []


def test_renaming_writes_the_header_cell_and_nothing_else(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.rename_column.invoke({"column": "Region", "new_name": "Area"})

    assert answer["ok"] is True
    assert (answer["old_name"], answer["new_name"]) == ("Region", "Area")
    assert calls(sent, "update_cells")[0]["updates"] == [
        {"range": "'Sales Orders'!B1:B1", "values": [["Area"]]}
    ]
    # The values under the header are not touched.
    assert len(calls(sent, "update_cells")) == 1


def test_removing_a_column_takes_the_one_its_header_sits_in(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.delete_column.invoke({"column": "Region"})

    assert answer["deleted_column"] == "Region"
    assert answer["deleted_position"] == 2
    assert answer["column_positions_changed"] is True
    assert calls(sent, "delete_columns")[0]["start_column"] == 2


def test_moving_a_column_names_where_it_came_from_and_went_to(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.move_column.invoke({"column": "Product", "to_position": 1})

    assert (answer["from_position"], answer["to_position"]) == (4, 1)
    assert answer["changed"] is True

    moved = calls(sent, "move_column")[0]
    assert (moved["column"], moved["to_position"]) == (4, 1)


def test_moving_a_column_to_where_it_already_is_changes_nothing(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.move_column.invoke({"column": "Region", "to_position": 2})

    assert answer["ok"] is True
    assert answer["changed"] is False
    assert calls(sent, "move_column") == []


def test_a_row_formula_is_written_over_the_whole_column_at_once(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.set_column_formula.invoke(
        {"column": "Units", "formula": "=A2&B2"}
    )

    assert answer["ok"] is True
    assert answer["mode"] == "fill_down"
    assert (answer["first_row"], answer["last_row"]) == (2, 6)
    assert answer["filled_rows"] == 5

    # One request covering every data row: Sheets shifts =A2&B2 to =A3&B3 as
    # it repeats, which is what writing then copying took two calls to do.
    repeated = calls(sent, "repeat_cell")[0]
    assert repeated["grid_range"] == {
        "sheetId": 0,
        "startRowIndex": 1,
        "endRowIndex": 6,
        "startColumnIndex": 2,
        "endColumnIndex": 3,
    }
    assert repeated["cell"] == {"userEnteredValue": {"formulaValue": "=A2&B2"}}
    assert repeated["fields"] == "userEnteredValue"

    assert calls(sent, "copy_paste") == []
    assert calls(sent, "update_cells") == []


def test_a_spilling_formula_goes_in_the_first_data_row_only(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.set_column_formula.invoke(
        {
            "column": "Units",
            "formula": "=ARRAYFORMULA(A2:A - B2:B)",
            "mode": "spill",
        }
    )

    assert answer["ok"] is True
    assert answer["mode"] == "spill"
    # One cell, not five: five overlapping spills is the #REF! cascade that
    # the old copy-down produced while still reporting success.
    assert (answer["first_row"], answer["last_row"]) == (2, 2)
    assert answer["filled_rows"] == 1

    repeated = calls(sent, "repeat_cell")[0]
    assert repeated["grid_range"]["startRowIndex"] == 1
    assert repeated["grid_range"]["endRowIndex"] == 2

    assert len(calls(sent, "repeat_cell")) == 1


def test_something_that_is_not_a_formula_is_refused(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.set_column_formula.invoke(
        {"column": "Units", "formula": "B2*C2"}
    )

    assert answer["ok"] is False
    assert answer["error"] == "invalid_formula"
    assert sent == []


def test_a_column_that_is_not_there_is_refused(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.rename_column.invoke({"column": "Nonsense", "new_name": "X"})

    assert answer["ok"] is False
    assert answer["error"] == "column_not_found"
    # The names that do exist come back, so the next call can be right.
    assert answer["available_columns"] == ["Order ID", "Region", "Units", "Product"]
    assert sent == []


def test_a_second_column_of_the_same_name_is_allowed(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.insert_column.invoke({"name": "Region"})

    # Two columns may share a header, the way they may in a sheet someone
    # filled in by hand. What that costs is that the name alone no longer
    # reaches one of them, which is what a position is for; the refusal that
    # follows from that is covered in test_column_positions.
    assert answer["ok"] is True
    assert answer["column"] == "Region"
    assert calls(sent, "insert_columns") != []


def test_renaming_needs_a_new_name(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    # new_name has no default, so a call without one never reaches the tool.
    with pytest.raises(ValidationError):
        columns.rename_column.invoke({"column": "Region"})

    assert sent == []


def test_moving_needs_somewhere_a_column_can_go(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    with pytest.raises(ValidationError):
        columns.move_column.invoke({"column": "Region"})

    outside = columns.move_column.invoke({"column": "Region", "to_position": 99})

    assert outside["ok"] is False
    assert outside["error"] == "invalid_position"
    assert sent == []


def a_sheet_with_a_side_table() -> list:
    """The shape the real spreadsheet has, which broke appending.

    A second small table sits off to the right, sharing the header row: four
    table columns, a blank gap, then a heading with nothing under it.
    """
    rows = [
        [
            fake_sheets.text("Order ID"),
            fake_sheets.text("Region"),
            fake_sheets.text("Units"),
            fake_sheets.text("Product"),
            fake_sheets.EMPTY,
            fake_sheets.text("Revenue by Region"),
        ]
    ]

    for order, region, units, product in (
        ("ORD-1001", "North", 1, "Laptop"),
        ("ORD-1002", "South", 2, "Monitor"),
    ):
        rows.append(
            [
                fake_sheets.text(order),
                fake_sheets.text(region),
                fake_sheets.number(units),
                fake_sheets.text(product),
            ]
        )

    return rows


def test_a_row_added_beside_a_second_table_still_lands_in_the_first(
    a_writable_sheet,
):
    """REGRESSION: the row went wherever Google thought the table ended.

    values.append was handed a range spanning every named column, which on
    this shape covers the gap and the heading to the right as well. Google
    decides for itself where the table inside that range stops, and its answer
    was not the table meant: a row landed below the wrong block, or a lone
    value landed away from its column.
    """
    sent = a_writable_sheet(rows=a_sheet_with_a_side_table())

    answer = row_tools.append_row.invoke(
        {"values": {"Order ID": "ORD-1003", "Units": 7}}
    )

    # Two data rows, so the new one is row 4, whatever sits to the right.
    assert answer["ok"] is True
    assert answer["row"] == 4

    # Only the columns named are written, each in its own place. Nothing is
    # written into the gap, or under the heading of the other table.
    assert written(sent) == {
        "'Sales Orders'!A4:A4": "ORD-1003",
        "'Sales Orders'!C4:C4": 7,
    }


# Reaching a column whose header is capitalised differently


def test_a_column_is_found_however_it_was_capitalised(a_writable_columns_sheet):
    """REGRESSION: "profit margin" missed "Profit Margin", and the worker
    fell back to column letters and overwrote the wrong column."""
    sent = a_writable_columns_sheet()

    answer = columns.rename_column.invoke(
        {"column": "region", "new_name": "Area"}
    )

    assert answer["ok"] is True
    # The sheet's own spelling comes back, not the one that was asked for.
    assert answer["old_name"] == "Region"
    assert calls(sent, "update_cells")[0]["updates"] == [
        {"range": "'Sales Orders'!B1:B1", "values": [["Area"]]}
    ]


def test_a_position_matches_a_header_capitalised_differently(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.rename_column.invoke(
        {"column": "REGION", "position": 2, "new_name": "Area"}
    )

    # Naming the same column two ways is agreement, not a mismatch.
    assert answer["ok"] is True
    assert calls(sent, "update_cells")[0]["updates"][0]["range"] == (
        "'Sales Orders'!B1:B1"
    )


def both_spellings() -> list[list[fake_sheets.Cell]]:
    """A sheet whose second and third columns differ only in capitalisation."""
    return [
        [
            fake_sheets.text("Order ID"),
            fake_sheets.text("Region"),
            fake_sheets.text("region"),
            fake_sheets.text("Product"),
        ],
        [
            fake_sheets.text("A-1"),
            fake_sheets.text("West"),
            fake_sheets.text("west"),
            fake_sheets.text("Desk"),
        ],
    ]


def test_the_exact_spelling_wins_when_a_sheet_holds_both(a_writable_columns_sheet):
    a_writable_columns_sheet(rows=both_spellings())

    answer = columns.rename_column.invoke(
        {"column": "region", "new_name": "Area"}
    )

    # Both match once case is ignored, so the one actually written is taken
    # rather than the request being refused as ambiguous.
    assert answer["ok"] is True
    assert answer["position"] == 3
    assert answer["old_name"] == "region"


def test_two_columns_matching_only_by_case_are_still_ambiguous(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet(rows=both_spellings())

    answer = columns.rename_column.invoke(
        {"column": "REGION", "new_name": "Area"}
    )

    # Neither is what was written, so there is nothing to prefer.
    assert answer["ok"] is False
    assert answer["error"] == "ambiguous_column"
    assert sent == []


# Filling a block of rows, each with its own values


def test_a_block_of_rows_is_written_in_one_call(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.fill_rows.invoke(
        {
            "start_row": 3,
            "rows": [
                {"Region": "West", "Units": 1},
                {"Region": "East", "Units": 2},
            ],
        }
    )

    assert answer["ok"] is True
    assert (answer["first_row"], answer["last_row"]) == (3, 4)
    assert answer["rows_written"] == 2

    # One service call for the whole block: the shape that used to be one
    # update_row per row, twenty calls deep, running out of steps part way.
    assert [one["call"] for one in sent] == ["update_cells"]
    assert written(sent) == {
        "'Sales Orders'!B3:B3": "West",
        "'Sales Orders'!C3:C3": 1,
        "'Sales Orders'!B4:B4": "East",
        "'Sales Orders'!C4:C4": 2,
    }


def test_a_column_that_does_not_exist_stops_the_whole_block(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.fill_rows.invoke(
        {
            "start_row": 3,
            "rows": [{"Region": "West"}, {"Nonsense": 1}],
        }
    )

    # Checked across every row before anything is written, so a bad name in
    # the last dict cannot leave the first rows half written.
    assert answer["ok"] is False
    assert answer["error"] == "unknown_columns"
    assert answer["unknown_columns"] == ["Nonsense"]
    assert sent == []


def test_a_block_never_overwrites_the_header(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.fill_rows.invoke(
        {"start_row": 1, "rows": [{"Region": "West"}]}
    )

    assert answer["error"] == "row_not_found"
    assert sent == []


def test_a_block_reaching_past_the_grid_makes_room_first(
    a_writable_sheet, monkeypatch
):
    from excel_agent.services.spreadsheet import spreadsheet_service

    sent = a_writable_sheet()

    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"rowCount": 6, "columnCount": 10},
        },
    )

    answer = row_tools.fill_rows.invoke(
        {"start_row": 6, "rows": [{"Region": "A"}, {"Region": "B"}]}
    )

    assert answer["ok"] is True
    # Row 7 is past a six-row grid, so the room is made before the write.
    assert [one["call"] for one in sent] == ["insert_rows", "update_cells"]
    # And the block landing past the data is said out loud.
    assert answer["rows_past_data"] == 1


def test_a_row_left_empty_in_the_block_is_left_alone(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.fill_rows.invoke(
        {"start_row": 3, "rows": [{"Region": "West"}, {}, {"Region": "East"}]}
    )

    assert answer["ok"] is True
    assert answer["rows_written"] == 3
    # The gap row is skipped rather than blanked.
    assert "'Sales Orders'!B4:B4" not in written(sent)


# Sorting the data rows


def test_sorting_leaves_the_header_where_it_is(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.sort_rows.invoke({"column": "Region"})

    assert answer["ok"] is True
    assert answer["sorted_rows"] == 5
    assert answer["row_numbers_changed"] is True

    sorted_by = calls(sent, "sort_range")[0]
    # Rows 2 to 6, never row 1: the header is not part of the data.
    assert sorted_by["grid_range"]["startRowIndex"] == 1
    assert sorted_by["grid_range"]["endRowIndex"] == 6
    assert sorted_by["by_columns"] == [(2, False)]


def test_sorting_the_other_way_and_breaking_ties(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.sort_rows.invoke(
        {
            "column": "Units",
            "descending": True,
            "then_by": "Product",
        }
    )

    assert answer["ok"] is True
    assert calls(sent, "sort_range")[0]["by_columns"] == [(3, True), (4, False)]


def test_sorting_by_a_column_that_is_not_there_is_refused(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.sort_rows.invoke({"column": "Nonsense"})

    # REGRESSION: with no sort tool at all, the model improvised by deleting
    # the columns and adding them back.
    assert answer["ok"] is False
    assert answer["error"] == "unknown_columns"
    assert answer["available_columns"] == ["Order ID", "Region", "Units", "Product"]
    assert sent == []


# Reaching past the data, the way Sheets allows


def test_a_row_inserted_far_below_the_data_grows_the_grid_first(
    a_writable_sheet, monkeypatch
):
    """This used to be invalid_insert_position. A person can insert a row
    anywhere in Sheets, so the tool does too, making the room it needs."""
    from excel_agent.services.spreadsheet import spreadsheet_service

    sent = a_writable_sheet()

    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"rowCount": 10, "columnCount": 10},
        },
    )

    answer = row_tools.insert_row.invoke(
        {"row": 30, "values": {"Region": "EU"}}
    )

    assert answer["ok"] is True
    assert answer["past_end_of_data"] is True
    assert answer["empty_rows_above"] == 23

    grown = calls(sent, "insert_rows")[0]
    assert (grown["start_row"], grown["count"]) == (11, 20)


def test_a_column_can_be_created_past_the_last_named_one(
    a_writable_columns_sheet,
):
    sent = a_writable_columns_sheet()

    answer = columns.insert_column.invoke({"name": "Notes", "position": 9})

    # Four named columns; position 9 used to be refused as outside the
    # table, though the sheet itself is 26 columns wide.
    assert answer["ok"] is True
    assert answer["position"] == 9
    assert calls(sent, "insert_columns")[0]["start_column"] == 9


def test_a_formula_goes_into_a_table_that_has_only_headers(a_writable_columns_sheet):
    """This used to be refused as no_data_rows. Sheets lets a formula go in
    any cell, and the agent is never stricter than Sheets."""
    sent = a_writable_columns_sheet(
        rows=[
            [
                fake_sheets.text("Order ID"),
                fake_sheets.text("Region"),
                fake_sheets.text("Units"),
                fake_sheets.text("Product"),
            ]
        ]
    )

    answer = columns.set_column_formula.invoke(
        {"column": "Units", "formula": "=A2&B2"}
    )

    assert answer["ok"] is True
    # The first data row, with nothing under the header yet.
    assert (answer["first_row"], answer["last_row"]) == (2, 2)
    assert calls(sent, "repeat_cell")[0]["grid_range"]["startRowIndex"] == 1


def test_sorting_a_table_with_no_rows_changes_nothing_and_says_so(
    a_writable_sheet,
):
    """Sorting an empty selection is a no-op in Sheets, not an error."""
    sent = a_writable_sheet(
        rows=[
            [
                fake_sheets.text("Order ID"),
                fake_sheets.text("Region"),
                fake_sheets.text("Units"),
                fake_sheets.text("Product"),
            ]
        ]
    )

    answer = row_tools.sort_rows.invoke({"column": "Region"})

    assert answer["ok"] is True
    assert answer["sorted_rows"] == 0
    assert answer["changed"] is False
    assert sent == []


def test_a_row_can_be_moved_past_the_end_of_the_data(
    a_writable_sheet, monkeypatch
):
    """This used to be invalid_destination. Dragging a row below the last
    one is ordinary in Sheets, and it is how "send this to the bottom" is
    said."""
    from excel_agent.services.spreadsheet import spreadsheet_service

    sent = a_writable_sheet()

    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"rowCount": 10, "columnCount": 10},
        },
    )

    answer = row_tools.move_row.invoke({"row": 2, "to_row": 9})

    assert answer["ok"] is True
    assert answer["past_end_of_data"] is True
    assert answer["empty_rows_above"] == 2
    # Inside the grid already, so no room had to be made.
    assert [one["call"] for one in sent] == ["move_row"]


def test_a_move_beyond_the_grid_makes_the_room_first(
    a_writable_sheet, monkeypatch
):
    from excel_agent.services.spreadsheet import spreadsheet_service

    sent = a_writable_sheet()

    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"rowCount": 10, "columnCount": 10},
        },
    )

    answer = row_tools.move_row.invoke({"row": 2, "to_row": 14})

    assert answer["ok"] is True
    # moveDimension cannot land outside the grid, so four rows are added.
    grown = calls(sent, "insert_rows")[0]
    assert (grown["start_row"], grown["count"]) == (11, 4)
    assert [one["call"] for one in sent] == ["insert_rows", "move_row"]


def test_a_row_still_cannot_be_moved_onto_the_header(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 3, "to_row": 1})

    assert answer["ok"] is False
    assert answer["error"] == "invalid_destination"
    assert sent == []


def test_a_column_is_read_by_its_letter(a_sheet):
    """REGRESSION, from the traces: sheet_stats(column='E') answered
    column_not_found. A letter is an address now, bounded by the sheet."""
    a_sheet(fake_sheets.orders(), module=inspect)

    answer = inspect.inspect_sheet.invoke({"columns": ["B", "Product"]})

    # B is Region, named by letter alongside a header name -- and shown to
    # the user under the name the sheet holds, not the letter typed.
    assert "| row | Region | Product |" in answer
    assert "| 2 | North | Laptop |" in answer


def test_a_letter_past_the_edge_of_the_sheet_is_still_unknown(a_sheet):
    a_sheet(fake_sheets.orders(), module=inspect)

    answer = inspect.inspect_sheet.invoke({"columns": ["ZZ"]})

    # 702 columns out. A word spelt in letters must not become an address.
    assert "do not exist" in answer
    assert "Order ID, Region, Units, Product" in answer


# A sheet with no header row at all


def headerless() -> list:
    """Three rows of values, no column names anywhere.

    No text in the first row, which is the plain evidence the heuristic
    needs: a row with any text in it might be a header whose later columns
    are named by their year, so it is left alone.
    """
    return [
        [fake_sheets.number(1), fake_sheets.number(10), fake_sheets.number(100)],
        [fake_sheets.number(2), fake_sheets.number(20), fake_sheets.number(200)],
        [fake_sheets.number(3), fake_sheets.number(30), fake_sheets.number(300)],
    ]


def test_a_sheet_with_no_header_is_read_whole(a_sheet):
    """Every row is data. The first used to be eaten as column names, which
    lost a row and named the columns after its values."""
    a_sheet(headerless(), module=inspect)

    answer = inspect.inspect_sheet.invoke({})

    assert "no header row -- name columns by letter" in answer
    assert "3 rows of data" in answer
    # Row 1 is data, and it is still there.
    assert "| 1 | 1 | 10 | 100 |" in answer


def test_a_headerless_column_is_written_by_its_letter(a_writable_sheet):
    sent = a_writable_sheet(rows=headerless())

    answer = row_tools.update_row.invoke({"row": 2, "values": {"B": 99}})

    # This used to be headers_not_found; the sheet has no names, so the
    # letter is the address.
    assert answer["ok"] is True
    assert written(sent) == {"'Sales Orders'!B2:B2": 99}


def test_a_headerless_sheet_says_what_a_column_may_be_called(a_writable_sheet):
    sent = a_writable_sheet(rows=headerless())

    answer = row_tools.update_row.invoke({"row": 2, "values": {"Region": "West"}})

    # "The sheet has: ." tells the model nothing. The letters do.
    assert answer["ok"] is False
    assert answer["error"] == "unknown_columns"
    assert answer["available_columns"][:3] == ["A", "B", "C"]
    assert sent == []


def test_row_one_of_a_headerless_sheet_can_be_changed(a_writable_sheet):
    sent = a_writable_sheet(rows=headerless())

    answer = row_tools.update_row.invoke({"row": 1, "values": {"A": 99}})

    # With no header there is no header to protect, so row 1 is ordinary.
    assert answer["ok"] is True
    assert written(sent) == {"'Sales Orders'!A1:A1": 99}


def test_a_number_column_is_averaged_and_its_middle_found(a_sheet):
    """An average is the most obvious question to ask of a column, and the
    analyst had to work it out itself, which is the hallucination path."""
    a_sheet(fake_sheets.orders(), module=stats)

    message = stats.sheet_stats.invoke(
        {
            "name": "sheet_stats",
            "args": {"column": "Units"},
            "id": "a-call",
            "type": "tool_call",
        }
    )

    # Units are 1, 2, 3, 4, 5.
    assert "averaging 3" in message.content
    assert "middle value 3" in message.content
    assert message.artifact["average"] == 3
    assert message.artifact["median"] == 3
