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
from excel_agent import runner  # noqa: E402
from excel_agent.services.drive import drive_service  # noqa: E402
from excel_agent.services.google import GoogleAPI, google_api  # noqa: E402
from excel_agent.services.spreadsheet import spreadsheet_service  # noqa: E402

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


@pytest.fixture(autouse=True)
def no_spreadsheet_from_the_environment(monkeypatch):
    """Start every test having chosen nothing.

    EXCEL_AGENT_SPREADSHEET names the file a conversation opens on, and a
    Session seeds itself from it. Anyone who followed the README has it set,
    and without this their suite runs against a different starting state than
    everybody else's.
    """
    monkeypatch.setattr(runner, "START_SPREADSHEET", None)


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
            "must work on sheets built by hand, through the a_spreadsheet "
            "or a_drive fixtures."
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
        drive_service._spreadsheet_ids,
        spreadsheet_service._sheets,
        spreadsheet_service._grids,
    ):
        cache.clear()

    yield

    for cache in (
        google_api._services,
        drive_service._spreadsheet_ids,
        spreadsheet_service._sheets,
        spreadsheet_service._grids,
    ):
        cache.clear()


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
    "sort_range",
    "insert_columns",
    "delete_columns",
    "move_column",
    "copy_paste",
    "repeat_cell",
    "batch_update",
    "format_range",
    "freeze",
    "size_columns",
    "add_chart",
    "update_chart_spec",
    "delete_chart",
)


@pytest.fixture
def a_spreadsheet(monkeypatch):
    """Point whole tool modules at a sheet built by hand, and record the writes.

    resolve_spreadsheet is patched inside each tool module, because a tool
    holds its own reference to it, taken at import. Everything else goes
    through the two shared services, whose methods are replaced once and
    reach every module at once.

    Given no modules it patches every one of them, which is what a test driving
    an agent needs: the model decides which tool to call, so the test cannot
    know in advance which module to stub.

    Returns the list of writes, so a test can assert that a turn really wrote
    without a spreadsheet to look at afterwards.
    """
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
            # A module has only the names it imported, so what it has is
            # what gets patched.
            if hasattr(module, "resolve_spreadsheet"):
                monkeypatch.setattr(
                    module,
                    "resolve_spreadsheet",
                    lambda name=None: ("an-id", spreadsheet),
                )

        # Finding files goes through the one shared Drive service.
        monkeypatch.setattr(
            drive_service,
            "search_spreadsheets",
            lambda name=None: [("an-id", spreadsheet)],
        )
        monkeypatch.setattr(
            drive_service,
            "search_spreadsheets_by_content",
            lambda text: [("an-id", spreadsheet)],
        )

        properties = {"title": title, "sheetId": 0}

        for name, replacement in (
            ("resolve_sheet", lambda id, name=None: properties),
            ("list_sheets", lambda id: {title: properties}),
            ("read_sheet", lambda id, name: rows),
            ("read_range", lambda id, range_name: []),
            ("list_charts", lambda id, name=None: []),
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

        # A method renamed on the service leaves a name here that patches
        # nothing, which shows up as an unrelated test failing far away.
        missing = [
            name
            for name in SERVICE_WRITES
            if not hasattr(spreadsheet_service, name)
        ]

        assert not missing, (
            f"SERVICE_WRITES names methods SpreadsheetService no longer has: "
            f"{', '.join(missing)}. Update the list in conftest."
        )

        for name in SERVICE_WRITES:
            monkeypatch.setattr(spreadsheet_service, name, recording(name))

        return sent

    return use


@pytest.fixture
def a_drive(monkeypatch):
    """Answer the questions browsing asks of Drive without asking Drive."""

    def use(rows=None, files=(("an-id", SPREADSHEET),), sheet=SHEET):
        monkeypatch.setattr(
            drive_service,
            "search_spreadsheets",
            lambda name=None: list(files),
        )
        monkeypatch.setattr(
            "excel_agent.sheets.resolve_spreadsheet",
            lambda name=None: (files[0][0], files[0][1]),
        )
        monkeypatch.setattr(
            spreadsheet_service,
            "resolve_sheet",
            lambda id, name=None: {"title": sheet, "sheetId": 0},
        )
        monkeypatch.setattr(
            spreadsheet_service,
            "read_sheet",
            lambda id, title: (
                rows if rows is not None else fake_sheets.orders()
            ),
        )

    return use
