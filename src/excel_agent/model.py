"""The model, and the few settings every agent built on it shares.

Nothing here builds an agent. The orchestrator and its subagents are put
together in the subagents package, and all of them ask this for the model.
"""

from uuid import uuid4

from langchain_groq import ChatGroq

from excel_agent.config import MAX_TURNS, MODEL, require_api_key


RECURSION_LIMIT = MAX_TURNS * 2 + 1
TEMPERATURE = 0.3


GAVE_UP = (
    "I ran out of steps before reaching an answer."
)


def build_model():
    """The model, built the one way every agent here uses."""

    return ChatGroq(
        model=MODEL,
        api_key=require_api_key(),
        temperature=TEMPERATURE,
        model_kwargs={"parallel_tool_calls": False},
    )


def new_thread() -> str:
    """Creates a thread ID used to track conversations"""
    return uuid4().hex
