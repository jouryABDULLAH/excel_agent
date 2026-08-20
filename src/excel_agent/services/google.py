"""Low-level Google API infrastructure.

This module owns Google API clients, authentication, retries, and conversion
of Google API errors into messages that can be handled by higher layers.

It does not contain spreadsheet or Drive business logic.
"""


import random
import ssl
import time
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from excel_agent.auth import get_credentials
from excel_agent.scopes import SCOPES


RETRY_ON = (429, 500, 502, 503, 504)

# The same transient category at the transport level: a connection Google or
# the network dropped mid-request. Left out, one blip failed the request
# instantly while a 503 would have been retried.
TRANSPORT_ERRORS = (ssl.SSLError, ConnectionError, TimeoutError)

MAX_ATTEMPTS = 5
MAX_BACKOFF = 32.0


class GoogleAPI:
    """Shared access to the Google Drive and Sheets API."""

    def __init__(self) -> None:
        self._services: dict[str, Any] = {}

    def service(self, api: str, version: str) -> Any:
        """Return a cached Google API service client."""
        key = f"{api}:{version}"

        if key not in self._services:
            self._services[key] = build(
                api,
                version,
                credentials=get_credentials(SCOPES),
                cache_discovery=False,
            )

        return self._services[key]

    @property
    def sheets(self) -> Any:
        """Google Sheets API v4 client."""
        return self.service("sheets", "v4")

    @property
    def drive(self) -> Any:
        """Google Drive API v3 client."""
        return self.service("drive", "v3")

    def execute(self, request: Any) -> Any:
        """Execute a Google API request with retries for transient failures."""
        for attempt in range(MAX_ATTEMPTS):
            try:
                return request.execute()

            except HttpError as failure:
                status = getattr(failure.resp, "status", None)

                if status not in RETRY_ON or attempt == MAX_ATTEMPTS - 1:
                    raise

                waiting = min(
                    2**attempt + random.random(),
                    MAX_BACKOFF,
                )
                time.sleep(waiting)

            except TRANSPORT_ERRORS:
                if attempt == MAX_ATTEMPTS - 1:
                    raise

                waiting = min(
                    2**attempt + random.random(),
                    MAX_BACKOFF,
                )
                time.sleep(waiting)

        raise RuntimeError("Unreachable: request loop neither returned nor raised.")


def readable(failure: HttpError) -> str:
    """Turn a Google API error into a message suitable for the application."""
    status = getattr(failure.resp, "status", None)
    detail = getattr(failure, "reason", None) or str(failure)

    if status == 401:
        return (
            "Google would not accept the saved sign in. Delete token.json and "
            "run the agent again to sign in afresh."
        )

    if status == 403:
        return f"Google refused the request: {detail}"

    if status == 404:
        return (
            "That resource does not exist, or the signed in account "
            "cannot access it."
        )

    if status == 400:
        return f"Google rejected the request as malformed: {detail}."

    return f"Google returned an error: {detail}."


# One shared instance for the application.
google_api = GoogleAPI()