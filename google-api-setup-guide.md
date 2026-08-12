# Google APIs Setup Guide (Personal Project, Desktop OAuth)

A generic, one-time setup for any project that needs to read or write a user's own
Google data — Calendar, Gmail, Drive, Sheets, Tasks, etc. — from a script running on
your own machine.

**Assumes:** Python, a personal Google account, and an app that runs locally (CLI,
script, or local server). Not for a hosted multi-user web app — that needs a Web
application client and a different redirect setup.

**Time:** 30–60 minutes, mostly clicking through consoles. Do it before you start
writing real code; the browser-consent dance is the single most common place to lose
an afternoon.

---

## 0. Install the client libraries

```bash
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv
pip freeze > requirements.txt
```

Three packages do three different jobs, and you need all of them:
`google-api-python-client` builds the service objects, `google-auth-oauthlib` runs the
consent flow, `google-auth-httplib2` is the transport glue.

> **Windows / PowerShell:** if `Activate.ps1` errors with an execution-policy message,
> run this once per machine as your normal user (no admin needed), then re-run activate:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com>
2. Project dropdown (top bar) → **New Project** → name it after your app → Create
3. Make sure the new project is selected in that dropdown before doing anything else.
   Enabling an API in the wrong project is a classic 20-minute detour.

There's no billing requirement for the APIs listed below at personal-use volume.

---

## 2. Enable the APIs you need

**APIs & Services → Library** → search each one → **Enable**.

Enable only what you'll actually call. Common ones:

| Service | API to enable |
|---|---|
| Calendar | Google Calendar API |
| Email | Gmail API |
| Files | Google Drive API |
| Spreadsheets | Google Sheets API |
| Docs | Google Docs API |
| To-dos | Google Tasks API |
| Contacts | People API |

If you get a `403 ... has not been used in project N before or it is disabled`, this
step is what you missed. The error text includes a direct enable link.

---

## 3. Configure the OAuth consent screen

Under **APIs & Services → OAuth consent screen** (recent consoles label this
**Google Auth Platform**; the UI moves around, the concepts don't).

- **User type: External.** "Internal" only exists if you're on Google Workspace and
  want to restrict to your own organization. A personal `@gmail.com` account must use
  External.
- Fill in app name, your email as support contact, your email as developer contact.
  Everything else is optional for testing.
- **Add yourself as a Test user.** This is the important one. While the app's
  publishing status is *Testing*, only listed test users can authorize it — and you
  need no app verification at all. You never have to publish or get verified for a
  personal project.

> ⚠️ **The 7-day gotcha.** While in *Testing* status, refresh tokens expire after 7
> days. Your saved token file will suddenly stop working and you'll have to re-consent
> through the browser. This is expected and takes 10 seconds — just don't panic and
> assume you broke your auth code. If it becomes annoying and your scopes are
> non-sensitive, you can move the app to *In production*.

---

## 4. Create the OAuth client → `credentials.json`

**APIs & Services → Credentials → Create Credentials → OAuth client ID**

- **Application type: Desktop app** ← important. Desktop clients use a localhost
  loopback redirect, which the Python library handles automatically. You do not need
  to configure any redirect URIs.
- Create → **Download JSON** → save it as `credentials.json` in your project root.

**Immediately add both secret files to `.gitignore`:**

```gitignore
credentials.json
token.json
.env
```

These are credentials to your real account. Never commit them.

---

## 5. Pick your scopes

Scopes are what you're asking permission for. Two rules that will save you pain:

1. **Request every scope you'll eventually need, in the very first consent flow.**
   Adding one later invalidates the cached token and forces re-consent.
2. **Request the narrowest scope that does the job.** Broad scopes mean scarier
   consent screens, and matter if you ever publish.

Common ones:

| Scope | Grants |
|---|---|
| `.../auth/calendar.readonly` | read events |
| `.../auth/calendar` | read + create/edit/delete events |
| `.../auth/gmail.readonly` | read messages and full bodies |
| `.../auth/gmail.compose` | create/manage drafts (limited read) |
| `.../auth/gmail.send` | send mail |
| `.../auth/gmail.modify` | read + labels + modify, no permanent delete |
| `.../auth/drive.readonly` | read all Drive files |
| `.../auth/drive.file` | only files your app creates or the user opens with it |
| `.../auth/spreadsheets` | read + write Sheets |
| `.../auth/tasks` | read + write Tasks |

All are prefixed `https://www.googleapis.com/auth/`.

Notes worth knowing:

- `gmail.compose` lets you create drafts but gives you thin read access. If you need
  full message bodies to build those drafts, request `gmail.readonly` **alongside** it.
- `drive.file` is dramatically less invasive than `drive.readonly` and is enough for
  many apps. Try it first.
- Gmail and full-Drive scopes are classified *restricted* by Google. Fine in Testing
  mode forever; a real verification process if you ever publish publicly.

---

## 6. The auth module (`auth.py`)

Drop this in as-is. It caches to `token.json`, refreshes silently when it can, and
only opens a browser when it truly must.

```python
"""Google OAuth for a local/desktop app. One consent, cached token, auto-refresh."""
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


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
```

Define your scopes in exactly one place and import them everywhere:

```python
# scopes.py
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
]
```

Mismatched scope lists across files cause silent re-consent loops that are genuinely
confusing to debug. One source of truth.

---

## 7. Verify it works — before writing anything else

Run a throwaway script that does one real read. If this doesn't print real data from
your account, stop and fix it; everything downstream depends on it.

```python
from googleapiclient.discovery import build
from auth import get_credentials
from scopes import SCOPES

creds = get_credentials(SCOPES)

# Calendar: next 5 events
svc = build("calendar", "v3", credentials=creds)
events = svc.events().list(
    calendarId="primary", maxResults=5,
    singleEvents=True, orderBy="startTime",
    timeMin="2026-01-01T00:00:00Z",
).execute()
print([e.get("summary") for e in events.get("items", [])])

# Gmail: 5 most recent message IDs
gmail = build("gmail", "v1", credentials=creds)
msgs = gmail.users().messages().list(userId="me", maxResults=5).execute()
print(len(msgs.get("messages", [])), "messages found")

# Drive: 5 files
drive = build("drive", "v3", credentials=creds)
files = drive.files().list(pageSize=5, fields="files(id,name,mimeType)").execute()
print([f["name"] for f in files.get("files", [])])
```

First run opens a browser. You'll see **"Google hasn't verified this app"** — that's
normal for a Testing-status app. Click **Advanced → Go to \<app name\> (unsafe)** and
continue. It's your own app and your own account.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `403: API has not been used in project...` | API not enabled (Step 2). The error links straight to the enable page. |
| `Error 403: access_denied` at consent | Your account isn't in the Test users list (Step 3). |
| `Error 400: redirect_uri_mismatch` | You created a *Web application* client instead of *Desktop app*. Make a new Desktop client. |
| Re-prompted for consent every run | `token.json` isn't being written — check write permissions and the path. |
| `Scope has changed` / `ValueError` on token load | You edited your scope list. Delete `token.json` and re-consent. |
| Worked for a week, now fails | The Testing-mode 7-day refresh-token expiry. Delete `token.json`, re-consent. |
| No `refresh_token` in the saved token | Google only returns it on first consent. Delete `token.json` (or revoke app access at <https://myaccount.google.com/permissions>) and redo the flow. |
| `insufficientPermissions` on a call | Scope too narrow for that operation. Add the right scope, delete `token.json`, re-consent. |
| Browser doesn't open (WSL/remote/headless) | Use `flow.run_console()` on older lib versions, or run the consent step on a machine with a browser and copy `token.json` over. |

---

## 9. Practical habits

- **Wrap every API call in try/except** and return a readable message rather than
  letting a stack trace escape. Google APIs fail for transient reasons.
- **Handle empty results explicitly** — no events, no matching files, no messages.
  These are normal, not errors.
- **Watch quotas.** Generous for personal use, but Gmail in particular is easy to
  hammer in a loop. Batch where you can, and don't poll in a tight loop.
- **Keep `credentials.json` and `token.json` out of git.** Worth saying twice.
- **Revoking access** is at <https://myaccount.google.com/permissions>. Useful when you
  want to test the first-run consent flow from a clean state.

---

## Checklist

- [ ] Client libraries installed, `requirements.txt` pinned
- [ ] Cloud project created and **selected**
- [ ] Needed APIs enabled
- [ ] Consent screen: External, own email added as **Test user**
- [ ] **Desktop app** OAuth client created, `credentials.json` in project root
- [ ] `credentials.json`, `token.json`, `.env` in `.gitignore`
- [ ] All scopes decided up front and defined in one place
- [ ] `auth.py` written, verification script prints real account data
