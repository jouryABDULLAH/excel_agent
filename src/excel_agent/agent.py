"""The agent itself.

Builds the model, gives it the tools from the tools package, and runs the
loop that lets it call them until it has an answer.
"""

from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_groq import ChatGroq
from langgraph.errors import GraphRecursionError

from excel_agent.config import MAX_TURNS, MODEL, require_api_key, resolve_workbook
from excel_agent.prompts import SYSTEM_PROMPT
from excel_agent.tools import TOOLS


RECURSION_LIMIT = MAX_TURNS * 2 + 1
TEMPERATURE = 0.3

# Stands in for the answer when the agent runs out of steps before giving one.
GAVE_UP = (
    "I ran out of steps before reaching an answer. The tool calls above show "
    "what I kept trying."
)


def build_agent():
    """Build the agent from the model, the tools and the system prompt.

    Tool calls are asked for one at a time. Two of them running at once would
    be two writes to the same file at once, and the second would be working
    from the sheet as it looked before the first one saved.
    """
    model = ChatGroq(
        model=MODEL,
        api_key=require_api_key(),
        temperature=TEMPERATURE,
        model_kwargs={"parallel_tool_calls": False},
    )
    return create_agent(model, TOOLS, system_prompt=SYSTEM_PROMPT)


def ask(agent, question: str, history: list[BaseMessage] | None = None) -> list[BaseMessage]:
    """Send one question and return the full conversation that came back.

    The returned list includes the question, any tool calls and their
    results, and the answer. Pass it back in as history to keep the
    conversation going, which lets the user say things like "remove that row
    again" and be understood.
    """
    messages = list(history or []) + [HumanMessage(question)]
    state = {"messages": messages}

    # Streamed rather than invoked so that the messages so far are still in
    # hand when the agent runs out of steps.
    try:
        for state in agent.stream(
            {"messages": messages},
            config={"recursion_limit": RECURSION_LIMIT},
            stream_mode="values",
        ):
            pass
    except GraphRecursionError:
        return state["messages"] + [AIMessage(GAVE_UP)]

    return state["messages"]


def answer_of(messages: list[BaseMessage]) -> str:
    """Pull the text of the final answer out of a conversation."""
    return str(messages[-1].content)


def tool_calls_in(messages: list[BaseMessage]) -> list[str]:
    """List the tool calls made in a conversation, for showing the workings.

    Reads the calls the model asked for rather than the results, so a call
    that a tool refused still shows up here.
    """
    calls = []
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            arguments = ", ".join(f"{k}={v!r}" for k, v in call["args"].items())
            calls.append(f"{call['name']}({arguments})")
    return calls



CASES = [
    ("reading the columns", ["What columns does the sheet have?"]),
    (
        "counting",
        ["Which regions appear in the sheet, and how many rows does each have?"],
    ),
    ("filtering", ["Show me the rows for the EU region."]),
    ("something it cannot do", ["Make the header row bold and blue."]),
    ("something else it cannot do", ["Add a Profit column to the sheet."]),
    ("a row it cannot pick between", ["Change the unit price of the Webcam to 45."]),
    ("a plain edit", ["Set the units on row 7 to 25."]),
    (
        "a spelling fix",
        ["Row 10 spells the product in lowercase. Make it match the others."],
    ),
    ("adding a row", ["Add a row for a Webcam sold in the EU, 5 units at 42.00."]),
    (
        "adding then changing its mind",
        [
            "Add a row for a Monitor Arm sold in the US, 2 units at 89.99.",
            "Actually, remove that row again.",
        ],
    ),
]


def run_case(agent, prompts: list[str]) -> None:
    """Hold one conversation, printing the tool calls and answer per turn."""
    history: list[BaseMessage] = []

    for prompt in prompts:
        # Where the new messages start, so a second turn does not reprint the
        # tool calls from the first one.
        already_said = len(history)
        history = ask(agent, prompt, history)

        print(f"> {prompt}")
        for call in tool_calls_in(history[already_said:]):
            print(f"    {call}")
        print(f"    {answer_of(history)}")


def main() -> None:
    """Run every case with `python -m excel_agent.agent`.

    Some of these change the sheet, so the file is copied first and put back
    afterwards. That means the cases can be run again and again and always
    start from the same data. It does make one Groq request per turn, plus
    one per tool call, so it is not free.
    """
    path = resolve_workbook()

    # Kept outside the data folder. A copy left beside the workbook would be a
    # workbook itself, and would show up as one more file to choose between.
    with TemporaryDirectory() as folder:
        snapshot = Path(folder) / path.name
        copyfile(path, snapshot)

        try:
            agent = build_agent()
            for label, prompts in CASES:
                print(f"=== {label} ===")
                run_case(agent, prompts)
                print()
        finally:
            # Runs even if a case raises, so a crash cannot leave the sheet in
            # a half changed state.
            copyfile(snapshot, path)
            print("The sheet has been put back to how it was before these cases ran.")


if __name__ == "__main__":
    main()
