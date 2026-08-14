"""Tool for finding out which workbooks there are.

The other tools take a workbook by name. This is how that name is found, so
the model can offer the user a choice instead of guessing at a file.

Read-only: it looks at the names of the files in the data folder and does
not open any of them.
"""

from langchain_core.tools import tool

from excel_agent import config
from excel_agent.config import workbook_names


@tool
def list_workbooks() -> str:
    """List the workbooks that can be worked on, by name.

    Call this when the user talks about a file you have not been given the
    name of, or when more than one file might be the one they mean. The names
    it returns are the ones the other tools accept as their workbook argument.

    Returns:
        The name of every workbook in the folder, with the one currently being
        worked on marked. Leaving the workbook argument out of the other tools
        uses that one.

    Examples:
        list_workbooks()
    """
    names = workbook_names()
    if not names:
        return f"There are no workbooks in {config.DATA_DIR.name}."

    in_use = config.WORKBOOK_PATH.name
    lines = [
        f"{len(names)} workbook{'s' if len(names) > 1 else ''} in {config.DATA_DIR.name}:"
    ]
    for name in names:
        lines.append(f"  {name}{' (the one in use)' if name == in_use else ''}")

    return "\n".join(lines)


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.workbooks`."""
    print(list_workbooks.invoke({}))


if __name__ == "__main__":
    main()