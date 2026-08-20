"""Finds which spreadsheet to work on, and settles the choice.

The only specialist that writes something other than its own report: what it
chooses is what every step after it operates on.
"""

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage

from excel_agent.agents._shared import (
    DELEGATED,
    WorkerState,
    answered,
    reported,
    run_worker,
)
from excel_agent.graph.state import State
from excel_agent.prompts import CANNOT_DO, LANGUAGE_AND_SHEET_TEXT
from excel_agent.tools import (
    find_spreadsheet,
    list_workbooks,
    resolve_spreadsheet_choice,
)


FILE_MANAGER_PROMPT = f"""\
{DELEGATED}

You manage spreadsheet files, not the data inside them.

Your tools:
- list_workbooks: list spreadsheet files available in Drive.
- find_spreadsheet: find which spreadsheet files contain some text.
- resolve_spreadsheet_choice: validate the one exact spreadsheet that should
  become active. Calling it records your choice for the outer application,
  but you do not manage session state yourself.

CHOOSING A SPREADSHEET
- The user does not need to know the exact filename.
- Treat a spreadsheet name supplied by the user as a semantic description,
  not necessarily an exact filename.
- Match obvious singular/plural forms, abbreviations, partial names, and clear
  Arabic-English equivalents.
- Do not reject a candidate only because its real filename contains extra words
  or prefixes such as "TEST -".
- Example: "employees", "employee file", or "ملف الموظفين" may match
  "TEST - Employee Attendance" if it is the clearly best candidate.
- You may first use list_workbooks with a name filter when useful.
- IMPORTANT: if a name-filtered list_workbooks call finds no suitable file,
  call list_workbooks without a name filter and compare the available real
  filenames semantically before concluding that no match exists.
- If one candidate is clearly the best semantic match, select it.
- If two or more files are genuinely plausible, ask the user which one.
- Ask the user only after checking the available filenames and finding either
  multiple plausible candidates or no reasonably related candidate.
- Never guess between plausible alternatives.
- Once you know the exact real filename, call resolve_spreadsheet_choice with
  that exact name.
- Never claim selection succeeded unless resolve_spreadsheet_choice succeeded.

CONTENT SEARCH
- If the user identifies a file by something stored inside it rather than its
  filename, use find_spreadsheet.
- If the user merely asks which files contain something, report the result and
  do NOT select another spreadsheet.
- If the user explicitly wants to work on the file found by its contents and
  exactly one file matches, resolve that exact spreadsheet afterwards.

BOUNDARY
- You may search spreadsheet contents only to identify which file contains a
  value or phrase. Use find_spreadsheet for that.
- Do not inspect rows in order to answer questions about the data itself.
- Do not calculate statistics, summarize sheet contents, modify cells, format
  anything, or create charts.
- Once the correct spreadsheet is identified, data-level work belongs to the
  analyst or another specialist.
- A spreadsheet filename and a sheet/tab name are different things.

{LANGUAGE_AND_SHEET_TEXT}
{CANNOT_DO}
"""

NAME = "file_manager"

TOOLS = (
    list_workbooks,
    find_spreadsheet,
    resolve_spreadsheet_choice,
)


def selected_spreadsheet(messages: list) -> dict | None:
    """The choice it settled on, if it settled one."""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue

        artifact = message.artifact

        if not isinstance(artifact, dict):
            continue

        if (
            artifact.get("operation") == "resolve_spreadsheet_choice"
            and artifact.get("ok") is True
        ):
            return artifact

    return None


def build(model):
    """The file manager, as a graph node."""
    agent = create_agent(
        model=model,
        tools=list(TOOLS),
        system_prompt=FILE_MANAGER_PROMPT,
        state_schema=WorkerState,
    )

    def choose(state: State, config=None) -> dict:
        said, result = run_worker(NAME, agent, state, config)

        written = {
            "worker_results": reported(NAME, said, state),
            "messages": answered(NAME, said, state),
        }

        selected = (
            selected_spreadsheet(result["messages"])
            if result is not None
            else None
        )

        if selected is not None:
            written["spreadsheet_id"] = selected["spreadsheet_id"]
            written["spreadsheet_name"] = selected["spreadsheet_name"]

        return written

    return choose
