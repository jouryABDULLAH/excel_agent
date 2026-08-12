"""Tool for finding the spreadsheets that can be worked on.

Searches Drive by name. Reading Drive is all this needs, so it works under
the drive.readonly scope.
"""

from langchain_core.tools import tool

from excel_agent.tracing import traced


@tool
@traced
def list_workbooks(name: str | None = None) -> str:
    """List the Google spreadsheets that can be worked on.

    Args:
        name: Part of a spreadsheet's name, to narrow the search. Leave this
            out to list everything reachable.

    Returns:
        One line per spreadsheet, or a sentence saying none were found.
    """
    raise NotImplementedError
