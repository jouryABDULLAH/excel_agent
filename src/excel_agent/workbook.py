"""Enables Excel file access for the agent.

This module holds the rules for opening and saving the workbook so that the
tools do not have to repeat them.
"""

import os
import threading
from pathlib import Path
from shutil import copyfile

import pandas as pd
from openpyxl import load_workbook
from openpyxl.formula.translate import Translator

from excel_agent.config import WORKBOOK_PATH

WRITE_LOCK = threading.RLock()

# Used when no row near the top of the sheet looks like a header.
DEFAULT_HEADER_ROW = 1


HEADER_SEARCH_DEPTH = 10

# Backup once in a session
_backup_state = {"taken": False}


def load_values(path: Path = WORKBOOK_PATH):
    """Open the workbook with formula results in place of the formulas.

    Used for reading values. The returned workbook is tagged as read only,
    because saving it would write the cached values over the formulas.
    """
    book = load_workbook(path, data_only=True)

    setattr(book, "_agent_read_only", True)
    return book


def load_book(path: Path = WORKBOOK_PATH):
    """Open the workbook for editing, with the formulas kept as formulas."""
    return load_workbook(path)


def find_header_row(sheet, search_depth: int = HEADER_SEARCH_DEPTH) -> int:
    """Find the row that holds the column names.

    Walks down from the top and takes the first row with at least two filled
    cells, all of them text, and something filled in the row below it. A
    title in A1 fails the two cell test and blank rows fail it as well, so
    sheets that do not start with their header are still read correctly.

    Falls back to DEFAULT_HEADER_ROW when no row near the top looks like one.
    """
    for row in range(1, min(search_depth, sheet.max_row) + 1):
        filled = [cell.value for cell in sheet[row] if cell.value is not None]

        if len(filled) < 2:
            continue
        if not all(isinstance(value, str) for value in filled):
            continue
        if row >= sheet.max_row:
            continue
        if not any(cell.value is not None for cell in sheet[row + 1]):
            continue

        return row

    return DEFAULT_HEADER_ROW


def header_map(sheet, header_row: int) -> dict[str, int]:
    """Map each column name in the header row to its Excel column number.

    Looking columns up by name means no column letters are hardcoded, so a
    column moving in the file does not break the tools. Names are stripped,
    because a header typed as "Total " would otherwise only match when the
    trailing space is included.
    """
    return {
        str(cell.value).strip(): cell.column
        for cell in sheet[header_row]
        if cell.value is not None
    }


def last_data_row(sheet, header_row: int) -> int:
    """Find the last row that actually holds data.

    sheet.max_row counts rows that carry only formatting, so a sheet styled
    well past its contents reports far more rows than it has. Walking back up
    from there finds the last row with something in it, and returns the
    header row itself when the sheet holds no data at all.
    """
    for row in range(sheet.max_row, header_row, -1):
        if any(cell.value is not None for cell in sheet[row]):
            return row

    return header_row


def formula_columns(sheet, header_row: int, last_row: int) -> set[int]:
    """Column numbers whose values are worked out by a formula.

    Read from the last row of data, on the basis that a calculated column is
    calculated the whole way down. Used to stop a caller writing a number
    into a cell the sheet works out for itself.
    """
    if last_row <= header_row:
        return set()

    return {
        cell.column
        for cell in sheet[last_row]
        if isinstance(cell.value, str) and cell.value.startswith("=")
    }


def copy_row_formulas(
    sheet,
    source_row: int,
    target_row: int,
    skip: set[int] | None = None,
) -> list[int]:
    """Copy every formula in one row down into another row.

    Used when adding a row, so a new row picks up whatever calculated columns
    the sheet already has. Cell references are shifted to match the new row,
    so a formula reading =D11*E11 becomes =D12*E12.

    Columns listed in skip are left alone, which is how a value the caller
    asked for avoids being overwritten by a formula.

    Returns the column numbers that received a formula.
    """
    skip = skip or set()
    copied = []

    for cell in sheet[source_row]:
        if cell.column in skip:
            continue
        if not isinstance(cell.value, str) or not cell.value.startswith("="):
            continue

        target = sheet.cell(row=target_row, column=cell.column)
        target.value = Translator(
            cell.value, origin=cell.coordinate
        ).translate_formula(target.coordinate)
        copied.append(cell.column)

    return copied


def backup_once(path: Path = WORKBOOK_PATH) -> Path | None:
    """Copy the workbook next to itself the first time it is called.

    Returns the backup path, or None if a backup was already taken this
    session. This runs before the first write so a bad edit is recoverable.
    """
    with WRITE_LOCK:
        if _backup_state["taken"]:
            return None

        backup_path = path.with_suffix(path.suffix + ".bak")
        copyfile(path, backup_path)
        _backup_state["taken"] = True
        return backup_path


def save(book, path: Path = WORKBOOK_PATH) -> None:
    """Save an editable workbook, taking a backup first.

    Raises ValueError if given a workbook came from load_values, since
    saving that one would destroy every formula in the file.

    Writes to a temporary file and then renames it over the real one. The
    rename is a single step as far as the operating system is concerned, so
    anything reading the file sees either the old version or the new one,
    never half of each.
    """
    if getattr(book, "_agent_read_only", False):
        raise ValueError(
            "This workbook was opened for reading only. "
            "Use load_book() to make changes."
        )

    with WRITE_LOCK:
        backup_once(path)

        being_written = path.with_suffix(path.suffix + ".writing")
        book.save(being_written)
        os.replace(being_written, path)