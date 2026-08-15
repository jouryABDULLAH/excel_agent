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
        """Read the populated grid of one sheet."""
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

        return [
            [
                self._as_cell(raw_cell)
                for raw_cell in row.get("values", [])
            ]
            for row in raw_rows
        ]

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

    def update_cells(
        self,
        spreadsheet_id: str,
        updates: list[dict],
        value_input_option: Literal["USER_ENTERED", "RAW"] = "USER_ENTERED",
    ) -> dict:
        """Update values in one or more A1 ranges.

        Each update is:

            {
                "range": "'Sheet1'!B2",
                "values": [["value"]]
            }

        USER_ENTERED means formulas and values behave like user input.
        USER_ENTERED parses each value as though it were typed into the cell: formulas become live formulas, and text that looks like a number or date is converted and formatted accordingly. Use RAW if values must be stored verbatim.
        """
        return self._google.execute(
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

    def clear_range(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> dict:
        """Clear values from an A1 range."""
        return self._google.execute(
            self._google.sheets
            .spreadsheets()
            .values()
            .clear(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                body={},
            )
        )

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
        """Insert rows before the specified 1-based row."""
        self._validate_positive(start_row)
        self._validate_positive(count)

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
        start_row: int,
        end_row: int,
    ) -> dict:
        """Delete rows in the inclusive 1-based range."""
        self._validate_range(start_row, end_row)

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
            ],
        )

    def move_rows(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_row: int,
        end_row: int,
        destination_row: int,
    ) -> dict:
        """Move rows to a destination position.

        All row positions are 1-based from the application's perspective.
        """
        self._validate_range(start_row, end_row)
        self._validate_positive(destination_row)

        destination_index = destination_row - 1

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "moveDimension": {
                        "source": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": start_row - 1,
                            "endIndex": end_row,
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
        """Insert columns before the specified 1-based column."""
        self._validate_positive(start_column)
        self._validate_positive(count)

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
        end_column: int,
    ) -> dict:
        """Delete columns in the inclusive 1-based range."""
        self._validate_range(start_column, end_column)

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

    def move_columns(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_column: int,
        end_column: int,
        destination_column: int,
    ) -> dict:
        """Move columns to a destination position."""
        self._validate_range(start_column, end_column)
        self._validate_positive(destination_column)

        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "moveDimension": {
                        "source": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_column - 1,
                            "endIndex": end_column,
                        },
                        "destinationIndex": destination_column - 1,
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
        """Apply structural spreadsheet changes atomically."""
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

    def format_range(
        self,
        spreadsheet_id: str,
        sheet_id: int,
        start_row: int,
        end_row: int,
        start_column: int,
        end_column: int,
        user_entered_format: dict,
    ) -> dict:
        """Apply formatting to a rectangular 1-based range."""
        self._validate_range(start_row, end_row)
        self._validate_range(start_column, end_column)

        request = {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": start_column - 1,
                    "endColumnIndex": end_column,
                },
                "cell": {
                    "userEnteredFormat": user_entered_format,
                },
                "fields": "userEnteredFormat",
            }
        }

        return self.batch_update(
            spreadsheet_id,
            [request],
        )

    # ------------------------------------------------------------------
    # Charts
    # ------------------------------------------------------------------

    def list_charts(
        self,
        spreadsheet_id: str,
    ) -> list[dict]:
        """Return charts from all sheets."""
        response = self._google.execute(
            self._google.sheets
            .spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="sheets(properties(title),charts(chartId,spec,position))",
            )
        )

        charts = []

        for sheet in response.get("sheets", []):
            title = sheet.get("properties", {}).get("title")

            for chart in sheet.get("charts", []):
                charts.append(
                    {
                        "sheet": title,
                        **chart,
                    }
                )

        return charts

    def add_chart(
        self,
        spreadsheet_id: str,
        chart_spec: dict,
    ) -> dict:
        """Create a chart using a Google Sheets AddChartRequest."""
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "addChart": {
                        "chart": chart_spec,
                    }
                }
            ],
        )

    def update_chart(
        self,
        spreadsheet_id: str,
        chart_id: int,
        chart_spec: dict,
    ) -> dict:
        """Replace a chart's specification."""
        return self.batch_update(
            spreadsheet_id,
            [
                {
                    "updateChartSpec": {
                        "chartId": chart_id,
                        "spec": chart_spec,
                    }
                }
            ],
        )

    def delete_chart(
        self,
        spreadsheet_id: str,
        chart_id: int,
    ) -> dict:
        """Delete a chart."""
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

    # ------------------------------------------------------------------
    # Cache / conversion helpers
    # ------------------------------------------------------------------

    def invalidate(self, spreadsheet_id: str) -> None:
        """Forget cached sheet metadata for a spreadsheet."""
        self._sheets.pop(spreadsheet_id, None)

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

    @staticmethod
    def _validate_positive(value: int) -> None:
        if value < 1:
            raise ValueError("Row and column numbers must be at least 1.")

    @staticmethod
    def _validate_range(start: int, end: int) -> None:
        if start < 1 or end < 1:
            raise ValueError("Row and column numbers must be at least 1.")

        if start > end:
            raise ValueError(
                f"Invalid range: start ({start}) is greater than end ({end})."
            )


spreadsheet_service = SpreadsheetService()
