"""How to deal with the Google Sheets and Drive APIs.

Rules the tools do not have to repeat: building a service, turning a name into
an id, reading a sheet into cells, and turning rows and columns counting from
one into the ranges Google wants. A tool that worked any of this out for
itself would be one more place to get it wrong.

Everything here raises ValueError with a message worth showing the model, or
HttpError for the caller to pass through readable().
"""

import random
import time
from dataclasses import dataclass
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from excel_agent import config
from excel_agent.auth import get_credentials
from excel_agent.scopes import SCOPES

# Worth trying again: too many requests, and the five hundreds that mean
# Google rather than the request. A 400 would be just as wrong the second time.
RETRY_ON = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5
MAX_BACKOFF = 32.0

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# What a cell is worth reading for: what it shows, what was typed into it, what
# it works out to, and whether the sheet is formatting it as a date. Asked for
# by name because the whole of a spreadsheet is far more than any tool here
# needs, and one call for all three beats three calls for one each.
GRID_FIELDS = (
    "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),"
    "data(rowData(values(formattedValue,userEnteredValue,effectiveValue,"
    "effectiveFormat(numberFormat(type))))))"
)

# The number formats that mean a cell holds a date rather than a number. Both
# arrive as a count of days, so without this a column of dates would be
# summarised as arithmetic on five figure numbers.
DATE_FORMATS = ("DATE", "DATE_TIME")


@dataclass(frozen=True)
class Cell:
    """One cell, in the three ways a tool might need to read it.

    displayed is what a person looking at the sheet sees, formula is what was
    typed in if that was a formula, and value is what it works out to, as a
    number or a string rather than as text. A tool showing the sheet wants the
    first; a tool adding a column up wants the last.
    """

    displayed: str | None = None
    formula: str | None = None
    value: object = None
    number_format: str | None = None

    @property
    def is_date(self) -> bool:
        """Whether the sheet is treating this cell as a date."""
        return self.number_format in DATE_FORMATS


EMPTY = Cell()

# Looked up once and kept, because none of it changes while the agent runs and
# all of it costs a round trip.
_services: dict[str, Any] = {}
_spreadsheet_ids: dict[str, str] = {}
_sheets_in: dict[str, dict[str, dict]] = {}


def service(api: str, version: str) -> Any:
    """One built service per API, kept for the life of the process.

    Building one is not free, and a tool that built its own would pay for it
    on every call. Discovery documents ship inside the client library now, so
    this does not reach the network.
    """
    if api not in _services:
        _services[api] = build(
            api, version, credentials=get_credentials(SCOPES), cache_discovery=False
        )

    return _services[api]


def sheets():
    """The Sheets service, for reading and writing a spreadsheet."""
    return service("sheets", "v4")


def drive():
    """The Drive service, for finding a spreadsheet by name."""
    return service("drive", "v3")


def forget(spreadsheet_id: str) -> None:
    """Drop what is remembered about one spreadsheet.

    Adding or removing a sheet changes which numeric ids are real, and every
    structural change moves the rows and columns that were counted. Anything
    that writes calls this, so the next read asks Google rather than answering
    from what was true before.
    """
    _sheets_in.pop(spreadsheet_id, None)


def readable(failure: HttpError) -> str:
    """Turn an HttpError into a sentence worth showing the model.

    A tool returns prose that the model reads and acts on, so a traceback is
    no use to it. The four below are the ones a wrong argument or a missing
    scope actually produces.
    """
    status = getattr(failure.resp, "status", None)
    detail = getattr(failure, "reason", None) or str(failure)

    if status == 401:
        return (
            "Google would not accept the saved sign in. Delete token.json and "
            "run the agent again to sign in afresh."
        )
    if status == 403:
        # Not always about permission: Drive also answers 403 for a query it
        # will not run, so what Google said matters more than any guess made
        # here about why.
        return f"Google refused the request: {detail}"
    if status == 404:
        return "That spreadsheet does not exist, or the signed in account cannot see it."
    if status == 400:
        return f"Google rejected the request as malformed: {detail}."

    return f"Google returned an error: {detail}."


def with_retries(call):
    """Send a prepared request, waiting and trying again when told to slow down.

    Only the statuses in RETRY_ON are worth repeating: a 400 means the request
    itself is wrong and would be just as wrong the second time. The wait grows
    with a little randomness in it, so several agents running at once do not
    come back in step with each other.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call.execute()
        except HttpError as failure:
            status = getattr(failure.resp, "status", None)
            if status not in RETRY_ON or attempt == MAX_ATTEMPTS - 1:
                raise

            waiting = min(2**attempt + random.random(), MAX_BACKOFF)
            time.sleep(waiting)

    raise RuntimeError("unreachable: the loop above either returns or raises")


def batch(spreadsheet_id: str, requests: list[dict]) -> dict:
    """Send one or more changes as a single batchUpdate.

    One call rather than several means the changes land together, and means
    one request against the quota instead of one each. Every caller here goes
    through this, so nothing writes without the retries above.

    What is remembered about the spreadsheet is dropped afterwards, since a
    change may have moved the very things that were remembered.
    """
    answer = with_retries(
        sheets()
        .spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
    )
    forget(spreadsheet_id)
    return answer


def write_values(spreadsheet_id: str, data: list[dict]) -> dict:
    """Write cells by A1 range, letting Google read what is typed.

    Each entry is a range and the values for it, so cells that are nowhere
    near each other go in one call. USER_ENTERED means what is written is read
    the way it would be if a person typed it: "=B2*C2" becomes a formula and
    "5" becomes a number, rather than both being stored as text.

    Values go through a different endpoint from structural changes, so this
    exists alongside batch() rather than inside it, and drops what is
    remembered for the same reason.
    """
    answer = with_retries(
        sheets().spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        )
    )
    forget(spreadsheet_id)
    return answer


def quoted(text: str) -> str:
    """Escape a value going into a Drive query.

    A quote inside the text would otherwise close the string early and change
    what is being asked for. Search terms reach here from the model, so this
    is not only about names with apostrophes in them.
    """
    return text.replace("\\", "\\\\").replace("'", "\\'")


def search(name: str | None = None) -> list[tuple[str, str]]:
    """Every spreadsheet whose name matches, as id and title.

    Asked of Drive rather than Sheets, because only Drive knows what files
    there are. Reading Drive is all this needs, so it works under the
    drive.readonly scope the agent asks for.
    """
    query = f"mimeType = '{SPREADSHEET_MIME}' and trashed = false"
    if name:
        query += f" and name contains '{quoted(name)}'"

    answer = with_retries(
        drive().files().list(
            q=query, pageSize=50, fields="files(id,name)", orderBy="name"
        )
    )
    return [(found["id"], found["name"]) for found in answer.get("files", [])]


def containing(text: str) -> list[tuple[str, str]]:
    """Every spreadsheet holding the given text anywhere inside it.

    Drive searches the contents of a spreadsheet, not only its name, which is
    what makes it possible to find the right file without opening each one in
    turn. Reading is all it needs, so this works under drive.readonly.

    The search is Drive's own: it matches whole words rather than parts of
    them, and it can lag a change by a little while, since a file is indexed
    after it is written rather than as it is written.

    Nothing is asked to be sorted. Drive refuses a fullText query that names
    an order, because it returns these by how well they match instead, which
    is the more useful order anyway.
    """
    query = (
        f"mimeType = '{SPREADSHEET_MIME}' and trashed = false "
        f"and fullText contains '{quoted(text)}'"
    )

    answer = with_retries(
        drive().files().list(q=query, pageSize=25, fields="files(id,name)")
    )
    return [(found["id"], found["name"]) for found in answer.get("files", [])]


def number_forms(text: str) -> list[str]:
    """The ways a number might be written in a sheet, given one of them.

    Drive indexes what a cell displays, not the number behind it, so looking
    for 12240 finds nothing in a sheet showing $12,240.00. Measured: 12240 and
    12,240 both miss, while 12240.00, 12,240.00 and $12,240.00 all hit.

    The text itself comes first, so anything that is not a number is searched
    exactly as it was given and nothing else is tried.
    """
    forms = [text]

    try:
        value = float(text.strip().replace(",", "").replace("$", ""))
    except ValueError:
        return forms

    for form in (f"{value:,.2f}", f"{value:.2f}", f"{value:,.0f}"):
        if form not in forms:
            forms.append(form)

    return forms


def resolve_spreadsheet(name: str | None = None) -> tuple[str, str]:
    """Turn the name of a spreadsheet into its id, and give back both.

    Returns the spreadsheet being worked on when given nothing, which is what
    a tool called without one wants. Names arrive from the model, so they are
    matched against what Drive actually holds rather than trusted: the id that
    comes back has been seen to exist.

    Raises ValueError, with a message worth showing the model, when the name
    reaches nothing.
    """
    wanted = (name or "").strip()

    if not wanted:
        # Read at the moment it is asked for rather than when this module was
        # imported, so a spreadsheet chosen while the agent is running is the
        # one that answers.
        if not config.SPREADSHEET:
            raise ValueError(
                "No spreadsheet has been chosen yet. Call list_workbooks and "
                "ask the user which one to work on, then name it in the "
                "spreadsheet argument."
            )
        wanted = config.SPREADSHEET

    if wanted in _spreadsheet_ids:
        return _spreadsheet_ids[wanted], wanted

    found = search(wanted)

    if not found:
        raise ValueError(
            f'There is no spreadsheet called "{wanted}". Call list_workbooks '
            "to see the ones there are."
        )

    if len(found) > 1:
        names = ", ".join(title for _, title in found)
        raise ValueError(
            f'More than one spreadsheet is called "{wanted}": {names}. Say '
            "which one, by its id."
        )

    spreadsheet_id, title = found[0]
    _spreadsheet_ids[wanted] = spreadsheet_id
    return spreadsheet_id, title


def sheets_in(spreadsheet_id: str) -> dict[str, dict]:
    """Every sheet in one spreadsheet, by title, with its id and its size.

    batchUpdate works in numeric sheet ids and never in titles, so this is
    what stands between the name a person uses and the number Google wants.
    """
    if spreadsheet_id not in _sheets_in:
        answer = with_retries(
            sheets().spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(sheetId,title,gridProperties))",
            )
        )
        _sheets_in[spreadsheet_id] = {
            sheet["properties"]["title"]: sheet["properties"]
            for sheet in answer.get("sheets", [])
        }

    return _sheets_in[spreadsheet_id]


def resolve_sheet(spreadsheet_id: str, name: str | None = None) -> dict:
    """Pick a sheet out of a spreadsheet by name.

    Returns the first sheet when given nothing, which is the one a spreadsheet
    opens on. Names are matched ignoring case and surrounding spaces, the same
    way workbook.resolve_sheet does it, so " notes " reaches Notes.

    Raises ValueError, naming the sheets that do exist, when the name reaches
    none of them.
    """
    found = sheets_in(spreadsheet_id)

    if not found:
        raise ValueError("This spreadsheet has no sheets.")

    if not name or not name.strip():
        return next(iter(found.values()))

    wanted = name.strip()
    for title, properties in found.items():
        if title.lower() == wanted.lower():
            return properties

    raise ValueError(
        f'There is no sheet called "{name}". The spreadsheet has: '
        f"{', '.join(found)}."
    )


def grid(spreadsheet_id: str, title: str) -> list[list[Cell]]:
    """Read one sheet into rows of cells.

    One call serves every reading tool. valueRenderOption applies to a whole
    request rather than to one range within it, so asking values.batchGet for
    displayed values and formulas at once is not possible; spreadsheets.get
    returns all of it for every cell, and the field mask keeps it to the four
    things worth having.

    Rows are indexed from 0 and ragged: a row reaches only as far as its last
    filled cell. Use cell() rather than indexing into them.
    """
    answer = with_retries(
        sheets().spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            ranges=[title],
            includeGridData=True,
            fields=GRID_FIELDS,
        )
    )

    raw_rows = []
    for sheet in answer.get("sheets", []):
        for data in sheet.get("data", []):
            raw_rows = data.get("rowData", [])

    rows = []
    for raw_row in raw_rows:
        rows.append([as_cell(raw) for raw in raw_row.get("values", [])])

    return rows


def as_cell(raw: dict) -> Cell:
    """Turn one cell of Google's answer into a Cell.

    effectiveValue arrives tagged with its type, one key of numberValue,
    stringValue, boolValue or formulaValue, so which key is there is what says
    whether the cell holds a number or text.
    """
    effective = raw.get("effectiveValue") or {}
    value = None
    for key in ("numberValue", "stringValue", "boolValue"):
        if key in effective:
            value = effective[key]
            break

    return Cell(
        displayed=raw.get("formattedValue"),
        formula=(raw.get("userEnteredValue") or {}).get("formulaValue"),
        value=value,
        number_format=(
            (raw.get("effectiveFormat") or {}).get("numberFormat") or {}
        ).get("type"),
    )


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

    The same rule workbook.is_blank uses, and for the same reason: a cell
    never filled in and one emptied by hand look identical to a reader, so
    they are treated identically here. A zero is not blank.
    """
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def find_header_row(rows: list[list[Cell]], search_depth: int = 10) -> int:
    """Find the row that holds the column names, counting from 1.

    The same walk workbook.find_header_row does: the first row near the top
    with at least two filled cells, all of them text, and something filled in
    the row below. A title above the table fails the two cell test, so a sheet
    that does not start with its header is still read correctly.

    Whether a cell is text is asked of its value and not of what it displays,
    because Google formats every cell into a string for display: a row of
    years would otherwise pass for a header, where the local backend refuses
    it.
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


def header_map(rows: list[list[Cell]], header_row: int) -> dict[str, int]:
    """Map each column name to its column number, counting from 1.

    Looking columns up by name means no column letters are written down, so a
    column that moves does not break a tool. A cell with nothing in it names
    no column and is left out.
    """
    if header_row > len(rows):
        return {}

    return {
        str(one.displayed).strip(): number
        for number, one in enumerate(rows[header_row - 1], start=1)
        if not is_blank(one.displayed)
    }


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


def to_dimension_range(
    sheet_id: int, dimension: str, first: int, last: int | None = None
) -> dict:
    """The range a row or column request wants, counting from 1.

    Used by the requests that insert, delete and move whole rows or columns.
    One row means first and last are the same, which is the common case and
    why last may be left out.

    Google moves a run of rows by where they should land before they are
    lifted out, so moving rows 2 and 3 down to sit after row 5 asks for
    destinationIndex 5, not 3. Whoever builds that request works it out; this
    only says which rows are being moved.
    """
    return {
        "sheetId": sheet_id,
        "dimension": dimension,
        "startIndex": first - 1,
        "endIndex": (last if last is not None else first),
    }
