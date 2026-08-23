"""Exposes application-level spreadsheet operations over Google Sheets spreadsheets."""

from dataclasses import dataclass
from typing import Any, Literal

from excel_agent.services.google import GoogleAPI, google_api


@dataclass(frozen=True)
class Cell:
    """A cell represented in the forms useful to the application."""

    displayed: str | None = None 
    formula: str | None = None
    value: object = None
    number_format: str | None = None

    @property
    def is_date(self) -> bool:
        return self.number_format in {"DATE", "DATE_TIME"}


EMPTY = Cell()


GRID_FIELDS = (
    "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),"
    "data(rowData(values(formattedValue,userEnteredValue,effectiveValue,"
    "effectiveFormat(numberFormat(type))))))"
)


class SpreadsheetService:
    """High-level operations over Google Sheets."""

    def __init__(self, google: GoogleAPI | None = None) -> None:
        self._google = google or google_api

        # Cached spreadsheet structure:  spreadsheet_id -> sheet title -> sheet properties
        # filled by list_sheets(). cleared by invalidate().
        self._sheets: dict[str, dict[str, dict]] = {}

        # Cached grids: (spreadsheet_id, sheet title) -> rows of cells,
        # filled by read_sheet(), cleared by the same invalidate() every
        # write already calls. Within one turn a read and the write that
        # follows it stop fetching the same sheet twice.
        self._grids: dict[tuple[str, str], list] = {}

    # ------------------------------------------------------------------
    # Spreadsheet / sheet metadata
    # ------------------------------------------------------------------

    def get_spreadsheet(
        self,
        spreadsheet_id: str,
    ) -> dict:
        """Return spreadsheet metadata."""
        return self._google.execute(
            self._google.sheets
            .spreadsheets()
            .get(spreadsheetId=spreadsheet_id)
        )

    def list_sheets(
        self,
        spreadsheet_id: str,
    ) -> dict[str, dict]:
        """Return sheets keyed by title."""
        if spreadsheet_id not in self._sheets:
            response = self._google.execute(
                self._google.sheets
                .spreadsheets()
                .get(
                    spreadsheetId=spreadsheet_id,
                    fields=(
                        "sheets("
                        "properties("
                        "sheetId,"
                        "title,"
                        "gridProperties"
                        ")"
                        ")"
                    ),
                )
            )

            self._sheets[spreadsheet_id] = {
                sheet["properties"]["title"]: sheet["properties"]
                for sheet in response.get("sheets", [])
            }

        return self._sheets[spreadsheet_id]

    def resolve_sheet(
        self,
        spreadsheet_id: str,
        name: str | None = None,
    ) -> dict:
        """Resolve a sheet name to its Google sheet properties."""
        sheets = self.list_sheets(spreadsheet_id)

        if not sheets:
            raise ValueError("This spreadsheet has no sheets.")

        if not name or not name.strip():
            return next(iter(sheets.values()))

        wanted = name.strip()

        for title, properties in sheets.items():
            if title.lower() == wanted.lower():
                return properties

        available = ", ".join(sheets)

        raise ValueError(
            f'There is no sheet called "{name}". '
            f"The spreadsheet has: {available}."
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_sheet(
        self,
        spreadsheet_id: str,
        sheet_name: str,
    ) -> list[list[Cell]]:
        """Read the populated grid of one sheet, from the cache when clean.

        The trade is deliberate: an edit made by hand in Google mid-turn can
        be missed until the next write or turn, and in exchange a turn stops
        downloading the same sheet before every step.
        """
        key = (spreadsheet_id, sheet_name)

        if key in self._grids:
            return self._grids[key]

        response = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                ranges=[sheet_name],
                includeGridData=True,
                fields=GRID_FIELDS,
            )
        )

        raw_rows: list[dict] = []

        for sheet in response.get("sheets", []):
            for data in sheet.get("data", []):
                raw_rows.extend(data.get("rowData", []))

        self._grids[key] = [
            [
                self._as_cell(raw_cell)
                for raw_cell in row.get("values", [])
            ]
            for row in raw_rows
        ]

        return self._grids[key]

    def read_range(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> list[list[Any]]:
        """Read values from an A1 range."""
        response = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
            )
        )

        return response.get("values", [])

    # ------------------------------------------------------------------
    # Value writes
    # ------------------------------------------------------------------

    def append_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
        value_input_option: Literal["USER_ENTERED", "RAW"] = "USER_ENTERED",
    ) -> dict:
        """Append rows after the current table in an A1 range."""
        result = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                insertDataOption="INSERT_ROWS",
                body={
                    "majorDimension": "ROWS",
                    "values": values,
                },
            )
        )

        self.invalidate(spreadsheet_id)
        return result

    def update_cells(
        self,
        spreadsheet_id: str,
        updates: list[dict],
        value_input_option: Literal["USER_ENTERED", "RAW"] = "USER_ENTERED",
    ) -> dict:
        """Update values in one or more A1 ranges."""
        result = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "valueInputOption": value_input_option,
                    "data": updates,
                },
            )
        )

        self.invalidate(spreadsheet_id)
        return result


    def clear_range(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> dict:
        """Clear cell values without clearing formatting."""
        result = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .values()
            .clear(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                body={},
            )
        )

        self.invalidate(spreadsheet_id)
        return result

    # ------------------------------------------------------------------
    # Row operations
    # ------------------------------------------------------------------

# add append rows

    def insert_rows(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_row: int,
        count: int = 1,
    ) -> dict:
        """Insert empty rows before a 1-based row number."""
        if start_row < 1:
            raise ValueError("start_row must be at least 1.")

        if count < 1:
            raise ValueError("count must be at least 1.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": start_row - 1,
                            "endIndex": start_row - 1 + count,
                        },
                        "inheritFromBefore": False,
                    }
                }
            ],
        )

    def delete_rows(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        ranges: list[tuple[int, int]],
    ) -> dict:
        """Delete inclusive ranges of 1-based rows, as one atomic batch.

        Deletions are sent bottom-up so an earlier one never shifts the rows
        a later one names, and Google applies the whole batch or none of it,
        so a failure leaves the sheet untouched.
        """
        if not ranges:
            raise ValueError("At least one row range is required.")

        for start_row, end_row in ranges:
            if start_row < 1 or end_row < start_row:
                raise ValueError("Invalid row range.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": start_row - 1,
                            "endIndex": end_row,
                        }
                    }
                }
                for start_row, end_row in sorted(ranges, reverse=True)
            ],
        )


    def sort_range(
        self,
        spreadsheet_id: str,
        grid_range: dict,
        by_columns: list[tuple[int, bool]],
    ) -> dict:
        """Sort the rows of a grid range by one or more columns.

        by_columns pairs a 1-based column number with whether it sorts
        descending. The conversion to Google's 0-based index happens here,
        beside the rest of that arithmetic, and nowhere above.
        """
        if not by_columns:
            raise ValueError("At least one column to sort by is required.")

        for column, _ in by_columns:
            if column < 1:
                raise ValueError("Column numbers must be at least 1.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "sortRange": {
                        "range": grid_range,
                        "sortSpecs": [
                            {
                                "dimensionIndex": column - 1,
                                "sortOrder": (
                                    "DESCENDING"
                                    if descending
                                    else "ASCENDING"
                                ),
                            }
                            for column, descending in by_columns
                        ],
                    }
                }
            ],
        )

    def move_row(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        row: int,
        to_row: int,
    ) -> dict:
        """Move one row so its final 1-based position is to_row."""
        if row < 1 or to_row < 1:
            raise ValueError("Row numbers must be at least 1.")

        if row == to_row:
            raise ValueError("The source and destination rows are the same.")

        # moveDimension's destination is measured against the grid before
        # the source row is removed.
        destination_index = to_row if to_row > row else to_row - 1

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "moveDimension": {
                        "source": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row - 1,
                            "endIndex": row,
                        },
                        "destinationIndex": destination_index,
                    }
                }
            ],
        )

    # ------------------------------------------------------------------
    # Column operations
    # ------------------------------------------------------------------

    def insert_columns(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_column: int,
        count: int = 1,
    ) -> dict:
        """Insert empty columns before a 1-based column position."""
        if start_column < 1:
            raise ValueError("start_column must be at least 1.")

        if count < 1:
            raise ValueError("count must be at least 1.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_column - 1,
                            "endIndex": start_column - 1 + count,
                        },
                        "inheritFromBefore": False,
                    }
                }
            ],
        )


    def delete_columns(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_column: int,
        end_column: int | None = None,
    ) -> dict:
        """Delete an inclusive range of 1-based columns."""
        end_column = (
            end_column
            if end_column is not None
            else start_column
        )

        if start_column < 1 or end_column < start_column:
            raise ValueError("Invalid column range.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_column - 1,
                            "endIndex": end_column,
                        }
                    }
                }
            ],
        )


    def move_column(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        column: int,
        to_position: int,
    ) -> dict:
        """Move one column so its final 1-based position is to_position."""
        if column < 1 or to_position < 1:
            raise ValueError("Column positions must be at least 1.")

        if column == to_position:
            raise ValueError(
                "The source and destination columns are the same."
            )

        # Google measures destinationIndex against the grid before
        # removing the source column.
        destination_index = (
            to_position
            if to_position > column
            else to_position - 1
        )

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "moveDimension": {
                        "source": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": column - 1,
                            "endIndex": column,
                        },
                        "destinationIndex": destination_index,
                    }
                }
            ],
        )


    def copy_paste(
        self,
        spreadsheet_id: str,
        source: dict,
        destination: dict,
        paste_type: str,
    ) -> dict:
        """Copy one grid range to another using a Sheets paste operation."""
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "copyPaste": {
                        "source": source,
                        "destination": destination,
                        "pasteType": paste_type,
                    }
                }
            ],
        )

    def repeat_cell(
        self,
        spreadsheet_id: str,
        grid_range: dict,
        cell: dict,
        fields: str,
    ) -> dict:
        """Write one cell across a grid range.

        Sheets adjusts a formula's relative references for each cell it lands
        in, so a formula written this way changes from row to row exactly as
        it would if someone had filled it down by hand.
        """
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "repeatCell": {
                        "range": grid_range,
                        "cell": cell,
                        "fields": fields,
                    }
                }
            ],
        )

    # ------------------------------------------------------------------
    # Structural / formatting operations
    # ------------------------------------------------------------------

# add update_fromat

    def batch_update(
        self,
        spreadsheet_id: str,
        requests: list[dict],
    ) -> dict:
        """Apply structural spreadsheet changes as one batch."""
        result = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
        )

        self.invalidate(spreadsheet_id) # drops the cached sheet

        return result

    def freeze(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        rows: int | None = None,
        columns: int | None = None,
    ) -> dict:
        """Freeze a number of rows and columns so they stay on screen."""
        grid: dict = {}

        if rows is not None:
            grid["frozenRowCount"] = rows

        if columns is not None:
            grid["frozenColumnCount"] = columns

        if not grid:
            raise ValueError("Nothing to freeze.")

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": grid,
                        },
                        "fields": ",".join(
                            f"gridProperties.{one}" for one in grid
                        ),
                    }
                }
            ],
        )

    def size_columns(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        first_column: int,
        last_column: int,
        pixels: int | None = None,
    ) -> dict:
        """Set the width of a run of columns, or fit them to their contents.

        pixels None means auto-fit, which is what double-clicking the edge
        of a column does.
        """
        if first_column < 1 or last_column < first_column:
            raise ValueError("Invalid column range.")

        span = {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "startIndex": first_column - 1,
            "endIndex": last_column,
        }

        if pixels is None:
            request = {"autoResizeDimensions": {"dimensions": span}}
        else:
            if pixels < 1:
                raise ValueError("A column must be at least one pixel wide.")

            request = {
                "updateDimensionProperties": {
                    "range": span,
                    "properties": {"pixelSize": pixels},
                    "fields": "pixelSize",
                }
            }

        return self.batch_update(spreadsheet_id, [request])

    def format_range(
        self,
        spreadsheet_id: str,
        ranges: list[dict],
        cell_format: dict,
        fields: list[str],
    ) -> dict:
        """Apply the same cell format to one or more grid ranges."""
        if not ranges:
            raise ValueError("At least one range is required.")

        if not fields:
            raise ValueError("At least one formatting field is required.")

        requests = [
            {
                "repeatCell": {
                    "range": grid_range,
                    "cell": {
                        "userEnteredFormat": cell_format,
                    },
                    "fields": ",".join(fields),
                }
            }
            for grid_range in ranges
        ]

        return self.batch_update(
            spreadsheet_id,
            requests,
        )

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------

    def add_chart(
        self,
        spreadsheet_id: str,
        chart: dict,
    ) -> dict:
        """Add one embedded chart and return Google's response."""
        result = self.batch_update(
            spreadsheet_id,
            [
                {
                    "addChart": {
                        "chart": chart,
                    }
                }
            ],
        )

        replies = result.get("replies") or []

        if not replies:
            return {}

        return replies[0].get("addChart", {}).get("chart", {})


    def update_chart_spec(
        self,
        spreadsheet_id: str,
        chart_id: int,
        spec: dict,
    ) -> dict:
        """Replace the specification of one embedded chart."""
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "updateChartSpec": {
                        "chartId": chart_id,
                        "spec": spec,
                    }
                }
            ],
        )


    def delete_chart(
        self,
        spreadsheet_id: str,
        chart_id: int,
    ) -> dict:
        """Delete one embedded chart by its stable chart ID."""
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "deleteEmbeddedObject": {
                        "objectId": chart_id,
                    }
                }
            ],
        )

    def list_charts(
        self,
        spreadsheet_id: str,
        sheet_name: str | None = None,
    ) -> list[dict]:
        """Return embedded charts, optionally from one sheet only.

        Masked rather than reusing get_spreadsheet, which asks for the whole
        spreadsheet: this runs on every sheet read, including reads that have
        nothing to do with charts.
        """
        spreadsheet = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields=(
                    "sheets("
                    "properties(sheetId,title),"
                    "charts(chartId,spec)"
                    ")"
                ),
            )
        )

        charts = []

        for sheet in spreadsheet.get("sheets", []):
            properties = sheet.get("properties", {})
            title = properties.get("title")

            if sheet_name is not None and title != sheet_name:
                continue

            for chart in sheet.get("charts", []) or []:
                charts.append(
                    {
                        **chart,
                        "sheet": title,
                        "sheetId": properties.get("sheetId"),
                    }
                )

        return charts
    
    # ------------------------------------------------------------------
    # Cache / conversion helpers
    # ------------------------------------------------------------------

    def invalidate(self, spreadsheet_id: str) -> None:
        """Forget what is cached about a spreadsheet a write may have moved."""
        self._sheets.pop(spreadsheet_id, None)

        for key in [
            one for one in self._grids if one[0] == spreadsheet_id
        ]:
            self._grids.pop(key, None)

    @staticmethod
    def _as_cell(raw: dict) -> Cell:
        """Convert Google's cell representation into Cell."""
        effective = raw.get("effectiveValue") or {}

        value = None

        for key in (
            "numberValue",
            "stringValue",
            "boolValue",
        ):
            if key in effective:
                value = effective[key]
                break

        number_format = (
            (raw.get("effectiveFormat") or {})
            .get("numberFormat") or {}
        ).get("type")

        return Cell(
            displayed=raw.get("formattedValue"),
            formula=(
                raw.get("userEnteredValue") or {}
            ).get("formulaValue"),
            value=value,
            number_format=number_format,
        )

spreadsheet_service = SpreadsheetService()
