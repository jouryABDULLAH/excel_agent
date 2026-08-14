"""Tool for how the sheet looks.

Number formats, weight and colour, over a column or a run of rows.
"""

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent.sheets import (
    batch,
    find_header_row,
    grid,
    header_map,
    last_data_row,
    readable,
    resolve_sheet,
    resolve_spreadsheet,
    to_grid_range,
)

# The colours a person is likely to ask for by name. Anything else has to be
# given as a hex code, which is said in the refusal rather than guessed at.
COLOURS = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#f4cccc",
    "orange": "#fce5cd",
    "yellow": "#fff2cc",
    "green": "#d9ead3",
    "blue": "#cfe2f3",
    "purple": "#d9d2e9",
    "grey": "#efefef",
    "gray": "#efefef",
}


def as_colour(given: str) -> dict | None:
    """Turn a colour name or hex code into what the API wants, or None.

    Google takes each part as a fraction of one rather than as 0 to 255, so
    the arithmetic here is the whole of the conversion.
    """
    text = given.strip().lower()
    text = COLOURS.get(text, text)

    if not text.startswith("#") or len(text) != 7:
        return None

    try:
        parts = [int(text[first : first + 2], 16) / 255 for first in (1, 3, 5)]
    except ValueError:
        return None

    return {"red": parts[0], "green": parts[1], "blue": parts[2]}


def as_number_format(pattern: str) -> dict:
    """Work out which sort of format a pattern is, from the pattern itself.

    Google wants a type as well as a pattern, and refuses a date pattern
    labelled as a number. What the pattern is made of is enough to tell.
    """
    lowered = pattern.lower()

    if "%" in pattern:
        kind = "PERCENT"
    elif any(part in lowered for part in ("yyyy", "yy", "mmm", "dd")):
        kind = "DATE"
    elif any(part in pattern for part in ("$", "£", "€")):
        kind = "CURRENCY"
    else:
        kind = "NUMBER"

    return {"type": kind, "pattern": pattern}


@tool
def modify_style(
    column: str | None = None,
    first_row: int | None = None,
    last_row: int | None = None,
    number_format: str | None = None,
    bold: bool | None = None,
    background: str | None = None,
    spreadsheet: str | None = None,
    sheet: str | None = None,
) -> str:
    """Change how cells are displayed, without changing what they hold.

    Args:
        column: The column to style, by the name in its header. Leave this
            out to style whole rows.
        first_row: The first row to style. Leave this out to style the whole
            column, header included.
        last_row: The last row to style. Leave this out to reach the bottom
            of the data.
        number_format: How numbers should read, written the way Google Sheets
            writes it, such as "#,##0.00" or "0%" or "dd/mm/yyyy".
        bold: Whether the text should be bold.
        background: The fill colour, as a name such as "yellow" or a hex code
            such as "#fff2cc".
        spreadsheet: Which spreadsheet to change, by name. Leave this out to
            change the one being worked on.
        sheet: Which sheet to change, by name. Leave this out to change the
            first sheet in the spreadsheet.

    Returns:
        A sentence saying what changed, or an explanation of why nothing was
        changed. This never changes a value: a column shown as 0% holds the
        same number it held before.

    Examples:
        modify_style(column="Revenue", number_format="#,##0.00")
        modify_style(first_row=1, last_row=1, bold=True, background="yellow")
        modify_style(column="Notes", background="#fff2cc")
    """
    if number_format is None and bold is None and background is None:
        return (
            "Say what to change: a number_format, bold, or a background "
            "colour."
        )

    fill = None
    if background is not None:
        fill = as_colour(background)
        if fill is None:
            return (
                f'"{background}" is not a colour I can read. Use a name '
                f"({', '.join(sorted(set(COLOURS)))}) or a hex code such as "
                '"#fff2cc".'
            )

    try:
        spreadsheet_id, name = resolve_spreadsheet(spreadsheet)
        properties = resolve_sheet(spreadsheet_id, sheet)
        rows = grid(spreadsheet_id, properties["title"])
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    sheet_id = properties["sheetId"]
    where = f"({properties['title']} in {name})"

    header_row = find_header_row(rows)
    headers = header_map(rows, header_row)
    end_of_data = last_data_row(rows, header_row)

    number = None
    if column is not None:
        if column not in headers:
            return (
                f'There is no column called "{column}". '
                f"The sheet has: {', '.join(headers)}. {where}"
            )
        number = headers[column]

    if column is None and first_row is None:
        return (
            "Say which cells to change: a column, or a first_row and "
            "last_row, or both."
        )

    first = first_row if first_row is not None else header_row
    end = last_row if last_row is not None else end_of_data

    if first < 1 or end < first:
        return (
            f"Rows {first} to {end} are not a run of rows. The sheet has rows "
            f"{header_row} to {end_of_data}. {where}"
        )

    style: dict = {}
    fields = []
    changed = []

    if number_format is not None:
        style["numberFormat"] = as_number_format(number_format)
        fields.append("userEnteredFormat.numberFormat")
        changed.append(f"shown as {number_format}")
    if bold is not None:
        style["textFormat"] = {"bold": bold}
        fields.append("userEnteredFormat.textFormat.bold")
        changed.append("bold" if bold else "not bold")
    if fill is not None:
        style["backgroundColor"] = fill
        fields.append("userEnteredFormat.backgroundColor")
        changed.append(f"filled {background}")

    try:
        batch(
            spreadsheet_id,
            [
                {
                    "repeatCell": {
                        "range": to_grid_range(sheet_id, first, end, number, number),
                        "cell": {"userEnteredFormat": style},
                        # Only the parts named here are touched, so setting a
                        # colour does not quietly clear a number format set
                        # earlier.
                        "fields": ",".join(fields),
                    }
                }
            ],
        )
    except HttpError as failure:
        return readable(failure)

    what = f'"{column}"' if column else "every column"
    return (
        f"Rows {first} to {end} of {what} are now {', '.join(changed)}. The "
        f"values themselves are unchanged. {where}"
    )


CASES = [
    ("nothing to change", {"column": "Region"}),
    ("nowhere to change it", {"bold": True}),
    ("a colour that is not one", {"column": "Region", "background": "octarine"}),
    ("a column that does not exist", {"column": "Nonsense", "bold": True}),
]


def main() -> None:
    """Try the refusals by hand with `python -m excel_agent.tools.style`.

    Only the calls that change nothing are here. Anything that writes belongs
    in a scratch spreadsheet, run by hand.
    """
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(modify_style.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
