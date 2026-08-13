"""Sheets built by hand, so the reading tools can be tested without Google.

A Cell arrives from Google with what it displays, what it works out to, and
what was typed in. Making them here rather than fetching them is what lets a
test say exactly what a sheet holds, including the shapes that are awkward to
create on purpose in a real spreadsheet.
"""

from excel_agent.sheets import Cell


def text(shown: str) -> Cell:
    """A cell holding words."""
    return Cell(displayed=shown, value=shown)


def number(value: float, shown: str | None = None) -> Cell:
    """A cell holding a number, displayed the way the sheet formats it."""
    return Cell(displayed=shown if shown is not None else str(value), value=value)


def date(shown: str, serial: float = 45000) -> Cell:
    """A cell the sheet is treating as a date."""
    return Cell(displayed=shown, value=serial, number_format="DATE")


def calculated(shown: str | None, formula: str, value: object = None) -> Cell:
    """A cell whose value the sheet works out, and what it shows for it."""
    return Cell(displayed=shown, formula=formula, value=value)


EMPTY = Cell()


def orders() -> list[list[Cell]]:
    """A small sheet shaped like the demo one: header, then five orders."""
    rows = [[text("Order ID"), text("Region"), number(0, "Units"), text("Product")]]
    rows[0][2] = text("Units")

    for order, region, units, product in (
        ("ORD-1001", "North", 1, "Laptop"),
        ("ORD-1002", "South", 2, "Monitor"),
        ("ORD-1003", "North", 3, "Keyboard"),
        ("ORD-1004", "East", 4, "Laptop Stand"),
        ("ORD-1005", "West", 5, "Webcam"),
    ):
        rows.append([text(order), text(region), number(units), text(product)])

    return rows
