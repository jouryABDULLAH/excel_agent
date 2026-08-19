"""The planner: decides who does the next step and what the task is, or
answers the user and stops."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    SummarizationMiddleware,
    dynamic_prompt,
)
from langchain.agents.structured_output import ToolStrategy

from excel_agent.graph.state import Decision, Delegate, State
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT


SUMMARISE_AT = 0.7
KEEP_MESSAGES = 20


class SupervisorState(AgentState):
    """What the supervisor agent is handed on each call.

    Every field the prompt reads has to be declared here as well as passed;
    an undeclared one comes back as None with no error.
    """

    spreadsheet_id: str | None
    spreadsheet_name: str | None
    worker_results: list[str]


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
    )


@dynamic_prompt
def supervisor_prompt(request) -> str:
    """Rebuild the prompt on every call, from the state the agent was handed."""
    return supervisor_instructions(
        request.state.get("spreadsheet_name"),
        request.state.get("worker_results") or [],
    )


def build_supervisor(model):
    """The planner. It holds no tools: it routes, it does not act."""
    return create_agent(
        model=model,
        tools=[],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=SupervisorState,
        # ToolStrategy explicitly: left to choose, a model with native
        # structured output gets ProviderStrategy, which cannot take a
        # union. As two tools the model picks delegating or finishing.
        response_format=ToolStrategy(Decision), # type: ignore
        middleware=[
            supervisor_prompt,
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
    ) # type: ignore


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:	")

    return (head or type(failure).__name__)[:160]


def _decide(supervisor, state: State) -> dict:
    """Ask the planner what happens next, and turn it into state."""
    decision = supervisor.invoke(
        {
            "messages": state["messages"],
            "spreadsheet_id": state.get("spreadsheet_id"),
            "spreadsheet_name": state.get("spreadsheet_name"),
            "worker_results": state.get("worker_results") or [],
        }
    )["structured_response"]

    if isinstance(decision, Delegate):
        return {
            "route": decision.next,
            "task": decision.task,
            "final_answer": None,
        }

    return {
        "route": "end",
        "task": None,
        "final_answer": decision.final_answer,
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
