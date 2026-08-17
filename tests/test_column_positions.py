"""Tests for targeting a column by where it physically sits.

Two changes are covered here, and they only make sense together.

inspect_sheet now reports the physical column layout, so a column with no
header is something the model can see rather than a gap it reads straight
over. delete_column now accepts a position, so a column the model can see but
cannot name is something it can act on.

What is asserted is the position that reaches the service, because a column
number off by one deletes the column next to the one meant.
"""

import fake_sheets
import pytest

from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import columns, inspect

SPREADSHEET = "TEST - Employee Attendance"
SHEET = "Attendance"

# Where the unnamed columns are in the sheet built below. Named here because
# every test about them is about these three numbers.
UNNAMED = (2, 6, 11)


def attendance() -> list[list[fake_sheets.Cell]]:
    """A sheet with unnamed columns at positions 2, 6 and 11.

    Shaped like a sheet someone has been editing by hand: a blank column left
    behind between two filled ones. The gaps are inside the table rather than
    off its right hand end, which is what makes them invisible to a header map
    and visible to a physical layout.
    """
    headers = [
        "Employee ID",
        "",
        "Name",
        "Department",
        "Date",
        "",
        "Status",
        "Hours",
        "Notes",
        "Approved By",
        "",
        "Comments",
    ]

    rows = [
        [
            fake_sheets.text(header) if header else fake_sheets.EMPTY
            for header in headers
        ]
    ]

    # Every named column holds something, so "the empty column" can only mean
    # one of the three with no header. A named column left blank would make
    # the phrase ambiguous, and the tests below would stop being about
    # positions and start being about that ambiguity.
    for identifier, name, department, date, status, hours, note, approver in (
        ("EMP-1", "Alice", "Sales", "2026-08-03", "Present", 8, "On time", "Dana"),
        ("EMP-2", "Bilal", "Sales", "2026-08-03", "Absent", 0, "Sick leave", "Dana"),
        ("EMP-3", "Chen", "Support", "2026-08-03", "Present", 7, "Left early", "Omar"),
    ):
        row = [fake_sheets.EMPTY] * 12
        row[0] = fake_sheets.text(identifier)
        row[2] = fake_sheets.text(name)
        row[3] = fake_sheets.text(department)
        row[4] = fake_sheets.date(date)
        row[6] = fake_sheets.text(status)
        row[7] = fake_sheets.number(hours)
        row[8] = fake_sheets.text(note)
        row[9] = fake_sheets.text(approver)
        row[11] = fake_sheets.text("-")
        rows.append(row)

    return rows


@pytest.fixture
def a_sheet_with_gaps(monkeypatch):
    """Point the column and reading tools at a sheet built by hand.

    Unlike the shared a_spreadsheet fixture this one answers resolve_sheet
    with gridProperties, because the physical width of the sheet is what
    delete_column checks a position against, and a fixture that leaves it out
    would never exercise that check.

    Returns the list of calls the service was asked to make, so a test can
    tell a refusal that sent nothing from one that sent the wrong thing.
    """

    def use(rows=None, width=26):
        rows = attendance() if rows is None else rows
        sent: list = []

        properties = {
            "title": SHEET,
            "sheetId": 7,
            "gridProperties": {"rowCount": 1000, "columnCount": width},
        }

        for module in (columns, inspect):
            monkeypatch.setattr(
                module,
                "resolve_spreadsheet",
                lambda name=None: ("an-id", SPREADSHEET),
            )

        monkeypatch.setattr(
            spreadsheet_service, "resolve_sheet", lambda id, name=None: properties
        )
        monkeypatch.setattr(spreadsheet_service, "read_sheet", lambda id, name: rows)
        monkeypatch.setattr(
            spreadsheet_service, "list_charts", lambda id, name=None: []
        )

        def recording(name):
            def called(*arguments, **named):
                sent.append({"call": name, "args": arguments, **named})
                return {}

            return called

        for name in (
            "delete_columns",
            "insert_columns",
            "update_cells",
            "move_column",
            "batch_update",
        ):
            monkeypatch.setattr(spreadsheet_service, name, recording(name))

        return sent

    return use


def read(**arguments) -> tuple[str, dict]:
    """Invoke inspect_sheet and give back both halves of what it returns.

    Invoking a content_and_artifact tool with plain arguments hands back the
    content alone, and the layout lives in the artifact, so the call is made
    the way the agent makes it: as a tool call, answered with a ToolMessage.
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


def deletions(sent: list) -> list[int]:
    """The column positions the service was asked to delete."""
    return [one["start_column"] for one in sent if one["call"] == "delete_columns"]


# What the model can see
#
# A column with no header used to be missing from everything inspect_sheet
# said, so a sheet of twelve columns read as a sheet of nine and the model had
# no way to know the difference.


def test_the_layout_names_every_physical_column_including_the_blank_ones(
    a_sheet_with_gaps,
):
    a_sheet_with_gaps()

    _, artifact = read()

    layout = artifact["column_layout"]

    # Twelve columns, not the nine that have names.
    assert [one["position"] for one in layout] == list(range(1, 13))
    assert [one["position"] for one in layout if one["header"] is None] == list(UNNAMED)
    assert layout[0] == {"position": 1, "letter": "A", "header": "Employee ID"}
    assert layout[1] == {"position": 2, "letter": "B", "header": None}


def test_the_letters_are_the_ones_the_sheet_shows(a_sheet_with_gaps):
    a_sheet_with_gaps()

    _, artifact = read()

    # The letter is what the user is looking at while they talk about the
    # column, so it has to be the physical letter and not a count of the
    # columns that happen to have names: K, not H.
    letters = {one["position"]: one["letter"] for one in artifact["column_layout"]}
    assert letters[6] == "F"
    assert letters[11] == "K"
    assert letters[12] == "L"


def test_the_blank_columns_are_in_what_the_model_reads(a_sheet_with_gaps):
    a_sheet_with_gaps()

    content, _ = read()

    # The artifact is for the application; this line is the whole of what the
    # model gets, so the gaps have to be spelled out in it.
    assert "Physical column layout" in content
    assert "B (position 2): [unnamed]" in content
    assert "F (position 6): [unnamed]" in content
    assert "K (position 11): [unnamed]" in content
    assert "A (position 1): Employee ID" in content


def test_the_named_columns_are_still_listed_by_name_only(a_sheet_with_gaps):
    a_sheet_with_gaps()

    content, artifact = read()

    # The layout is an addition, not a replacement: the table of data is still
    # keyed by header, because a blank column holds nothing to show.
    assert artifact["headers"] == [
        "Employee ID",
        "Name",
        "Department",
        "Date",
        "Status",
        "Hours",
        "Notes",
        "Approved By",
        "Comments",
    ]
    assert (
        "| row | Employee ID | Name | Department | Date | Status | Hours | "
        "Notes | Approved By | Comments |"
    ) in content
    assert "| 2 | EMP-1 | Alice | Sales |" in content


def test_a_sheet_with_no_data_rows_still_says_where_its_columns_are(
    a_sheet_with_gaps,
):
    a_sheet_with_gaps(rows=attendance()[:1])

    content, artifact = read()

    # A sheet whose columns were just created is exactly when the layout is
    # worth having, and it is a separate return path in the tool.
    assert "no rows of data" in content
    assert "B (position 2): [unnamed]" in content
    assert len(artifact["column_layout"]) == 12


def test_asking_for_some_columns_does_not_shorten_the_layout(a_sheet_with_gaps):
    a_sheet_with_gaps()

    _, artifact = read(columns=["Name", "Status"])

    # Which columns to show is the caller's choice; where the columns are is a
    # fact about the sheet, and narrowing one must not narrow the other.
    assert artifact["headers"] == ["Name", "Status"]
    assert len(artifact["column_layout"]) == 12


def test_the_layout_stops_at_the_last_named_column(a_sheet_with_gaps):
    rows = attendance()
    rows[0][11] = fake_sheets.EMPTY

    a_sheet_with_gaps(rows=rows)

    _, artifact = read()

    # Position 12 has no header now, so the rightmost named column is 10 and
    # the layout ends there. Columns 11 and 12 are physically present in a
    # grid 26 wide and are not reported, which is the known edge of this: a
    # blank column past the end of the table cannot be seen, and so cannot be
    # deleted by a position read from here.
    assert [one["position"] for one in artifact["column_layout"]] == list(range(1, 11))


def test_two_columns_sharing_a_header_both_appear(a_sheet_with_gaps):
    rows = attendance()
    rows[0][11] = fake_sheets.text("Notes")

    a_sheet_with_gaps(rows=rows)

    _, artifact = read()

    at = {one["position"]: one["header"] for one in artifact["column_layout"]}

    # A header map holds one entry per name, so the second Notes was invisible
    # before and the name reached only the later of the two. The layout shows
    # both, which is the only thing that makes the pair addressable.
    assert at[9] == "Notes"
    assert at[12] == "Notes"
    assert artifact["headers"].count("Notes") == 1


# Deleting by name, which is what worked before


def test_a_named_column_is_deleted_at_the_position_its_header_sits_in(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "Status"})

    assert answer["ok"] is True
    assert answer["deleted_column"] == "Status"
    # Status is the fifth column with a name and the seventh column in the
    # sheet. Counting names would delete Hours.
    assert answer["deleted_position"] == 7
    assert deletions(sent) == [7]


def test_a_name_is_taken_without_the_spaces_around_it(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "  Status  "})

    assert answer["deleted_position"] == 7
    assert deletions(sent) == [7]


def test_a_name_that_is_not_there_is_refused_with_the_names_that_are(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "Salary"})

    assert answer["ok"] is False
    assert answer["error"] == "column_not_found"
    assert "Status" in answer["available_columns"]
    assert sent == []


def test_deleting_a_column_says_the_positions_have_moved(a_sheet_with_gaps):
    a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "Name"})

    # Everything to the right of a deleted column shifts left, so a position
    # read before this call is stale after it.
    assert answer["column_positions_changed"] is True


# Deleting by position, which is what is new


@pytest.mark.parametrize("position", UNNAMED)
def test_a_column_with_no_header_is_deleted_by_its_position(
    a_sheet_with_gaps, position
):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"position": position})

    assert answer["ok"] is True
    assert answer["deleted_position"] == position
    # Nothing is invented for a column that has no name. A header made up here
    # would be reported back to the user as the column that was removed.
    assert answer["deleted_column"] is None
    assert deletions(sent) == [position]


def test_a_position_holding_a_named_column_reports_the_name_it_found(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"position": 4})

    # Asked only for a number, the answer still says what was there, so the
    # confirmation the user reads names the column rather than a letter.
    assert answer["deleted_column"] == "Department"
    assert deletions(sent) == [4]


def test_a_name_and_a_position_that_agree_are_accepted(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "Hours", "position": 8})

    assert answer["ok"] is True
    assert answer["deleted_column"] == "Hours"
    assert deletions(sent) == [8]


def test_a_name_and_a_position_that_disagree_delete_nothing(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "Hours", "position": 6})

    # Two identifiers pointing at different columns is a model that has lost
    # track of the sheet, and either one could be the wrong one. Refusing is
    # the only answer that cannot delete the wrong column.
    assert answer["ok"] is False
    assert answer["error"] == "column_position_mismatch"
    assert answer["column_position"] == 8
    assert answer["requested_position"] == 6
    assert sent == []


def test_neither_a_name_nor_a_position_deletes_nothing(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({})

    assert answer["error"] == "missing_column"
    assert sent == []


def test_a_blank_name_with_a_position_is_taken_as_the_position(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "   ", "position": 2})

    # A model with no header to give may send an empty string alongside the
    # position rather than leaving the argument out. That is the position it
    # meant, not a missing name.
    assert answer["ok"] is True
    assert answer["deleted_position"] == 2
    assert deletions(sent) == [2]


def test_a_blank_name_and_no_position_deletes_nothing(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"column": "   "})

    assert answer["error"] == "missing_column"
    assert sent == []


@pytest.mark.parametrize("position", (0, -1, -99))
def test_a_position_below_the_first_column_is_refused(a_sheet_with_gaps, position):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"position": position})

    # Columns count from 1 everywhere the model can see. A 0 sent through
    # would become -1 in the request Google is given.
    assert answer["error"] == "invalid_position"
    assert sent == []


def test_a_position_past_the_right_edge_of_the_sheet_is_refused(a_sheet_with_gaps):
    sent = a_sheet_with_gaps(width=12)

    answer = columns.delete_column.invoke({"position": 13})

    assert answer["error"] == "invalid_position"
    assert (answer["first_position"], answer["last_position"]) == (1, 12)
    assert sent == []


def test_a_blank_column_past_the_last_named_one_can_still_be_deleted(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps(width=26)

    answer = columns.delete_column.invoke({"position": 20})

    # The sheet's table ends at column 12, but the grid is 26 wide, and the
    # tool deletes physical columns rather than table columns. A position
    # inside the grid is a real column whether or not the table reaches it.
    assert answer["ok"] is True
    assert answer["deleted_column"] is None
    assert deletions(sent) == [20]


def test_the_first_column_can_be_deleted_by_position(a_sheet_with_gaps):
    sent = a_sheet_with_gaps()

    answer = columns.delete_column.invoke({"position": 1})

    # 1 is the boundary the guard is written around, so it is worth saying
    # that it is inside it.
    assert answer["ok"] is True
    assert answer["deleted_column"] == "Employee ID"
    assert deletions(sent) == [1]


# Three blank columns, one at a time
#
# The case this was built for. Deleting one of them moves the other two, and
# the model is told so by column_positions_changed; nothing about the tool
# stops a stale position being sent, which is what these two check.


def test_deleting_the_blank_columns_from_the_right_keeps_the_others_where_they_are(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps()

    for position in reversed(UNNAMED):
        assert columns.delete_column.invoke({"position": position})["ok"] is True

    # Right to left, because a deletion only moves the columns after it. The
    # sheet these read is built by hand and does not shrink, so this is about
    # the positions asked for and not about what the sheet becomes.
    assert deletions(sent) == [11, 6, 2]


def test_positions_read_before_a_deletion_are_not_corrected_afterwards(
    a_sheet_with_gaps,
):
    sent = a_sheet_with_gaps()

    columns.delete_column.invoke({"position": 2})
    columns.delete_column.invoke({"position": 6})

    # Once column 2 is gone the blank column that was 6 is at 5, and 6 holds
    # Status. The tool has no memory of the earlier call and sends 6 as given,
    # which is why left to right needs a fresh read between the deletions.
    assert deletions(sent) == [2, 6]


# When something goes wrong underneath


def test_a_name_that_reaches_no_spreadsheet_comes_back_as_a_refusal(
    a_sheet_with_gaps, monkeypatch
):
    sent = a_sheet_with_gaps()

    def refuse(name=None):
        raise ValueError('There is no spreadsheet called "Nonsense".')

    monkeypatch.setattr(columns, "resolve_spreadsheet", refuse)

    answer = columns.delete_column.invoke({"position": 2, "spreadsheet": "Nonsense"})

    assert answer["error"] == "invalid_request"
    assert "no spreadsheet called" in answer["message"]
    assert sent == []


def test_a_google_refusal_comes_back_as_a_structured_error(
    a_sheet_with_gaps, monkeypatch
):
    from fake_google import error

    a_sheet_with_gaps()

    def fail(**named):
        raise error(403, "Insufficient permission")

    monkeypatch.setattr(spreadsheet_service, "delete_columns", fail)

    answer = columns.delete_column.invoke({"position": 2})

    assert answer["ok"] is False
    assert answer["error"] == "google_api_error"
    assert "Google refused the request" in answer["message"]


# What the model is offered
#
# The tool's own description is the whole of what the model knows about it
# before calling, so the position argument existing is part of the change.


def test_the_tool_offers_a_position_as_well_as_a_name():
    assert "position" in columns.delete_column.args
    assert "column" in columns.delete_column.args


def test_neither_argument_is_required():
    # Both optional is what lets a blank column be deleted at all: a required
    # column would force the model to invent a header for it.
    required = columns.delete_column.tool_call_schema.model_json_schema().get(
        "required", []
    )
    assert required == []


def test_the_description_says_where_a_position_counts_from():
    described = columns.delete_column.description

    # Counting from 0 is the mistake this argument invites, and the docstring
    # is the only place the model is told which way it goes.
    assert "Position counts from 1" in described
    assert "unnamed columns" in described
