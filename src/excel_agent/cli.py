"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

import argparse

from excel_agent.agent import build_agent
from excel_agent import config
from excel_agent.config import MODEL, resolve_workbook, use_utf8_output
from excel_agent.tools.inspect import inspect_sheet
from excel_agent.runner import Answer, Session, Text, ToolCall, rendered
from excel_agent.tools.workbooks import list_workbooks

HELP = """\
Type what you want done to the sheet, in your own words.

  /use [file]     work on another workbook, or list the ones there are
  /sheet [name]   show a sheet without asking the model, the one the file
                  opens on unless you name another
  /tools          show or hide the tool calls behind each answer
  /reset          forget the conversation so far
  /help           show this
  /quit           leave
"""


def run_turn(session, question: str, show_tools: bool) -> None:
    """Receives the user's question and prints the answer.

    Everything printed here comes from the events the session gives back, so
    the terminal knows as much about the agent as a web page would.
    """
    for event in session.ask(question):
        if isinstance(event, ToolCall) and show_tools:
            print(f"  . {rendered(event)}")
        elif isinstance(event, Text):
            print(event.text, end="", flush=True)
        elif isinstance(event, Answer):
            print(event.text)


def read_arguments() -> argparse.Namespace:
    """Read the command line, for both excel-agent and python -m excel_agent."""
    parser = argparse.ArgumentParser(
        prog="excel-agent",
        description="Change an Excel sheet by saying what you want in your own words.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "let errors out with their traceback instead of printing a one "
            "line summary and carrying on"
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Talk to the agent until the user leaves."""
    use_utf8_output()
    debug = read_arguments().debug

    try:
        agent = build_agent()
    except RuntimeError as e:
        # A missing API key explains itself, so the message is enough. Under
        # --debug the traceback is the point, so it is let through.
        if debug:
            raise
        print(e)
        return

    print(f"Working on {config.WORKBOOK_PATH.name} with {MODEL}. /help for commands.")

    session = Session(agent)
    show_tools = True

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not question:
            continue

        if question in ("/quit", "/exit"):
            return

        if question == "/help":
            print(HELP)
            continue

        if question == "/sheet" or question.startswith("/sheet "):
            # A name after the command picks a sheet; a wrong one is answered
            # with the sheets the workbook does have.
            wanted = question[len("/sheet"):].strip()
            print(
                inspect_sheet.invoke(
                    {"workbook": config.WORKBOOK_PATH.name, "sheet": wanted or None}
                )
            )
            continue

        if question == "/use" or question.startswith("/use "):
            wanted = question[len("/use"):].strip()
            if not wanted:
                print(list_workbooks.invoke({}))
                continue
            try:
                config.WORKBOOK_PATH = resolve_workbook(wanted)
            except ValueError as explanation:
                print(explanation)
                continue
            print(f"Now working on {config.WORKBOOK_PATH.name}.")
            continue

        if question == "/tools":
            show_tools = not show_tools
            print(f"Tool calls are now {'shown' if show_tools else 'hidden'}.")
            continue

        if question == "/reset":
            session.reset()
            print("Conversation forgotten. The sheet itself is unchanged.")
            continue

        try:
            run_turn(session, question, show_tools)
        except KeyboardInterrupt:
            print("\nStopped. The sheet may have been part way through a change.")
        except Exception as e:
            # One bad turn should not end the session, so the loop keeps going
            # and the user can try again. Under --debug it stops instead, with
            # the traceback, because a summary is no use when the thing you
            # are looking at is the bug.
            if debug:
                raise
            print(f"That went wrong: {e}")


if __name__ == "__main__":
    main()
