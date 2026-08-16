"""Shared fixtures.

Three jobs: keep test runs out of LangSmith, keep them off Google, and give
them sheets built by hand to read instead.
"""

import os

# Before excel_agent is imported, and so before LangChain is, because the
# suite would otherwise be sent to LangSmith like any other run.
os.environ["LANGSMITH_TRACING"] = "false"

import pytest  # noqa: E402

import fake_sheets  # noqa: E402
from excel_agent import sheets  # noqa: E402
from excel_agent.services.google import GoogleAPI, google_api  # noqa: E402

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


@pytest.fixture(autouse=True)
def no_google(monkeypatch):
    """Fail any test that reaches Google instead of stubbing it.

    Every client is built in GoogleAPI.service, and both the sheets and drive
    properties go through it, so refusing there catches the lot however a
    caller arrived. Patched on the class rather than on the shared instance,
    because a test that builds a GoogleAPI of its own must be refused too.

    A test that forgets to stub would otherwise sit waiting on the network,
    or worse, write to somebody's real spreadsheet.
    """

    def refuse(self, api, version):
        raise AssertionError(
            f"This test asked Google for the {api} {version} client. Tests "
            "must work on sheets built by hand, through the a_spreadsheet, "
            "a_drive or fake_google fixtures."
        )

    monkeypatch.setattr(GoogleAPI, "service", refuse)


@pytest.fixture(autouse=True)
def nothing_remembered_between_tests():
    """Empty every cache that outlives a test.

    All three live on module level objects built once at import: the built
    clients, the spreadsheet names DriveService has resolved, and the sheets
    read out of a spreadsheet. Left alone, what one test put there answers the
    next one's question, and the order tests happen to run in starts deciding
    whether they pass.
    """
    for cache in (
        google_api._services,
        sheets._drive._spreadsheet_ids,
        sheets._sheets_in,
    ):
        cache.clear()

    yield

    for cache in (
        google_api._services,
        sheets._drive._spreadsheet_ids,
        sheets._sheets_in,
    ):
        cache.clear()


@pytest.fixture
def fake_google(monkeypatch):
    """Put a Google built by hand behind sheets.py and its DriveService.

    Both seams are moved together on purpose: sheets.py reaches Google through
    the shared google_api, and DriveService holds its own reference taken when
    it was built, so patching one and not the other leaves half the module
    talking to the real thing.
    """
    import fake_google as builder

    def use(files=None, spreadsheets=None):
        pretend = builder.FakeGoogle(files=files, spreadsheets=spreadsheets)
        monkeypatch.setattr(sheets, "google_api", pretend)
        monkeypatch.setattr(sheets._drive, "_google", pretend)
        return pretend

    return use


# Everything on SpreadsheetService that changes a spreadsheet. Written out
# rather than worked out from the class, so a method added to the service
# without a thought for the tests fails loudly here instead of quietly
# reaching Google.
SERVICE_WRITES = (
    "append_rows",
    "update_cells",
    "clear_range",
    "insert_rows",
    "delete_rows",
    "move_row",
    "insert_columns",
    "delete_columns",
    "move_columns",
    "batch_update",
    "format_range",
    "add_chart",
    "update_chart",
    "delete_chart",
)


@pytest.fixture
def a_spreadsheet(monkeypatch):
    """Point whole tool modules at a sheet built by hand, and record the writes.

    Two seams, because the tools reach a spreadsheet two ways while the
    refactor is half done.

    The older tools import what they need from sheets.py by name, so what is
    replaced is the name inside the tool's own module. Patching excel_agent.sheets
    would not reach them: a tool holds its own reference, taken at import.

    The newer ones hold spreadsheet_service instead, which is one shared object,
    so its methods are replaced once and every module that uses it is covered.
    Without this half a migrated tool talks to the real Google, or fails the
    no_google guard, and either way the test stops testing what it says it does.

    Given no modules it patches every one of them, which is what a test driving
    an agent needs: the model decides which tool to call, so the test cannot
    know in advance which module to stub.

    Returns the list of writes, so a test can assert that a turn really wrote
    without a spreadsheet to look at afterwards.
    """
    from excel_agent.services.spreadsheet import spreadsheet_service
    from excel_agent.tools import (
        charts,
        columns,
        find,
        inspect,
        rows as row_tools,
        spreadsheets,
        stats,
        style,
    )

    every = (charts, columns, find, inspect, row_tools, spreadsheets, stats, style)

    def use(rows=None, modules=every, title=SHEET, spreadsheet=SPREADSHEET):
        rows = fake_sheets.orders() if rows is None else rows
        sent: list = []

        for module in modules:
            for name, replacement in (
                ("resolve_spreadsheet", lambda name=None: ("an-id", spreadsheet)),
                ("resolve_sheet", lambda id, name=None: {"title": title, "sheetId": 0}),
                ("sheets_in", lambda id: {title: {"title": title, "sheetId": 0}}),
                ("grid", lambda id, title: rows),
                ("charts_in", lambda id, title: []),
                ("search", lambda name=None: [("an-id", spreadsheet)]),
                ("containing", lambda text: [("an-id", spreadsheet)]),
                ("batch", lambda id, requests: sent.append(requests)),
                ("write_values", lambda id, data: sent.append(data)),
                ("forget", lambda id: None),
            ):
                # A module has only the names it imported, so what it has is
                # what gets patched.
                if hasattr(module, name):
                    monkeypatch.setattr(module, name, replacement)

        properties = {"title": title, "sheetId": 0}

        for name, replacement in (
            ("resolve_sheet", lambda id, name=None: properties),
            ("list_sheets", lambda id: {title: properties}),
            ("read_sheet", lambda id, name: rows),
            ("read_range", lambda id, range_name: []),
            ("list_charts", lambda id: []),
            ("get_spreadsheet", lambda id: {"sheets": [{"properties": properties}]}),
            ("invalidate", lambda id: None),
        ):
            monkeypatch.setattr(spreadsheet_service, name, replacement)

        def recording(name):
            """Record the call and answer the way Google's reply is read."""
            def called(*arguments, **named):
                sent.append({"call": name, "args": arguments, **named})
                # Every caller reads the reply with .get and a default, so an
                # empty answer is enough and says nothing that is not true.
                return {}

            return called

        for name in SERVICE_WRITES:
            monkeypatch.setattr(spreadsheet_service, name, recording(name))

        return sent

    return use


@pytest.fixture
def a_drive(monkeypatch):
    """Answer the questions browsing asks of Drive without asking Drive."""

    def use(rows=None, files=(("an-id", SPREADSHEET),), sheet=SHEET):
        monkeypatch.setattr("excel_agent.sheets.search", lambda name=None: list(files))
        monkeypatch.setattr(
            "excel_agent.sheets.resolve_spreadsheet",
            lambda name=None: (files[0][0], files[0][1]),
        )
        monkeypatch.setattr(
            "excel_agent.sheets.resolve_sheet",
            lambda id, name=None: {"title": sheet, "sheetId": 0},
        )
        monkeypatch.setattr(
            "excel_agent.sheets.grid",
            lambda id, title: rows if rows is not None else fake_sheets.orders(),
        )

    return use
