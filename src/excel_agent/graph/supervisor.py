"""The planner: decides who does the next step and what the task is, or
answers the user and stops."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import SummarizationMiddleware, dynamic_prompt

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
        response_format=Decision,
        middleware=[
            supervisor_prompt,
            SummarizationMiddleware(
                model=model,
                trigger=("fraction", SUMMARISE_AT),
                keep=("messages", KEEP_MESSAGES),
            ),
        ],
    )


def supervisor_node(supervisor):
    """Route to a worker, or answer and end the turn."""

    def decide(state: State) -> dict:
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

    return decide
