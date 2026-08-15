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
        start_row: int,
        end_row: int | None = None,
    ) -> dict:
        """Delete an inclusive range of 1-based rows."""
        end_row = end_row if end_row is not None else start_row

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
