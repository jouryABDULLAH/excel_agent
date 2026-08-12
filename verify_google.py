"""A throwaway script that does one real read."""

from googleapiclient.discovery import build
# from excel_agent.google_auth import get_credentials
# from excel_agent.google_scopes import SCOPES
from auth import get_credentials
from scopes import SCOPES

creds = get_credentials(SCOPES)

drive = build("drive", "v3", credentials=creds)
files = drive.files().list(pageSize=5, fields="files(id,name,mimeType)").execute().get("files", [])
print("Drive files:", [f["name"] for f in files])

spreadsheets = [f for f in files if f["mimeType"] == "application/vnd.google-apps.spreadsheet"]
if spreadsheets:
    sheets = build("sheets", "v4", credentials=creds)
    sid = spreadsheets[0]["id"]
    result = sheets.spreadsheets().get(spreadsheetId=sid, fields="properties.title").execute()
    print("Opened spreadsheet via Sheets API:", result["properties"]["title"])
else:
    print("No spreadsheets found among the first 5 Drive files — Sheets scope not exercised, but Drive scope worked.")
