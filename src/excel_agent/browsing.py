"""What a front end needs to know about the spreadsheets it can work on.

The handful of questions a page asks: what can be worked on, which one is in
hand, how to move to another, where that leaves us, and what is worth asking
about it.

Nothing here draws anything, so it can be tested without Streamlit. Nothing
here is cheap either: every one of these is a call over the network, and
Streamlit runs its page again on every click. Whoever draws the page is the
one that has to cache them.
"""

# What the page calls itself and the things it lists.
TITLE = "Sheets agent"
NOUN = "Spreadsheets"
EMPTY = "No spreadsheets in this Drive."

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


def workbooks() -> list[str]:
    """The spreadsheets that can be worked on, by name."""
    from excel_agent.services.drive import drive_service

    return [
        title for _, title in drive_service.search_spreadsheets()
    ]


def choose(name: str) -> tuple[str, str]:
    """Resolve a spreadsheet the page offered. Raises ValueError on a bad name.

    Resolved rather than trusted, so the name handed back is the one Drive
    really holds and a name shared by two files is refused here rather than by
    every call that came after it.

    Nothing is stored: which spreadsheet is in hand belongs to the Session
    that asked, not to this module.
    """
    from excel_agent.sheets import resolve_spreadsheet

    return resolve_spreadsheet(name)


def where(in_use: str | None) -> str:
    """Which sheet of which spreadsheet the work is going to.

    Both are named because neither on its own says where a change will land: a
    spreadsheet holds several sheets, and the one used when none is asked for
    is simply the first.
    """
    from excel_agent.services.spreadsheet import spreadsheet_service
    from excel_agent.sheets import resolve_spreadsheet

    if not in_use:
        return "[no spreadsheet chosen yet]"

    try:
        spreadsheet_id, title = resolve_spreadsheet(in_use)
        properties = spreadsheet_service.resolve_sheet(spreadsheet_id)
    except Exception:  # noqa: BLE001 - the page says where, it does not diagnose
        return str(in_use)

    return f"{properties['title']} in {title}"


def link(in_use: str | None) -> str | None:
    """Where the spreadsheet lives, so it can be opened beside the agent.

    The sheet itself is the only view of the sheet worth having: it is always
    right, and a change landing in it in front of someone is the whole of the
    proof that the change was real.
    """
    from excel_agent.sheets import resolve_spreadsheet

    if not in_use:
        return None

    try:
        spreadsheet_id, _ = resolve_spreadsheet(in_use)
    except Exception:  # noqa: BLE001 - no link is better than a broken one
        return None

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def suggestions(in_use: str | None) -> list[str]:
    """A few things worth asking about the spreadsheet in hand."""
    from excel_agent.services.spreadsheet import spreadsheet_service
    from excel_agent.sheets import (
        cell,
        find_header_row,
        header_map,
        last_data_row,
        resolve_spreadsheet,
    )

    if not in_use:
        return list(GENERIC)

    try:
        spreadsheet_id, _ = resolve_spreadsheet(in_use)
        properties = spreadsheet_service.resolve_sheet(spreadsheet_id)
        rows = spreadsheet_service.read_sheet(
            spreadsheet_id,
            properties["title"],
        )
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
