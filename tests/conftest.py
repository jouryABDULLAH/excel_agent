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

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


@pytest.fixture(autouse=True)
def no_google(monkeypatch):
    """Fail any test that reaches Google instead of stubbing it.

    Every call goes through service() to get a client, so refusing there
    catches the lot. A test that forgets to stub would otherwise sit waiting on
    the network, or worse, write to somebody's real spreadsheet.
    """

    def refuse(api, version):
        raise AssertionError(
            f"This test asked Google for the {api} client. Tests must work on "
            "sheets built by hand, through the a_sheet or a_drive fixture."
        )

    monkeypatch.setattr(sheets, "service", refuse)


@pytest.fixture
def a_spreadsheet(monkeypatch):
    """Point whole tool modules at a sheet built by hand, and record the writes.

    Each tool imports what it needs from sheets.py by name, so what is replaced
    is the name inside the tool's own module. Patching excel_agent.sheets would
    not reach them: a tool holds its own reference, taken at import.

    Given no modules it patches every one of them, which is what a test driving
    an agent needs: the model decides which tool to call, so the test cannot
    know in advance which module to stub.

    Returns the list of writes, so a test can assert that a turn really wrote
    without a spreadsheet to look at afterwards.
    """
    from excel_agent.tools import charts, columns, find, inspect, modify, spreadsheets, stats, style

    every = (charts, columns, find, inspect, modify, spreadsheets, stats, style)

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
