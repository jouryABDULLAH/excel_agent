"""Tools for finding which spreadsheet to work on.

One lists what there is by name, the other finds which file holds something.
Both ask Drive rather than Sheets, because only Drive knows what files exist
and only Drive searches inside them. Reading Drive is all either needs, so
both work under the drive.readonly scope.

Neither returns a row number, which is what keeps them apart from find_data:
choosing a file is the question asked before any sheet is opened.
"""

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from excel_agent import config
from excel_agent.sheets import (
    containing,
    number_forms,
    readable,
    resolve_spreadsheet,
    search,
    sheets_in,
)
from excel_agent.tracing import traced


@tool
@traced
def list_workbooks(name: str | None = None) -> str:
    """List the Google spreadsheets that can be worked on.

    Call this when the user has not said which spreadsheet to work on, or
    talks about one you have not been given the name of. The names it returns
    are the ones the other tools accept as their spreadsheet argument.

    Args:
        name: Part of a spreadsheet's name, to narrow the search. Leave this
            out to list everything reachable.

    Returns:
        One line per spreadsheet, or a sentence saying none were found. The
        one being worked on is marked, and that is the one the other tools use
        when their spreadsheet argument is left out.

    Examples:
        list_workbooks()
        list_workbooks(name="sales")
    """
    try:
        found = search(name)
    except HttpError as failure:
        return readable(failure)

    if not found:
        if name:
            return f'No spreadsheet has "{name}" in its name.'
        return "There are no spreadsheets in this Drive."

    in_use = config.SPREADSHEET
    titles = [title for _, title in found]

    lines = [
        f"{len(found)} spreadsheet{'s' if len(found) > 1 else ''}"
        + (f' with "{name}" in the name:' if name else ":")
    ]
    for title in titles:
        line = f"  {title}"
        if title == in_use:
            line += " (the one being worked on)"
        # A name shared by two files reaches neither, so say so here rather
        # than letting the next call be the one that fails.
        if titles.count(title) > 1:
            line += " (more than one file has this name: rename one to use it)"
        lines.append(line)

    if not in_use:
        lines.append("")
        lines.append(
            "No spreadsheet has been chosen yet. Ask the user which of these "
            "to work on, then name it in the spreadsheet argument."
        )

    return "\n".join(lines)




@tool
@traced
def use_spreadsheet(name: str) -> str:
    """Work on this spreadsheet from now on.

    Call this when the user wants to move to a different spreadsheet and stay
    there. Every tool called afterwards without a spreadsheet argument works on
    this one, so the name does not have to be repeated on every call.

    Do not call this to read something out of another file. Every tool takes a
    spreadsheet argument of its own for that. Calling this instead would leave
    the file being worked on changed, and every later edit would land in the
    file that was only meant to be read.

    Args:
        name: The spreadsheet to work on, by name, as list_workbooks or
            find_spreadsheet gives it.

    Returns:
        A sentence saying which spreadsheet is now being worked on and naming
        the sheets inside it, or an explanation of why the name reached none.

    Examples:
        use_spreadsheet(name="TEST - Sales Orders")
    """
    if not name or not name.strip():
        return "Say which spreadsheet to work on."

    try:
        # Resolved before it is settled on, so a name that reaches nothing, or
        # two files at once, is refused here rather than by every later call.
        spreadsheet_id, title = resolve_spreadsheet(name)
        inside = list(sheets_in(spreadsheet_id))
    except ValueError as explanation:
        return str(explanation)
    except HttpError as failure:
        return readable(failure)

    config.SPREADSHEET = title

    answer = (
        f'Now working on "{title}". Tools called without a spreadsheet '
        "argument will use it."
    )

    # The sheets are named here because nothing else says what they are, and
    # the name of a file is not the name of a sheet inside it. Told only the
    # file name, an agent will invent a sheet called after it.
    if inside:
        answer += (
            f" It holds {len(inside)} sheet(s): {', '.join(inside)}. "
            f"Calls that name no sheet work on {inside[0]}."
        )

    return answer




# find Spreadsheet possible use case:
# 1. identifies data: "Update the order for ORD-1042." No file is chosen, and list_workbooks can't help — no spreadsheet is named that. find_spreadsheet("ORD-1042") lands on exactly one file, and the orchestrator delegates with it. 
# 2. find file from its contents:  "The sheet where I track stock levels." 
# 3. A question about spread, mid-session. "Does Charlie appear anywhere else?" . two files, answered without moving off the one in hand.
# 4. Locating the source before a cross-file copy. When copy_data lands, "take the totals from the returns file" needs the source file resolved first


# shortcomings: 
# - Numbers don't match. 12240 found nothing, even though $12,240.00 is in the sheet
# - Part of a word won't match, so Ware wouldn't find Warehouse.
# - A value written a moment ago may not be findable

@tool
@traced
def find_spreadsheet(text: str) -> str:
    """Find which spreadsheets hold some text anywhere inside them.

    Use this when the user talks about data without saying which file it is
    in. It searches the contents of every spreadsheet, not just their names,
    so it finds the right file without opening each one in turn.

    Args:
        text: The words to look for. Whole words: part of one will not match.

    Returns:
        The spreadsheets that hold it, by name, or a sentence saying none do.
        It says nothing about where inside a file the text is: use find_data
        for that, once the file is settled.

    Examples:
        find_spreadsheet(text="quarterly targets")
        find_spreadsheet(text="ORD-1042")
    """
    if not text or not text.strip():
        return "Say what to look for."

    # A number is searched in the ways a sheet might be showing it, because
    # Drive indexes what a cell displays: 12240 finds nothing in a sheet
    # showing $12,240.00. Only tried when the plain text found nothing, so an
    # ordinary search still costs one call.
    try:
        found = containing(text)
        tried = [text]
        if not found:
            for form in number_forms(text)[1:]:
                tried.append(form)
                found = containing(form)
                if found:
                    break
    except HttpError as failure:
        return readable(failure)

    if not found:
        looked_for = tried[0] if len(tried) == 1 else ", ".join(tried)
        return (
            f'No spreadsheet holds "{looked_for}". Drive indexes a file after '
            "it is written rather than as it is written, so something changed "
            "a moment ago may not be findable yet; it matches whole words "
            "rather than parts of them; and it indexes what a cell shows, so "
            "a number has to be written the way the sheet displays it."
        )

    in_use = config.SPREADSHEET
    lines = [f'{len(found)} spreadsheet(s) hold "{text}":']
    for _, title in found:
        mark = " (the one being worked on)" if title == in_use else ""
        lines.append(f"  {title}{mark}")

    # Asking which file is only the next step when none has been settled on.
    # A question about where something lives can be answered from this list
    # without moving off the spreadsheet already in hand.
    if not in_use:
        lines.append("")
        lines.append(
            "Nothing is being worked on yet. To change that, ask the user "
            "which of these they mean and name it in the spreadsheet argument."
        )

    return "\n".join(lines)


CASES = [
    ("everything", {}),
    ("narrowed by name", {"name": "sales"}),
    ("a name nothing matches", {"name": "no such file anywhere"}),
]

FIND_CASES = [
    ("text inside a file", {"text": "Laptop"}),
    ("text nothing holds", {"text": "no such words anywhere"}),
]


def main() -> None:
    """Try the tool by hand with `python -m excel_agent.tools.sheets.spreadsheets`."""
    for label, arguments in CASES:
        print(f"--- {label}: {arguments} ---")
        print(list_workbooks.invoke(arguments))
        print()

    for label, arguments in FIND_CASES:
        print(f"--- find_spreadsheet, {label}: {arguments} ---")
        print(find_spreadsheet.invoke(arguments))
        print()


if __name__ == "__main__":
    main()
