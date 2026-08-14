"""The agent itself.

Builds the model, gives it the tools from the tools package, and runs the
loop that lets it call them until it has an answer.
"""

from uuid import uuid4

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from excel_agent.config import (
    MAX_TURNS,
    MODEL,
    SPREADSHEET,
    require_api_key,
    use_utf8_output,
)
from excel_agent.prompts import SYSTEM_PROMPT
from excel_agent.tools import TOOLS


RECURSION_LIMIT = MAX_TURNS * 2 + 1
TEMPERATURE = 0.3


GAVE_UP = (
    "I ran out of steps before reaching an answer."
)


def build_model():
    """The model, built the one way both variants (single agent, multi agent) use."""

    return ChatGroq(
        model=MODEL,
        api_key=require_api_key(),
        temperature=TEMPERATURE,
        model_kwargs={"parallel_tool_calls": False},
    )


def build_agent():
    """Build the agent from the model, the tools and the system prompt."""
    model = build_model()
    
    return create_agent(
        model,
        TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )


def new_thread() -> str:
    """Creates a thread ID used to track conversations"""
    return uuid4().hex


def ask(agent, question: str, thread_id: str) -> list[BaseMessage]:
    """Send one question. returns a list holding the messages this turn produced: any tool calls,
    their results, and the answer last."""

    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }


    produced: list[BaseMessage] = []
    try:
        for chunk in agent.stream(
            {"messages": [HumanMessage(question)]},
            config=config,
            stream_mode="updates",
        ):
            for update in chunk.values():
                # Not every update carries messages, so the ones that do not
                # are stepped over rather than assumed away.
                if isinstance(update, dict):
                    produced.extend(update.get("messages") or [])
    except GraphRecursionError:
        return produced + [AIMessage(GAVE_UP)]

    return produced


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
    ("finding the files", ["What spreadsheets can you work on?"]),
    (
        "a file it cannot pick between",
        ["Open the orders file and tell me how many rows it has."],
    ),
    ("reading the columns", ["What columns does the sheet have?"]),
    (
        "counting",
        ["Which regions appear in the sheet, and how many rows does each have?"],
    ),
    ("filtering", ["Show me the rows for the EU region."]),
    (
        "summing a column",
        ["How many units have been sold in total, and what is the largest order?"],
    ),
    ("something it cannot do", ["Make the header row bold and blue."]),
    ("something else it cannot do", ["Sort the sheet by units, largest first."]),
    ("adding a column", ["Add a Profit column to the sheet."]),
    ("drawing a chart", ["Draw me a bar chart of units by product."]),
    ("a column it must not delete", ["Delete the Units column."]),
    ("a row it cannot pick between", ["Change the unit price of the Webcam to 45."]),
    ("a plain edit", ["Set the units on row 7 to 25."]),
    ("renaming a column", ["Rename the Units column to Quantity."]),
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
    """Hold one conversation, printing the tool calls and answer per turn.

    One thread per case, so a case starts with nothing said and cannot be
    answered out of what some earlier case happened to leave behind.
    """
    thread_id = new_thread()

    for prompt in prompts:
        produced = ask(agent, prompt, thread_id)

        print(f"> {prompt}")
        for call in tool_calls_in(produced):
            print(f"    {call}")
        print(f"    {answer_of(produced)}")


def main() -> None:
    """Run every case with `python -m excel_agent.agent`.

    Said up front rather than left to be discovered: several of these cases
    change the sheet, and a spreadsheet on Drive cannot be copied aside and put
    back the way a file could. Point EXCEL_AGENT_SPREADSHEET at something you
    are willing to lose. It also makes one Groq request per turn, plus one per
    tool call, so it is not free.
    """
    use_utf8_output()

    print(
        f"These cases change {SPREADSHEET or '[no spreadsheet chosen]'}, and "
        "nothing here puts it back. Use a copy.\n"
    )

    agent = build_agent()
    for label, prompts in CASES:
        print(f"=== {label} ===")
        run_case(agent, prompts)
        print()


if __name__ == "__main__":
    main()
