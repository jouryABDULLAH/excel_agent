"""Command line loop.

Reads a line of text from the user, passes it to the agent, prints the
answer, and repeats.
"""

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
    """Recieves the user's question and print the answer. Returns the new history."""
    already_said = len(history)
    history = ask(agent, question, history)

    if show_tools:
        for call in tool_calls_in(history[already_said:]):
            print(f"  . {call}")

    print(answer_of(history))
    return history


def main() -> None:
    """Talk to the agent until the user leaves."""
    try:
        agent = build_agent()
    except RuntimeError as e:
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
            print(inspect_sheet.invoke({}))
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
            print(f"That went wrong: {e}")


if __name__ == "__main__":
    main()
