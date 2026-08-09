"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

import argparse

from excel_agent.agent import answer_of, ask, build_agent, tool_calls_in
from excel_agent.config import MODEL, WORKBOOK_PATH
from excel_agent.tools.inspect import inspect_sheet

HELP = """\
Type what you want done to the sheet, in your own words.

  /sheet   show the sheet without asking the model
  /tools   show or hide the tool calls behind each answer
  /reset   forget the conversation so far
  /help    show this
  /quit    leave
"""


def run_turn(agent, question: str, history: list, show_tools: bool) -> list:
    """Receives the user's question and prints the answer. Returns the new history."""
    already_said = len(history)
    history = ask(agent, question, history)

    if show_tools:
        for call in tool_calls_in(history[already_said:]):
            print(f"  . {call}")

    print(answer_of(history))
    return history


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

    print(f"Working on {WORKBOOK_PATH.name} with {MODEL}. /help for commands.")

    history: list = []
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

        if question == "/sheet":
            # Named rather than left out, so the workbook the banner promised
            # is the one shown, whatever the tool would have picked by itself.
            print(inspect_sheet.invoke({"workbook": WORKBOOK_PATH.name}))
            continue

        if question == "/tools":
            show_tools = not show_tools
            print(f"Tool calls are now {'shown' if show_tools else 'hidden'}.")
            continue

        if question == "/reset":
            history = []
            print("Conversation forgotten. The sheet itself is unchanged.")
            continue

        try:
            history = run_turn(agent, question, history, show_tools)
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
