"""Service for finding and resolving Google Drive spreadsheets.

This module own searching for spreadsheets, searching for spreadsheet contents, and resolving spreadsheets names to IDs.
"""

from excel_agent.services.google import GoogleAPI, google_api


SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


def quoted(text: str) -> str:
    """Escape text before putting it into a Google Drive query."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


class DriveService:
    """Application-level operations over Google Drive spreadsheets."""

    def __init__(self, google: GoogleAPI | None = None) -> None:
        self._google = google or google_api
        self._spreadsheet_ids: dict[str, str] = {}

    def search_spreadsheets(
        self,
        name: str | None = None,
    ) -> list[tuple[str, str]]:
        """Find spreadsheets whose names contain the given text.

        Returns:
            A list of (spreadsheet_id, spreadsheet_name) pairs.
        """
        query = (
            f"mimeType = '{SPREADSHEET_MIME}' "
            "and trashed = false"
        )

        if name:
            query += f" and name contains '{quoted(name)}'"

        response = self._google.execute(
            self._google.drive.files().list(
                q=query,
                pageSize=50,
                fields="files(id,name)",
                orderBy="name",
            )
        )

        return [
            (file["id"], file["name"])
            for file in response.get("files", [])
        ]

    def search_spreadsheets_by_content(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        """Find spreadsheets whose indexed contents contain the given text."""
        query = (
            f"mimeType = '{SPREADSHEET_MIME}' "
            "and trashed = false "
            f"and fullText contains '{quoted(text)}'"
        )

        response = self._google.execute(
            self._google.drive.files().list(
                q=query,
                pageSize=25,
                fields="files(id,name)",
            )
        )

        return [
            (file["id"], file["name"])
            for file in response.get("files", [])
        ]

    def resolve_spreadsheet(
        self,
        name: str,
    ) -> tuple[str, str]:
        """Resolve a spreadsheet name to exactly one spreadsheet.

        An exact title match wins over partial matches. If multiple files have
        the same exact title, resolution fails rather than choosing one.
        """
        wanted = name.strip()

        if not wanted:
            raise ValueError("A spreadsheet name is required.")

        if wanted in self._spreadsheet_ids:
            spreadsheet_id = self._spreadsheet_ids[wanted]
            return spreadsheet_id, wanted

        found = self.search_spreadsheets(wanted)

        if not found:
            raise ValueError(
                f'There is no spreadsheet called "{wanted}". '
                "Call list_workbooks to see the spreadsheets available."
            )

        exact = [
            (spreadsheet_id, title)
            for spreadsheet_id, title in found
            if title.strip().lower() == wanted.lower()
        ]

        if len(exact) > 1:
            names = ", ".join(title for _, title in exact)

            raise ValueError(
                f'More than one spreadsheet is called "{wanted}": {names}. '
                "Say which one by its ID."
            )

        if not exact and len(found) > 1:
            names = ", ".join(title for _, title in found)

            raise ValueError(
                f'No spreadsheet is called exactly "{wanted}". '
                f"These spreadsheets contain that name: {names}. "
                "Say which one by its full name."
            )

        spreadsheet_id, title = exact[0] if exact else found[0]

        self._spreadsheet_ids[wanted] = spreadsheet_id

        return spreadsheet_id, title

    def forget(self, spreadsheet_id: str) -> None:
        """Forget cached information about a spreadsheet.

        Nothing calls this yet. An id does not change while the agent runs, so
        a write is no reason to drop one; it is here for a rename or a delete,
        which is the only way what is remembered here can go wrong.
        """
        stale_names = [
            name
            for name, cached_id in self._spreadsheet_ids.items()
            if cached_id == spreadsheet_id
        ]

        for name in stale_names:
            self._spreadsheet_ids.pop(name, None)