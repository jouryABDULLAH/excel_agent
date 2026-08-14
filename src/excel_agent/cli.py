"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

import argparse

from excel_agent import browsing
from excel_agent.config import MODEL, use_utf8_output
from excel_agent.runner import Answer, Session, Text, ToolCall, rendered
from excel_agent.subagents.factory import VARIANTS, agent_name, build
from excel_agent.tools import inspect_sheet, list_workbooks

HELP = """\
Type what you want done to the sheet, in your own words.

  /use [name]     work on another spreadsheet, or list the ones there are
  /sheet [name]   show a sheet without asking the model, the one the
                  spreadsheet opens on unless you name another
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
    """Reads the command line."""
    parser = argparse.ArgumentParser(
        prog="excel-agent",
        description="Change an Excel sheet by saying what you want in your own words.",
    )
    parser.add_argument(
        "--agents",
        choices=VARIANTS,
        default="single",
        help=(
            "single asks one agent holding every tool; multi asks an "
            "orchestrator that hands the work to subagents"
        ),
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
    arguments = read_arguments()
    debug = arguments.debug

    try:
        agent = build(arguments.agents)
    except RuntimeError as e:
        # A missing API key explains itself, so the message is enough. Under
        # --debug the traceback is the point, so it is let through.
        if debug:
            raise
        print(e)
        return

    print(
        f"Working on {browsing.where()} with {MODEL}, "
        f"{arguments.agents} agent. /help for commands."
    )

    session = Session(agent, name=agent_name(arguments.agents))
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
            # with the sheets the spreadsheet does have.
            wanted = question[len("/sheet"):].strip()
            print(inspect_sheet.invoke({"sheet": wanted or None}))
            continue

        if question == "/use" or question.startswith("/use "):
            wanted = question[len("/use"):].strip()
            if not wanted:
                print(list_workbooks.invoke({}))
                continue
            try:
                # Resolved against Drive, so what is stored is the name Drive
                # really holds rather than the one that was typed.
                browsing.choose(wanted)
            except ValueError as explanation:
                print(explanation)
                continue
            print(f"Now working on {browsing.where()}.")
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

            if debug:
                raise
            print(f"That went wrong: {e}")


if __name__ == "__main__":
    main()
