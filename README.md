# excel_agent

Part of the first Evaluation at T2 (Thursday, 6th of August).

An agentic system driven by natural language that translates text into actions
(add, edit, remove) on the Google spreadsheets in your Drive.

## Getting it running

```powershell
pip install -e .[ui,dev]
```

The agent works on Google Sheets and nothing else, so it needs to be allowed
into your Drive before it can do anything. `google-api-setup-guide.md` walks
through making the OAuth client; what it leaves you with is a
`credentials.json` beside this file. The first run opens a browser to ask for
consent and writes a `token.json` next to it, so later runs ask nothing.

Then the model, and the spreadsheet to start on:

```powershell
$env:GROQ_API_KEY = "your-key-here"
$env:EXCEL_AGENT_SPREADSHEET = "TEST - Sales Orders"
```

`EXCEL_AGENT_SPREADSHEET` is optional. Without it the agent starts having
chosen nothing, and says so until you pick something with `/use` or the
sidebar.

Two ways in:

```powershell
excel-agent                            # the command line, --agents multi for subagents
streamlit run src/excel_agent/ui.py    # the page
```

## Tracing

Runs go to [LangSmith](https://smith.langchain.com/). LangChain sends them
itself, so there is nothing to switch on in the code — set these and run:

```powershell
$env:LANGSMITH_TRACING = "true"
$env:LANGSMITH_API_KEY = "your-key-here"
$env:LANGSMITH_PROJECT = "excel-agent"
```

A turn arrives as a tree: the question at the root, named for whichever agent
answered it, each model call under that with the prompt it saw and what it
cost, and each tool call with its arguments and what it returned. Under
`--agents multi` a subagent's whole conversation sits inside the tool call
that handed it the work.