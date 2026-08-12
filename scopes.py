"""Single source of truth for Google OAuth scopes.

Import SCOPES everywhere a Google credential is requested. Adding a scope
here invalidates the cached token.json and forces re-consent, so list
everything the agent will ever need up front.
"""

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    # "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]
