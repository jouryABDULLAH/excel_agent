"""Tests for the arithmetic SpreadsheetService does on the way to Google.

The tools count rows and columns from 1, the way the sheet shows them. Google
counts from 0 and leaves the end of a range out. This module is the only place
that knows both, so a mistake here deletes the row next to the one meant, and
no test above it would notice: a tool asks for row 4 and is told row 4 went.

That arithmetic used to sit in the row tools and was covered there. It moved
when they were rewritten against the service, so it is covered here now,
against a Google built by hand.
"""

import pytest

from excel_agent.services.spreadsheet import SpreadsheetService

from fake_google import Endpoint, FakeGoogle


def a_service(answers=None):
    """A service talking to a Google that records what it was asked."""
    pretend = FakeGoogle(spreadsheets=Endpoint(answers=answers or {}))
    return SpreadsheetService(google=pretend), pretend


def only_request(pretend) -> dict:
    """The single request of the last batchUpdate."""
    method, asked = pretend.spreadsheets_endpoint.calls[-1]
    assert method == "batchUpdate"
    requests = asked["body"]["requests"]
    assert len(requests) == 1
    return requests[0]


# Deleting


def test_deleting_one_row_asks_for_that_row_alone():
    service, pretend = a_service()

    service.delete_rows("an-id", sheet_id=0, ranges=[(4, 4)])

    # Row 4 alone: counting from 0 makes the start 3, and the end-exclusive
    # index 4. An end of 3 would delete row 3 instead.
    assert only_request(pretend) == {
        "deleteDimension": {
            "range": {
                "sheetId": 0,
                "dimension": "ROWS",
                "startIndex": 3,
                "endIndex": 4,
            }
        }
    }


def test_deleting_a_run_of_rows_covers_both_ends():
    service, pretend = a_service()

    service.delete_rows("an-id", sheet_id=0, ranges=[(4, 6)])

    deleted = only_request(pretend)["deleteDimension"]["range"]
    assert (deleted["startIndex"], deleted["endIndex"]) == (3, 6)


def test_separate_ranges_are_deleted_bottom_up_in_one_batch():
    service, pretend = a_service()

    service.delete_rows("an-id", sheet_id=0, ranges=[(3, 5), (9, 9)])

    # One request, so Google applies all of it or none of it; and the lower
    # range last, or deleting 3-5 first would shift what 9 names.
    (method, sent), = pretend.spreadsheets_endpoint.calls
    starts = [
        one["deleteDimension"]["range"]["startIndex"]
        for one in sent["body"]["requests"]
    ]
    assert starts == [8, 2]


@pytest.mark.parametrize("start,end", [(0, 4), (4, 3)])
def test_a_range_that_makes_no_sense_is_refused_before_google_sees_it(start, end):
    service, pretend = a_service()

    with pytest.raises(ValueError):
        service.delete_rows("an-id", sheet_id=0, ranges=[(start, end)])

    assert pretend.spreadsheets_endpoint.calls == []


# Inserting


def test_inserting_makes_the_gap_above_the_row_asked_for():
    service, pretend = a_service()

    service.insert_rows("an-id", sheet_id=0, start_row=3)

    inserted = only_request(pretend)["insertDimension"]
    assert inserted["range"]["startIndex"] == 2
    assert inserted["range"]["endIndex"] == 3
    # Inheriting from below rather than above, which is the only choice there
    # is when the new row is row 1.
    assert inserted["inheritFromBefore"] is False


def test_inserting_several_rows_widens_the_gap():
    service, pretend = a_service()

    service.insert_rows("an-id", sheet_id=0, start_row=3, count=4)

    gap = only_request(pretend)["insertDimension"]["range"]
    assert (gap["startIndex"], gap["endIndex"]) == (2, 6)


def test_inserting_nothing_is_refused():
    service, pretend = a_service()

    with pytest.raises(ValueError):
        service.insert_rows("an-id", sheet_id=0, start_row=3, count=0)

    assert pretend.spreadsheets_endpoint.calls == []


# Moving, which is where the counting is easiest to get wrong


def test_moving_a_row_down_lands_it_where_it_was_asked_for():
    service, pretend = a_service()

    service.move_row("an-id", sheet_id=0, row=2, to_row=5)

    # Google measures the destination against the grid before the row is
    # lifted out, so moving down asks for the number it should end up at.
    moved = only_request(pretend)["moveDimension"]
    assert moved["source"]["startIndex"] == 1
    assert moved["source"]["endIndex"] == 2
    assert moved["destinationIndex"] == 5


def test_moving_a_row_up_lands_it_where_it_was_asked_for():
    service, pretend = a_service()

    service.move_row("an-id", sheet_id=0, row=5, to_row=2)

    # Moving up, the destination is the row before the one asked for, because
    # nothing above it has been lifted out yet. Using 2 here would leave the
    # row one place below where it was wanted.
    moved = only_request(pretend)["moveDimension"]
    assert moved["source"]["startIndex"] == 4
    assert moved["destinationIndex"] == 1


def test_moving_a_row_onto_itself_is_refused():
    service, pretend = a_service()

    with pytest.raises(ValueError):
        service.move_row("an-id", sheet_id=0, row=3, to_row=3)

    assert pretend.spreadsheets_endpoint.calls == []


# Columns, which count the same way and are as easy to get wrong


def test_deleting_one_column_asks_for_that_column_alone():
    service, pretend = a_service()

    service.delete_columns("an-id", sheet_id=0, start_column=2)

    assert only_request(pretend) == {
        "deleteDimension": {
            "range": {
                "sheetId": 0,
                "dimension": "COLUMNS",
                "startIndex": 1,
                "endIndex": 2,
            }
        }
    }


def test_deleting_a_run_of_columns_covers_both_ends():
    service, pretend = a_service()

    service.delete_columns("an-id", sheet_id=0, start_column=2, end_column=4)

    deleted = only_request(pretend)["deleteDimension"]["range"]
    assert (deleted["startIndex"], deleted["endIndex"]) == (1, 4)


def test_inserting_makes_the_gap_left_of_the_column_asked_for():
    service, pretend = a_service()

    service.insert_columns("an-id", sheet_id=0, start_column=3)

    gap = only_request(pretend)["insertDimension"]["range"]
    assert (gap["startIndex"], gap["endIndex"]) == (2, 3)


def test_moving_a_column_right_lands_it_where_it_was_asked_for():
    service, pretend = a_service()

    service.move_column("an-id", sheet_id=0, column=2, to_position=5)

    # As with rows: Google reads the destination against the grid before the
    # column is lifted out, so moving right asks for the number it ends at.
    moved = only_request(pretend)["moveDimension"]
    assert moved["source"]["startIndex"] == 1
    assert moved["source"]["endIndex"] == 2
    assert moved["destinationIndex"] == 5


def test_moving_a_column_left_lands_it_where_it_was_asked_for():
    service, pretend = a_service()

    service.move_column("an-id", sheet_id=0, column=5, to_position=2)

    # Moving left it is the place before the one asked for, because nothing to
    # the left of it has been lifted out yet. Using 2 would land it one place
    # to the right of where it was wanted.
    moved = only_request(pretend)["moveDimension"]
    assert moved["source"]["startIndex"] == 4
    assert moved["destinationIndex"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"column": 0, "to_position": 2},
        {"column": 2, "to_position": 0},
        {"column": 3, "to_position": 3},
    ],
)
def test_a_column_move_that_makes_no_sense_never_reaches_google(arguments):
    service, pretend = a_service()

    with pytest.raises(ValueError):
        service.move_column("an-id", sheet_id=0, **arguments)

    assert pretend.spreadsheets_endpoint.calls == []


def test_a_repeated_formula_is_sent_as_a_formula_not_as_text():
    service, pretend = a_service()

    service.repeat_cell(
        "an-id",
        grid_range={"sheetId": 0, "startRowIndex": 1, "endRowIndex": 6},
        cell={"userEnteredValue": {"formulaValue": "=A2&B2"}},
        fields="userEnteredValue",
    )

    # formulaValue is what makes Sheets shift =A2&B2 to =A3&B3 for each row it
    # is repeated into; a stringValue would put the same text in every cell.
    repeated = only_request(pretend)["repeatCell"]
    assert repeated["cell"]["userEnteredValue"]["formulaValue"] == "=A2&B2"
    assert repeated["range"]["endRowIndex"] == 6
    assert repeated["fields"] == "userEnteredValue"


def test_a_formula_is_pasted_as_a_formula_rather_than_as_its_result():
    service, pretend = a_service()

    service.copy_paste(
        "an-id",
        source={"sheetId": 0, "startRowIndex": 1, "endRowIndex": 2},
        destination={"sheetId": 0, "startRowIndex": 2, "endRowIndex": 6},
        paste_type="PASTE_FORMULA",
    )

    # PASTE_FORMULA is what shifts =A2 into =A3 on the way down. Pasting the
    # value instead would leave every row holding the first row's answer.
    pasted = only_request(pretend)["copyPaste"]
    assert pasted["pasteType"] == "PASTE_FORMULA"
    assert pasted["source"]["startRowIndex"] == 1
    assert pasted["destination"]["endRowIndex"] == 6


# What a structural change does to what is remembered


def test_a_structural_change_forgets_the_sheets_it_may_have_moved():
    service, pretend = a_service(
        answers={
            "get": {
                "sheets": [
                    {
                        "properties": {
                            "sheetId": 0,
                            "title": "Sales",
                            "gridProperties": {"rowCount": 10, "columnCount": 4},
                        }
                    }
                ]
            }
        }
    )

    service.list_sheets("an-id")
    service.list_sheets("an-id")
    assert len(pretend.spreadsheets_endpoint.calls) == 1

    service.delete_rows("an-id", sheet_id=0, ranges=[(4, 4)])
    service.list_sheets("an-id")

    # Read, write, read again: the write sent the next reader back to Google
    # rather than answering out of what was true before it.
    assert [method for method, _ in pretend.spreadsheets_endpoint.calls] == [
        "get",
        "batchUpdate",
        "get",
    ]


def test_reading_a_sheet_asks_for_the_fields_a_cell_is_built_from():
    """Losing one field from the mask leaves that part of every cell empty,
    with nothing raising to say so."""
    from excel_agent.services.spreadsheet import GRID_FIELDS

    service, pretend = a_service(answers={"get": {"sheets": []}})

    service.read_sheet("an-id", "Sales")

    (method, sent), = pretend.spreadsheets_endpoint.calls
    assert method == "get"
    assert sent["fields"] == GRID_FIELDS
    assert sent["includeGridData"] is True

    for wanted in ("formattedValue", "userEnteredValue", "effectiveValue",
                   "effectiveFormat(numberFormat(type))"):
        assert wanted in GRID_FIELDS
