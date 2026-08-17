"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

import argparse

from excel_agent import browsing
from excel_agent.config import MODEL, use_utf8_output
from excel_agent.runner import Answer, Session, Text, ToolCall, rendered
from excel_agent.subagents.factory import build_orchestrator
from excel_agent.runner import (
    Answer,
    Artifact,
    ToolCall,
)

HELP = """\
Type what you want done to the sheet, in your own words.

  /reset          forget the conversation so far
"""

# show the artifact in the CLI
def print_artifact(
    artifact: dict,
) -> None:
    operation = artifact.get(
        "operation"
    )

    if operation == "inspect_sheet":
        columns = artifact.get(
            "headers",
            [],
        )

        rows = artifact.get(
            "rows",
            [],
        )

        if not rows:
            return

        print()
        print(
            "| row | "
            + " | ".join(columns)
            + " |"
        )

        print(
            "|"
            + "---|"
            * (len(columns) + 1)
        )

        for item in rows:
            values = item.get(
                "values",
                {},
            )

            print(
                f'| {item["row"]} | '
                + " | ".join(
                    str(
                        values.get(
                            column,
                            "",
                        )
                    )
                    for column
                    in columns
                )
                + " |"
            )

        print()

        return

    if operation == "find_data":
        matches = artifact.get(
            "matches",
            [],
        )

        if not matches:
            return

        columns = list(
            matches[0]
            .get("values", {})
        )

        print()
        print(
            "| row | matched in | "
            + " | ".join(columns)
            + " |"
        )

        print(
            "|"
            + "---|"
            * (len(columns) + 2)
        )

        for item in matches:
            values = item["values"]

            print(
                f'| {item["row"]} | '
                f'{item["matched_in"]} | '
                + " | ".join(
                    str(
                        values.get(
                            column,
                            "",
                        )
                    )
                    for column
                    in columns
                )
                + " |"
            )

        print()


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
        elif isinstance(event, Artifact):
            print_artifact(event.data)


def read_arguments() -> argparse.Namespace:
    """Reads the command line."""
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
    arguments = read_arguments()
    debug = arguments.debug

    try:
        agent = build_orchestrator()
    except RuntimeError as e:
        if debug:
            raise
        print(e)
        return

    session = Session(agent)

    print(f"Working on {browsing.where(session.in_use())} with {MODEL}.")
    show_tools = True

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not question:
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
