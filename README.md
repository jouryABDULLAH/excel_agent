# excel_agent

An agentic system driven by natural language that translates text into actions
(add, edit, remove) on the Google spreadsheets in your Drive. Ask in plain
words — English or Arabic — and it finds, reads, edits, formats and charts
your data, asking for your approval before anything destructive.

## What you need

- Python 3.10 or newer
- A Google account with spreadsheets in its Drive
- A [Groq](https://console.groq.com/) API key (the model runs there)

## Install

```powershell
pip install -e .[ui,dev]
```

`ui` brings Streamlit for the browser page; `dev` brings pytest. Leave either
out if you don't need it.

## Google credentials

The agent works on Google Sheets and nothing else, so it needs to be allowed
into your Drive before it can do anything. This is a one-time setup that
leaves a `credentials.json` beside this file:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and
   create a project (any name).
2. Under **APIs & Services → Library**, enable two APIs: **Google Sheets
   API** and **Google Drive API**.
3. Under **APIs & Services → OAuth consent screen**, configure the consent
   screen: External, fill in the app name and your email, and add your own
   Google account under **Test users**.
4. Under **APIs & Services → Credentials**, create an **OAuth client ID** of
   type **Desktop app**, and download its JSON.
5. Save that file as `credentials.json` in this project's root folder,
   next to this README.

The first run opens a browser asking for consent and writes a `token.json`
next to it, so later runs ask nothing. Both files are gitignored — never
commit them.

Two things worth knowing:

- **The token expires after 7 days** while the consent screen is in Testing
  status. When that happens the app asks for consent again on the next
  start — this is Google's policy for unpublished apps, not a bug. Publishing
  the consent screen removes the limit.
- **The scopes are deliberately minimal**: read-and-write on spreadsheet
  contents, read-only on Drive. The agent can edit what's inside your
  sheets, but it cannot create, delete or share files in your Drive.

## Configure

Copy `.env.example` to `.env` and fill it in. Only the model key is
required:

```
GROQ_API_KEY=your-key-here
```

## Run

Two ways in:

```powershell
excel-agent                            # the command line
streamlit run src/excel_agent/ui.py    # the page
```

On the page: pick a spreadsheet in the sidebar (or just ask for it by name),
then talk. Changes that delete, overwrite or reorder data stop and show you
an approval card first — nothing irreversible runs without your click.

## Tracing

Runs go to [LangSmith](https://smith.langchain.com/). LangChain sends them
itself, so there is nothing to switch on in the code — set these and run:

```powershell
$env:LANGSMITH_TRACING = "true"
$env:LANGSMITH_API_KEY = "your-key-here"
$env:LANGSMITH_PROJECT = "excel-agent"
```

A key belongs to one region. Posting it to another is refused with 403 and
nothing is recorded at all, which looks like tracing being broken rather than
aimed at the wrong place, so set the endpoint to match the host in your
LangSmith URL — `apac.api.smith.langchain.com` for an `apac.` workspace,
`eu.api.` for an `eu.` one:

```powershell
$env:LANGSMITH_ENDPOINT = "https://apac.api.smith.langchain.com"
```