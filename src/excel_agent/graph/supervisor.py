"""The planner: decides who does the next step and what the task is, or
answers the user and stops."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    SummarizationMiddleware,
    after_model,
    dynamic_prompt,
)
from langchain_core.tools import tool

from excel_agent.graph.state import DELEGATE, Delegate, State
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT


SUMMARISE_AT = 0.7
KEEP_MESSAGES = 20

DECIDING = """\

HOW TO REPLY
- To hand the next step to a specialist, call the delegate tool.
- To answer the user, write the answer as your reply and call nothing.
- Do one or the other, never both.
"""


class SupervisorState(AgentState):
    """What the supervisor agent is handed on each call.

    Every field the prompt reads has to be declared here as well as passed;
    an undeclared one comes back as None with no error.
    """

    spreadsheet_id: str | None
    spreadsheet_name: str | None
    worker_results: list[str]


@tool(DELEGATE, args_schema=Delegate)
def delegate(next: str, task: str) -> str:
    """Hand the next step to a specialist."""
    # Never runs. stop_at_delegation ends the agent as soon as the call is
    # made, because the specialist is a node in the outer graph, not a tool.
    return ""


@after_model
def stop_at_delegation(state, runtime) -> dict | None:
    """End the agent on a delegation, instead of running the tool."""
    if getattr(state["messages"][-1], "tool_calls", None):
        return {"jump_to": "end"}

    return None


def supervisor_instructions(
    spreadsheet_name: str | None,
    worker_results: list[str],
) -> str:
    """The prompt, named the file and the work so far."""
    return (
        f"{ORCHESTRATOR_PROMPT}\n"
        "CURRENT SPREADSHEET\n"
        f"- {spreadsheet_name or 'None chosen yet. Delegate to file_manager first.'}\n\n"
        "WORK SO FAR THIS TURN\n"
        + (
            "\n".join(f"- {one}" for one in worker_results)
            or "- Nothing yet."
        )
        + DECIDING
    )


@dynamic_prompt
def supervisor_prompt(request) -> str:
    """Rebuild the prompt on every call, from the state the agent was handed."""
    return supervisor_instructions(
        request.state.get("spreadsheet_name"),
        request.state.get("worker_results") or [],
    )


def build_supervisor(model):
    """The planner. Its one tool routes; it never touches a spreadsheet.

    Delegating is a tool call and answering is ordinary prose, so the model is
    free to write the reply as a sentence. Asked for the answer inside a
    schema, this one returned malformed JSON about half the time.
    """
    return create_agent(
        model=model,
        tools=[delegate],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=SupervisorState,
        middleware=[
            supervisor_prompt,
            stop_at_delegation,
            # The model produces a malformed tool call often enough to matter,
            # and it is usually transient. on_failure must be "error": the
            # default lets the agent carry on and call the failing model again,
            # which never terminates.
            ModelRetryMiddleware(max_retries=1, on_failure="error"),
            SummarizationMiddleware(
                model=model,
                trigger=("fraction", SUMMARISE_AT),
                keep=("messages", KEEP_MESSAGES),
            ),
        ],
    )


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]


def _decide(supervisor, state: State) -> dict:
    """Ask the planner what happens next, and turn it into state."""
    said = supervisor.invoke(
        {
            "messages": state["messages"],
            "spreadsheet_id": state.get("spreadsheet_id"),
            "spreadsheet_name": state.get("spreadsheet_name"),
            "worker_results": state.get("worker_results") or [],
        }
    )["messages"][-1]

    calls = getattr(said, "tool_calls", None) or []

    if calls:
        asked = Delegate(**calls[0]["args"])

        return {
            "route": asked.next,
            "task": asked.task,
            "final_answer": None,
        }

    # Nothing delegated, so this is the reply. It goes into messages as well,
    # for the next turn's supervisor: without it the thread holds the user's
    # questions and none of its own answers.
    return {
        "route": "end",
        "task": None,
        "final_answer": str(said.content or ""),
        "messages": [said],
        "worker_results": [],
    }


def supervisor_node(supervisor):
    """Route to a worker, or answer and end the turn."""

    def decide(state: State) -> dict:
        try:
            return _decide(supervisor, state)

        # Whatever broke, the turn ends with a sentence rather than a
        # traceback. Workers already do this; the planner did not.
        except Exception as failure:  # noqa: BLE001
            return {
                "route": "end",
                "task": None,
                "final_answer": (
                    "Something went wrong working that out: "
                    f"{_why(failure)}. Please try again."
                ),
                "worker_results": [],
            }

    return decide
