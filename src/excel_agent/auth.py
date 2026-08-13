"""Google OAuth for a local/desktop app. One consent, cached token, auto-refresh."""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from excel_agent.config import PROJECT_ROOT

# Named from the project root rather than from wherever the process was
# started, so a browser launched from any directory finds the same token.
CREDENTIALS_FILE = str(PROJECT_ROOT / "credentials.json")
TOKEN_FILE = str(PROJECT_ROOT / "token.json")


def get_credentials(scopes: list[str]) -> Credentials:
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)
        except ValueError:
            # Saved token's scopes don't match what's being asked for now.
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None  # refresh token revoked or expired -> full re-consent
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
            creds = flow.run_local_server(port=0)  # port=0 = pick any free port

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds