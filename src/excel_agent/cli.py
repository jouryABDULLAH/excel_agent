"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

import argparse

from excel_agent.agent import answer_of, ask, build_agent, new_thread, tool_calls_in
from excel_agent.config import MODEL, WORKBOOK_PATH, use_utf8_output
from excel_agent.tools.inspect import inspect_sheet

HELP = """\
Type what you want done to the sheet, in your own words.

  /sheet [name]   show a sheet without asking the model, the one the file
                  opens on unless you name another
  /tools          show or hide the tool calls behind each answer
  /reset          forget the conversation so far
  /help           show this
  /quit           leave
"""


def run_turn(agent, question: str, thread_id: str, show_tools: bool) -> None:
    """Receives the user's question and prints the answer.

    Nothing is handed back, because nothing needs to be carried: the agent
    keeps the conversation under the thread name, and this only prints what
    the turn produced.
    """
    produced = ask(agent, question, thread_id)

    if show_tools:
        for call in tool_calls_in(produced):
            print(f"  . {call}")

    print(answer_of(produced))


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

    print(f"Working on {WORKBOOK_PATH.name} with {MODEL}. /help for commands.")

    thread_id = new_thread()
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
            # Named rather than left out, so the workbook the banner promised
            # is the one shown, whatever the tool would have picked by itself.
            # A name after the command picks a sheet; a wrong one is answered
            # with the sheets the workbook does have.
            wanted = question[len("/sheet"):].strip()
            print(
                inspect_sheet.invoke(
                    {"workbook": WORKBOOK_PATH.name, "sheet": wanted or None}
                )
            )
            continue

        if question == "/tools":
            show_tools = not show_tools
            print(f"Tool calls are now {'shown' if show_tools else 'hidden'}.")
            continue

        if question == "/reset":
            # A new thread rather than an emptied one: the old messages stay
            # where they are and are never read again.
            thread_id = new_thread()
            print("Conversation forgotten. The sheet itself is unchanged.")
            continue

        try:
            run_turn(agent, question, thread_id, show_tools)
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
