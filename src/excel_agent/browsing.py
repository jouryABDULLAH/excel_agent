"""What a front end needs to know about the files it can work on.

The page asks the same handful of questions whichever backend is in use: what
can be worked on, which one is in hand, how to move to another, where that
leaves us, and what is worth asking about it. Answering them from openpyxl and
from Drive is different enough that the alternative was a page full of
branches.

Nothing here draws anything, so it can be tested without Streamlit. Nothing
here is cheap either: for Drive, every one of these is a call over the
network, and Streamlit runs its page again on every click. Whoever draws the
page is the one that has to cache them.
"""

from excel_agent import config

# Offered whatever the sheet turns out to hold, and the whole of what is
# offered when it cannot be read at all.
GENERIC = ["Show me the first few rows", "Summarise every column"]

# The last word of a column that names each row rather than measuring it.
# Matched on the last word, so "Order ID" and "Employee Code" are caught while
# "Reorder Level" is left alone.
IDENTIFIERS = {"id", "sku", "code", "ref", "number", "no"}


def asks_for(columns: dict[str, list]) -> list[str]:
    """Turn a few rows of each column into things worth asking.

    Built from the column names rather than written down in advance, so what
    is offered fits the file that is open. A column counts as numbers only if
    every value seen is one, which keeps a column of mostly-blank text out of
    the totals.
    """
    numbers, labels = [], []
    for name, values in columns.items():
        filled = [value for value in values if value not in (None, "")]
        if not filled:
            continue
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in filled
        ):
            numbers.append(name)
        elif all(isinstance(value, str) for value in filled):
            labels.append(name)

    # An identifier is no good as either. Adding one up means nothing, and one
    # per row makes a chart with a bar for every row and nothing to read off
    # it. A sheet holding nothing else is offered the plain two rather than a
    # suggestion worth nobody's click.
    def not_an_id(name: str) -> bool:
        last = name.strip().lower().split()[-1] if name.strip() else ""
        return last not in IDENTIFIERS

    worth_adding = [name for name in numbers if not_an_id(name)]
    worth_labelling = [name for name in labels if not_an_id(name)]
    chosen = worth_adding[-1] if worth_adding else None

    asks = list(GENERIC)
    if chosen:
        asks.append(f"What is the total {chosen}?")
    if chosen and worth_labelling:
        asks.append(f"Draw a bar chart of {chosen} by {worth_labelling[0]}")

    return asks


# Workbooks in the data folder, through openpyxl


def local_workbooks() -> list[str]:
    """The workbooks that can be worked on."""
    return config.workbook_names()


def local_in_use() -> str | None:
    """The workbook being worked on."""
    return config.WORKBOOK_PATH.name


def local_choose(name: str) -> None:
    """Work on this workbook from now on. Raises ValueError on a bad name."""
    config.WORKBOOK_PATH = config.resolve_workbook(name)


def local_where() -> str:
    """Where the work is going, for the line under the title."""
    return config.WORKBOOK_PATH.name


def local_link() -> str | None:
    """A workbook is a file on this machine, so there is nowhere to send anyone."""
    return None


def local_suggestions() -> list[str]:
    """A few things worth asking about the workbook in hand."""
    from excel_agent.workbook import (
        find_header_row,
        header_map,
        is_blank,
        last_data_row,
        load_values,
    )

    try:
        sheet = load_values(config.WORKBOOK_PATH).active
        header_row = find_header_row(sheet)
        headers = header_map(sheet, header_row)
        last_row = last_data_row(sheet, header_row)
    except Exception:  # noqa: BLE001 - a workbook that will not open offers nothing
        return list(GENERIC)

    if not headers or last_row <= header_row:
        return list(GENERIC)

    rows = range(header_row + 1, min(header_row + 6, last_row) + 1)
    columns = {
        name: [
            sheet.cell(row=row, column=number).value
            for row in rows
            if not is_blank(sheet.cell(row=row, column=number).value)
        ]
        for name, number in headers.items()
    }
    return asks_for(columns)


# Spreadsheets on Google Drive


def sheets_workbooks() -> list[str]:
    """The spreadsheets that can be worked on, by name."""
    from excel_agent.sheets import search

    return [title for _, title in search()]


def sheets_in_use() -> str | None:
    """The spreadsheet being worked on, or None when none has been chosen."""
    return config.SPREADSHEET


def sheets_choose(name: str) -> None:
    """Work on this spreadsheet from now on. Raises ValueError on a bad name.

    Resolved rather than trusted, so the name stored is the one Drive really
    holds and a name shared by two files is refused here rather than by every
    call that came after it.
    """
    from excel_agent.sheets import resolve_spreadsheet

    _, title = resolve_spreadsheet(name)
    config.SPREADSHEET = title


def sheets_where() -> str:
    """Which sheet of which spreadsheet the work is going to.

    Both are named because neither on its own says where a change will land: a
    spreadsheet holds several sheets, and the one used when none is asked for
    is simply the first.
    """
    from excel_agent.sheets import resolve_sheet, resolve_spreadsheet

    if not config.SPREADSHEET:
        return "[no spreadsheet chosen yet]"

    try:
        spreadsheet_id, title = resolve_spreadsheet(None)
        properties = resolve_sheet(spreadsheet_id, None)
    except Exception:  # noqa: BLE001 - the page says where, it does not diagnose
        return str(config.SPREADSHEET)

    return f"{properties['title']} in {title}"


def sheets_link() -> str | None:
    """Where the spreadsheet lives, so it can be opened beside the agent.

    The sheet itself is the only view of the sheet worth having: it is always
    right, and a change landing in it in front of someone is the whole of the
    proof that the change was real.
    """
    from excel_agent.sheets import resolve_spreadsheet

    if not config.SPREADSHEET:
        return None

    try:
        spreadsheet_id, _ = resolve_spreadsheet(None)
    except Exception:  # noqa: BLE001 - no link is better than a broken one
        return None

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def sheets_suggestions() -> list[str]:
    """A few things worth asking about the spreadsheet in hand."""
    from excel_agent.sheets import (
        cell,
        find_header_row,
        grid,
        header_map,
        last_data_row,
        resolve_sheet,
        resolve_spreadsheet,
    )

    if not config.SPREADSHEET:
        return list(GENERIC)

    try:
        spreadsheet_id, _ = resolve_spreadsheet(None)
        properties = resolve_sheet(spreadsheet_id, None)
        rows = grid(spreadsheet_id, properties["title"])
    except Exception:  # noqa: BLE001 - a sheet that will not open offers nothing
        return list(GENERIC)

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    last_row = last_data_row(rows, header_row)

    if not headers or last_row <= header_row:
        return list(GENERIC)

    numbers = range(header_row + 1, min(header_row + 6, last_row) + 1)
    columns = {
        name: [
            value
            for value in (cell(rows, row, number).value for row in numbers)
            if value not in (None, "")
        ]
        for name, number in headers.items()
    }
    return asks_for(columns)


BACKENDS = {
    "local": {
        "workbooks": local_workbooks,
        "in_use": local_in_use,
        "choose": local_choose,
        "where": local_where,
        "link": local_link,
        "suggestions": local_suggestions,
        # A workbook is added by putting a file in the folder. A spreadsheet is
        # added in Drive, by Google, and nothing here has the scope to do it.
        "uploads": True,
        "title": "Excel agent",
        "noun": "Workbooks",
        "empty": "No workbooks yet. Upload one below.",
    },
    "sheets": {
        "workbooks": sheets_workbooks,
        "in_use": sheets_in_use,
        "choose": sheets_choose,
        "where": sheets_where,
        "link": sheets_link,
        "suggestions": sheets_suggestions,
        "uploads": False,
        "title": "Sheets agent",
        "noun": "Spreadsheets",
        "empty": "No spreadsheets in this Drive.",
    },
}


def browsing_for(backend: str) -> dict:
    """The answers for one backend.

    Raises ValueError, naming both backends, when asked for neither.
    """
    if backend not in BACKENDS:
        raise ValueError(f'EXCEL_AGENT_BACKEND is "{backend}". Use "local" or "sheets".')

    return BACKENDS[backend]


IN_USE = browsing_for(config.BACKEND)
