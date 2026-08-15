"""Tests for what sheets.py still does now that the services hold the rest.

After the refactor this module is two things: a thin pass through to the
service layer, and the caching of what a spreadsheet's sheets are. The pass
through is easy to get subtly wrong in ways no other test would notice, and
the caching is where a refactor of this shape does real damage, so both are
gone over here in detail.

The tools import from this module by name, so its surface is a contract with
nine other files. That is checked here too.
"""

import pytest

from excel_agent import config, sheets

from fake_google import Endpoint, FakeGoogle, Request, error


ONE_SHEET = {
    "sheets": [
        {
            "properties": {
                "sheetId": 0,
                "title": "Sales Orders",
                "gridProperties": {"rowCount": 10, "columnCount": 4},
            }
        }
    ]
}

TWO_SHEETS = {
    "sheets": [
        {"properties": {"sheetId": 0, "title": "Sales Orders", "gridProperties": {}}},
        {"properties": {"sheetId": 7, "title": "Notes", "gridProperties": {}}},
    ]
}


# Reaching Google through the service layer


def test_the_two_clients_come_from_the_shared_google(fake_google):
    pretend = fake_google()

    # Nothing builds a client of its own any more: both come from the one
    # object, which is what makes a single sign in serve the whole process.
    assert sheets.sheets().spreadsheets() is pretend.spreadsheets_endpoint
    assert sheets.drive().files() is pretend.files_endpoint


def test_a_prepared_call_is_sent_through_execute_so_it_is_retried(fake_google):
    pretend = fake_google()
    request = pretend.spreadsheets_endpoint.get(spreadsheetId="an-id")

    sheets.with_retries(request)

    # Losing this would lose every retry in the project at once, silently:
    # the calls would still work, right up until Google says slow down.
    assert pretend.executed == [request]


def test_what_google_answers_comes_straight_back(fake_google):
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": {"answered": True}}))

    answer = sheets.with_retries(pretend.sheets.spreadsheets().get())

    assert answer == {"answered": True}


def test_an_error_from_google_is_not_swallowed_on_the_way_through(fake_google):
    pretend = fake_google(
        spreadsheets=Endpoint(failures={"get": [error(404, "no such file")]})
    )

    # A tool turns this into a sentence with readable(). Swallowing it here
    # would have the tool report success for a call that never happened.
    with pytest.raises(Exception):
        sheets.with_retries(pretend.sheets.spreadsheets().get())


# Writing


def test_a_batch_of_changes_goes_out_as_one_request(fake_google):
    pretend = fake_google()
    requests = [{"deleteDimension": {}}, {"insertDimension": {}}]

    sheets.batch("an-id", requests)

    method, asked = pretend.spreadsheets_endpoint.calls[-1]
    assert method == "batchUpdate"
    assert asked["spreadsheetId"] == "an-id"
    # One call rather than several means the changes land together, and one
    # request against the quota instead of one each.
    assert asked["body"] == {"requests": requests}
    assert len(pretend.executed) == 1


def test_values_are_written_the_way_a_person_would_have_typed_them(fake_google):
    pretend = fake_google()
    data = [{"range": "'Sales'!A2:B2", "values": [["=B2*C2", "5"]]}]

    sheets.write_values("an-id", data)

    # Values go through their own endpoint, which is why this exists alongside
    # batch() rather than inside it.
    method, asked = pretend.spreadsheets_endpoint.values().calls[-1]
    assert method == "batchUpdate"
    # USER_ENTERED is what makes "=B2*C2" a formula and "5" a number, rather
    # than both being stored as text.
    assert asked["body"]["valueInputOption"] == "USER_ENTERED"
    assert asked["body"]["data"] == data


def test_a_write_hands_back_what_google_said(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"batchUpdate": {"replies": [{}]}}))

    assert sheets.batch("an-id", [{}]) == {"replies": [{}]}
    assert sheets.write_values("an-id", [{}]) == {"replies": [{}]}


@pytest.mark.parametrize("write", ("batch", "write_values"))
def test_every_write_forgets_what_it_may_have_moved(write, monkeypatch, fake_google):
    fake_google()
    forgotten: list[str] = []
    monkeypatch.setattr(sheets, "forget", forgotten.append)

    getattr(sheets, write)("an-id", [{}])

    assert forgotten == ["an-id"]


# Reading a sheet


ONE_ROW = {
    "sheets": [
        {
            "data": [
                {
                    "rowData": [
                        {
                            "values": [
                                {
                                    "formattedValue": "Order ID",
                                    "effectiveValue": {"stringValue": "Order ID"},
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    ]
}


def test_reading_a_sheet_asks_for_the_fields_a_cell_is_built_from(fake_google):
    """The only test that calls the real grid().

    Every tool test replaces it through the a_spreadsheet fixture, so nothing
    exercised this path. That is how GRID_FIELDS came to be deleted from this
    module while grid() still named it: the suite stayed green and the agent
    died on the first read, in front of the user. A NameError here now.
    """
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": ONE_ROW}))

    rows = sheets.grid("an-id", "Sales Orders")

    method, asked = pretend.spreadsheets_endpoint.calls[-1]
    assert method == "get"
    assert asked["includeGridData"] is True

    # The four things a Cell is made of. Losing one from the mask leaves that
    # part of every cell empty, with nothing raising to say so.
    for field in (
        "formattedValue",
        "userEnteredValue",
        "effectiveValue",
        "numberFormat",
    ):
        assert field in asked["fields"], field

    assert rows[0][0].displayed == "Order ID"


def test_the_field_mask_is_the_services_own(fake_google):
    """Two copies of this drifted apart once, and the loud half was the lucky one."""
    from excel_agent.services.spreadsheet import GRID_FIELDS

    assert sheets.GRID_FIELDS is GRID_FIELDS


# What is remembered about a spreadsheet, and what is not


def test_the_sheets_in_a_spreadsheet_are_read_once_and_kept(fake_google):
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": TWO_SHEETS}))

    first = sheets.sheets_in("an-id")
    second = sheets.sheets_in("an-id")

    assert first is second
    assert len(pretend.spreadsheets_endpoint.calls) == 1


def test_the_sheets_come_back_by_title_with_their_id_and_size(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"get": TWO_SHEETS}))

    found = sheets.sheets_in("an-id")

    # batchUpdate works in numeric ids and never in titles, so this mapping is
    # what stands between the name a person uses and the number Google wants.
    assert list(found) == ["Sales Orders", "Notes"]
    assert found["Notes"]["sheetId"] == 7


def test_two_spreadsheets_are_remembered_apart(fake_google):
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": ONE_SHEET}))

    sheets.sheets_in("one")
    sheets.sheets_in("other")

    assert len(pretend.spreadsheets_endpoint.calls) == 2


def test_forgetting_the_name_a_spreadsheet_was_resolved_by_is_not_this_ones_job(
    fake_google,
):
    """The two caches were swapped once. This is what keeps them the right way round.

    A spreadsheet's id does not change while the agent runs, so a write is no
    reason to go and resolve its name again: that is a Drive round trip bought
    for nothing. DriveService.forget() is still there for a rename or a
    delete, which is the only thing that can make a cached id wrong.
    """
    fake_google(
        files=Endpoint(answers={"list": {"files": [{"id": "an-id", "name": "Sales"}]}}),
        spreadsheets=Endpoint(answers={"get": ONE_SHEET}),
    )
    sheets.resolve_spreadsheet("Sales")
    sheets.sheets_in("an-id")

    sheets.forget("an-id")

    assert "an-id" not in sheets._sheets_in
    assert sheets._drive._spreadsheet_ids == {"Sales": "an-id"}


def test_forgetting_drops_the_sheets_that_a_change_may_have_moved(fake_google):
    """What forget() exists for, by its own docstring.

    "Adding or removing a sheet changes which numeric ids are real, and every
    structural change moves the rows and columns that were counted." A sheet
    grown from four columns to five is read here as still having four.
    """
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": ONE_SHEET}))
    assert sheets.sheets_in("an-id")["Sales Orders"]["gridProperties"]["columnCount"] == 4

    pretend.spreadsheets_endpoint.answers["get"] = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Sales Orders",
                    "gridProperties": {"rowCount": 10, "columnCount": 5},
                }
            }
        ]
    }
    sheets.forget("an-id")

    grown = sheets.sheets_in("an-id")["Sales Orders"]["gridProperties"]["columnCount"]
    assert grown == 5


def test_only_the_spreadsheet_written_to_is_forgotten(fake_google):
    """One id in, one id out.

    Clearing the lot would work as a fix and cost every other spreadsheet in
    the conversation a re-read it did not need, so this is here to stop the
    next person reaching for _sheets_in.clear().
    """
    fake_google(spreadsheets=Endpoint(answers={"get": ONE_SHEET}))
    sheets.sheets_in("one")
    sheets.sheets_in("other")

    sheets.batch("one", [{"insertDimension": {}}])

    assert "one" not in sheets._sheets_in
    assert "other" in sheets._sheets_in


def test_forgetting_a_spreadsheet_never_read_is_harmless(fake_google):
    fake_google()

    # A write can land on a spreadsheet nothing has read yet, so this is an
    # ordinary case rather than a mistake.
    sheets.forget("never-read")

    assert sheets._sheets_in == {}


def test_a_write_sends_the_next_read_back_to_google(fake_google):
    """The gap that started this, reached the way a tool reaches it.

    modify_column reads columnCount out of this to decide whether it has to
    insert a column before writing one. Two additions in a row have to see the
    count the first one left, not the one from before it.
    """
    pretend = fake_google(spreadsheets=Endpoint(answers={"get": ONE_SHEET}))
    before = sheets.sheets_in("an-id")["Sales Orders"]["gridProperties"]["columnCount"]

    sheets.batch("an-id", [{"insertDimension": {}}])
    pretend.spreadsheets_endpoint.answers["get"] = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Sales Orders",
                    "gridProperties": {"rowCount": 10, "columnCount": 5},
                }
            }
        ]
    }
    after = sheets.sheets_in("an-id")["Sales Orders"]["gridProperties"]["columnCount"]

    assert (before, after) == (4, 5)
    # Read, write, read again: the write sent the next reader back to Google
    # rather than answering out of what was true before it.
    assert len(pretend.spreadsheets_endpoint.calls) == 3


# Picking a sheet out of a spreadsheet


def test_a_sheet_is_found_by_name_whatever_the_case(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"get": TWO_SHEETS}))

    assert sheets.resolve_sheet("an-id", "  notes ")["sheetId"] == 7


def test_naming_no_sheet_takes_the_one_a_spreadsheet_opens_on(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"get": TWO_SHEETS}))

    assert sheets.resolve_sheet("an-id")["title"] == "Sales Orders"


def test_a_sheet_that_is_not_there_is_refused_by_naming_the_ones_that_are(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"get": TWO_SHEETS}))

    with pytest.raises(ValueError) as refused:
        sheets.resolve_sheet("an-id", "Nonsense")

    assert "Sales Orders" in str(refused.value)
    assert "Notes" in str(refused.value)


def test_a_spreadsheet_with_no_sheets_at_all_is_refused(fake_google):
    fake_google(spreadsheets=Endpoint(answers={"get": {"sheets": []}}))

    with pytest.raises(ValueError, match="no sheets"):
        sheets.resolve_sheet("an-id")


# Which spreadsheet is being worked on


def test_naming_nothing_uses_the_spreadsheet_being_worked_on(monkeypatch, fake_google):
    fake_google(
        files=Endpoint(answers={"list": {"files": [{"id": "an-id", "name": "In Use"}]}})
    )
    monkeypatch.setattr(config, "SPREADSHEET", "In Use")

    assert sheets.resolve_spreadsheet() == ("an-id", "In Use")


def test_the_spreadsheet_in_use_is_read_when_asked_not_when_imported(
    monkeypatch, fake_google
):
    """A spreadsheet chosen while the agent runs has to be the one that answers."""
    fake_google(
        files=Endpoint(answers={"list": {"files": [{"id": "later-id", "name": "Chosen Later"}]}})
    )
    monkeypatch.setattr(config, "SPREADSHEET", None)
    monkeypatch.setattr(config, "SPREADSHEET", "Chosen Later")

    assert sheets.resolve_spreadsheet()[0] == "later-id"


@pytest.mark.parametrize("nothing", (None, "", "   "))
def test_naming_nothing_with_nothing_chosen_says_what_to_do(
    nothing, monkeypatch, fake_google
):
    fake_google()
    monkeypatch.setattr(config, "SPREADSHEET", None)

    with pytest.raises(ValueError) as refused:
        sheets.resolve_spreadsheet(nothing)

    assert "No spreadsheet has been chosen yet" in str(refused.value)
    assert "list_workbooks" in str(refused.value)


def test_resolving_goes_through_the_drive_service(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(
        sheets._drive,
        "resolve_spreadsheet",
        lambda name: asked.append(name) or ("an-id", name),
    )

    sheets.resolve_spreadsheet("  Sales  ")

    # Stripped here, so the service is asked about a name and not about
    # whatever spacing the model happened to put around it.
    assert asked == ["Sales"]


def test_searching_by_name_and_by_content_go_to_the_drive_service(monkeypatch):
    monkeypatch.setattr(
        sheets._drive, "search_spreadsheets", lambda name=None: [("id", f"by name {name}")]
    )
    monkeypatch.setattr(
        sheets._drive, "search_spreadsheets_by_content", lambda text: [("id", f"inside {text}")]
    )

    assert sheets.search("sales") == [("id", "by name sales")]
    assert sheets.containing("ORD-1042") == [("id", "inside ORD-1042")]


# The surface nine other modules import by name


def test_everything_the_tools_import_is_still_here():
    """A tool holds its own reference, taken at import.

    Losing a name here is an ImportError at start up rather than a test
    failure somewhere useful, so the list is written out.
    """
    for name in (
        "Cell a1 batch cell chart_kind chart_title charts_in column_letter containing "
        "find_header_row grid header_map is_blank last_data_row number_forms readable "
        "resolve_sheet resolve_spreadsheet search sheets_in to_dimension_range "
        "to_grid_range write_values"
    ).split():
        assert hasattr(sheets, name), name


def test_every_tool_module_still_imports():
    from excel_agent.tools import TOOLS

    assert len(TOOLS) == 10


def test_the_error_reader_the_tools_use_is_the_services_own():
    from excel_agent.services.google import readable

    # Re-exported rather than reimplemented, so there is one wording of these
    # messages and not two that drift apart.
    assert sheets.readable is readable


def test_the_retry_settings_left_behind_in_this_module_do_nothing(monkeypatch):
    """RETRY_ON, MAX_ATTEMPTS and MAX_BACKOFF still sit at the top of sheets.py.

    The code that read them moved to services/google.py, which has its own
    copies. They are dead, and dead in the way that costs someone an afternoon:
    turning this one down to a single attempt changes nothing at all, and the
    retries carry on from the other copy.
    """
    from excel_agent.services import google as google_module

    monkeypatch.setattr(google_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sheets, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(sheets, "RETRY_ON", ())

    request = Request(answer={"ok": True}, failures=[error(429)])
    assert sheets.with_retries(request) == {"ok": True}
    assert request.attempts == 2


def test_the_mime_type_left_behind_is_the_same_one_the_service_uses():
    """Dead too, but at least not disagreeing with the live one.

    Two copies of a constant that drift apart is how a search starts quietly
    returning documents as well as spreadsheets.
    """
    from excel_agent.services.drive import SPREADSHEET_MIME

    assert sheets.SPREADSHEET_MIME == SPREADSHEET_MIME


def test_the_guard_that_keeps_this_suite_off_google_still_bites():
    """The guard is the reason none of this can touch a real spreadsheet.

    It was patching a function the refactor deleted, which made every test in
    the project an error and would have left the next one to reach Google
    doing it for real. A guard nothing checks is a guard nobody notices
    breaking, so this checks it.
    """
    from excel_agent.services.google import GoogleAPI

    with pytest.raises(AssertionError, match="asked Google"):
        GoogleAPI().drive

    with pytest.raises(AssertionError, match="asked Google"):
        GoogleAPI().service("sheets", "v4")


def test_escaping_is_the_same_rule_on_both_sides_of_the_move():
    from excel_agent.services.drive import quoted as service_quoted

    for text in ("plain", "Bob's file", "back\\slash", "both'\\ways"):
        assert sheets.quoted(text) == service_quoted(text)
