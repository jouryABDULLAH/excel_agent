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
        spreadsheets, "search", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )
    answer = spreadsheets.list_workbooks.invoke({})

    assert "2 spreadsheets:" in answer
    assert "  Sales" in answer
    assert "  Returns" in answer
    # Nothing chosen yet, so the model is told to pick rather than to ask:
    # working out which file the user means is the orchestrator's job.
    assert "No spreadsheet is currently selected" in answer
    assert "call use_spreadsheet" in answer


class Working:
    """A runtime whose subagent was handed one spreadsheet."""

    def __init__(self, name):
        self.state = {"spreadsheet_name": name}


def test_the_one_being_worked_on_is_marked(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("1", "Sales"), ("2", "Returns")]
    )

    # Which file is in hand comes from the subagent's state now, so the tool
    # is called the way an agent calls it: with a runtime.
    answer = spreadsheets.list_workbooks.func(None, Working("Returns"))

    assert "Returns (the one being worked on)" in answer
    assert "No spreadsheet has been chosen yet" not in answer


def test_two_files_sharing_a_name_are_flagged_as_unusable(monkeypatch):
    monkeypatch.setattr(
        spreadsheets, "search", lambda name=None: [("1", "Budget"), ("2", "Budget")]
    )
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

    answer = spreadsheets.find_spreadsheet.func("quarterly", Working("Sales"))

    # Asking which files mention a word is a question, not a request to move
    # off the spreadsheet already being worked on.
    assert "Sales (the one being worked on)" in answer
    assert "Nothing is being worked on yet" not in answer


def test_a_search_says_what_to_do_next_when_no_file_is_settled(monkeypatch):
    monkeypatch.setattr(spreadsheets, "containing", lambda text: [("1", "Sales")])
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
    assert "3 more matching row(s) are not displayed" in answer


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

    monkeypatch.setattr(spreadsheets, "containing", only_the_formatted_one)
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
    # append lets Google place the row, so what goes out is the table it is
    # being added to rather than a cell reference worked out here.
    appended = next(one for one in sent if one["call"] == "append_rows")
    assert appended["range_name"].startswith("'Sales Orders'!")
    assert "Dock" in str(appended["values"])
    assert "EU" in str(appended["values"])


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
    assert answer["deleted_row"] == 4
    assert answer["row_numbers_changed"] is True
    assert sent[0]["call"] == "delete_rows"
    assert sent[0]["start_row"] == 4


def test_moving_a_row_says_where_it_came_from_and_went_to(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 2, "to_row": 5})

    assert (answer["from_row"], answer["to_row"]) == (2, 5)
    assert answer["row_numbers_changed"] is True
    assert sent[0]["call"] == "move_row"
    assert (sent[0]["row"], sent[0]["to_row"]) == (2, 5)


def test_a_row_that_does_not_exist_is_refused_and_nothing_is_sent(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.update_row.invoke({"row": 9999, "values": {"Region": "EU"}})

    assert answer["error"] == "row_not_found"
    assert (answer["first_data_row"], answer["last_data_row"]) == (2, 6)
    assert sent == []


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


def test_a_destination_outside_the_data_is_refused(a_writable_sheet):
    sent = a_writable_sheet()

    answer = row_tools.move_row.invoke({"row": 3, "to_row": 9999})

    assert answer["error"] == "invalid_destination"
    assert sent == []


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


def test_a_formula_is_copied_down_rather_than_repeated(a_writable_columns_sheet):
    sent = a_writable_columns_sheet()

    answer = columns.set_column_formula.invoke(
        {"column": "Units", "formula": "=A2&B2"}
    )

    assert answer["ok"] is True
    assert (answer["first_row"], answer["last_row"]) == (2, 6)
    assert answer["filled_rows"] == 5

    # The first row is written, then copied: copying is what shifts =A2&B2
    # into =A3&B3 as it goes down. Repeating the text would leave every row
    # reading row 2.
    assert calls(sent, "update_cells")[0]["updates"][0]["range"] == (
        "'Sales Orders'!C2:C2"
    )

    pasted = calls(sent, "copy_paste")[0]
    assert pasted["paste_type"] == "PASTE_FORMULA"
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
