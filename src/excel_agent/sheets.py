"""The arithmetic of a sheet, and the names the tools share.

Nothing here holds a Google client or a cache: reads and writes go through
the services, and what lives here is how a grid of cells is measured and
addressed once it has been read.
"""

from excel_agent.services.drive import drive_service
# Taken from the service rather than declared here as well. A second class of
# the same name and the same fields is still a different class: read_sheet
# returns the service's Cell, and a helper annotated with a local one does not
# accept it, which is a type error on every tool that reads a sheet and then
# measures it.
from excel_agent.services.spreadsheet import Cell, EMPTY


def resolve_spreadsheet(name: str | None = None) -> tuple[str, str]:
    """Turn the name of a spreadsheet into its id, and give back both.

    The refusal for a missing name lives here rather than in the service,
    because it tells the model which tool to call next -- the service does
    not know the tools exist.
    """
    wanted = (name or "").strip()

    if not wanted:
        raise ValueError(
            "No spreadsheet has been chosen yet. Call list_workbooks and "
            "ask the user which one to work on, then name it in the "
            "spreadsheet argument."
        )

    return drive_service.resolve_spreadsheet(wanted)


def chart_kind(spec: dict) -> str:
    """What sort of chart a spec describes, in the words the tool uses."""
    if "pieChart" in spec:
        return "pie"
    if "basicChart" in spec:
        return str(spec["basicChart"].get("chartType", "")).lower() or "chart"

    return "chart"


def chart_title(spec: dict) -> str:
    """What a chart is called, or a note that it is called nothing."""
    return spec.get("title") or "(untitled)"


def cell(rows: list[list[Cell]], row: int, column: int) -> Cell:
    """One cell, by the numbers a person uses.

    Rows stop at their last filled cell rather than being padded out, so
    asking past the end of one is ordinary rather than a mistake, and an empty
    cell comes back instead of an error.
    """
    if row < 1 or row > len(rows):
        return EMPTY

    values = rows[row - 1]
    if column < 1 or column > len(values):
        return EMPTY

    return values[column - 1]


def is_blank(value) -> bool:
    """Whether a cell holds nothing worth reading.

    A cell never filled in and one emptied by hand look identical to a
    reader, so they are treated identically here. A zero is not blank.
    """
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def find_header_row(rows: list[list[Cell]], search_depth: int = 10) -> int:
    """Find the row that holds the column names, counting from 1.

    The first row near the top with at least two filled cells, all of them
    text, and something filled in the row below. A title above the table
    fails the two cell test, so a sheet that does not start with its header
    is still read correctly.

    Whether a cell is text is asked of its value and not of what it displays,
    because Google formats every cell into a string for display: a row of
    years would otherwise pass for a header.
    """
    for index in range(min(search_depth, len(rows))):
        filled = [one for one in rows[index] if not is_blank(one.displayed)]

        if len(filled) < 2:
            continue
        if not all(isinstance(one.value, str) for one in filled):
            continue
        if index + 1 >= len(rows):
            continue
        if all(is_blank(one.displayed) for one in rows[index + 1]):
            continue

        return index + 1

    return 1


class Headers(dict):
    """Column names to column numbers, reached however they were written.

    Capitalisation and surrounding spaces are how someone types a header,
    not what tells two columns apart: "profit margin" means the "Profit
    Margin" column. An exact match wins, so a sheet holding both spellings
    still reaches the one actually named, and two columns differing only in
    case are unreachable by a spelling that is neither -- which is a refusal
    rather than a guess.

    A dict subclass so that every tool reading `column in headers` or
    `headers[column]` gets this without knowing it exists.
    """

    def _named(self, key) -> str | None:
        """The real header a name reaches, or None."""
        if dict.__contains__(self, key):
            return key

        if not isinstance(key, str):
            return None

        wanted = key.strip().casefold()

        found = [
            name
            for name in self
            if name.strip().casefold() == wanted
        ]

        return found[0] if len(found) == 1 else None

    def __contains__(self, key) -> bool:
        return self._named(key) is not None

    def __getitem__(self, key) -> int:
        found = self._named(key)

        if found is None:
            raise KeyError(key)

        return dict.__getitem__(self, found)

    def get(self, key, default=None):
        found = self._named(key)

        return (
            dict.__getitem__(self, found)
            if found is not None
            else default
        )


def header_map(rows: list[list[Cell]], header_row: int) -> Headers:
    """Map each column name to its column number, counting from 1.

    Looking columns up by name means no column letters are written down, so a
    column that moves does not break a tool. A cell with nothing in it names
    no column and is left out.
    """
    if header_row > len(rows):
        return Headers()

    return Headers(
        (str(one.displayed).strip(), number)
        for number, one in enumerate(rows[header_row - 1], start=1)
        if not is_blank(one.displayed)
    )


def last_data_row(rows: list[list[Cell]], header_row: int) -> int:
    """Find the last row that actually holds data, counting from 1.

    A sheet is a grid of a thousand rows whether or not anything was put in
    them, so its size says nothing about how much is filled. Walking back up
    finds the end of the data, and returns the header row itself when there is
    none.
    """
    for number in range(len(rows), header_row, -1):
        if any(not is_blank(one.displayed) for one in rows[number - 1]):
            return number

    return header_row


def column_letter(number: int) -> str:
    """Turn a column number counting from 1 into its letters: 1 -> A, 27 -> AA."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters

    return letters


def a1(
    title: str,
    first_row: int | None = None,
    last_row: int | None = None,
    first_column: int | None = None,
    last_column: int | None = None,
) -> str:
    """Write a range the way a person would: "Sales!A2:D10".

    Rows and columns count from 1, as they do everywhere the model can see.
    Anything left out is left open, so a range with no last row reaches the
    bottom of the sheet however far down the data goes.

    The values endpoints take this, which is why they need no conversion at
    all: A1 is already the numbering the model uses.
    """
    if first_row is None and first_column is None:
        return f"'{title}'"

    start = f"{column_letter(first_column) if first_column else ''}{first_row or ''}"
    end = f"{column_letter(last_column) if last_column else ''}{last_row or ''}"

    return f"'{title}'!{start}:{end}"


def to_grid_range(
    sheet_id: int,
    first_row: int | None = None,
    last_row: int | None = None,
    first_column: int | None = None,
    last_column: int | None = None,
) -> dict:
    """Turn rows and columns counting from 1 into the range batchUpdate wants.

    This is the only place that knows Google counts from 0 and leaves the end
    of a range out of it, so row 7 alone is startIndex 6 and endIndex 7. Every
    structural request goes through here, and nothing else does the sum: a
    tool that worked it out for itself would be one more place to get it
    wrong, and getting it wrong deletes the row next to the one meant.

    Anything left out is left off, and an absent bound means the whole of that
    direction.
    """
    grid_range: dict = {"sheetId": sheet_id}

    if first_row is not None:
        grid_range["startRowIndex"] = first_row - 1
    if last_row is not None:
        grid_range["endRowIndex"] = last_row
    if first_column is not None:
        grid_range["startColumnIndex"] = first_column - 1
    if last_column is not None:
        grid_range["endColumnIndex"] = last_column

    return grid_range
