"""What every specialist has in common.

The five differ only in their prompt and their tools. How they are handed work,
and what they hand back, is here.
"""

from langchain.agents import AgentState

from excel_agent.graph.state import State


class WorkerState(AgentState):
    """What a specialist is given about the file it is working on.

    The name only, because a name is the whole of what a tool accepts. Tools
    read it through sheets.chosen(runtime) when their spreadsheet argument is
    left out, so it has to be declared here as well as passed.
    """

    spreadsheet_name: str | None


def run_worker(name: str, agent, state: State) -> tuple[str, dict | None]:
    """Run one specialist on the task the supervisor set.

    Gives back what it said, and its whole result for a node that needs to read
    more than that. The result is None when the run failed.
    """
    try:
        task = state.get("task")

        if not task:
            raise RuntimeError(f"{name} reached without a task")

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": task,
                    }
                ],
                "spreadsheet_name": state.get("spreadsheet_name"),
            }
        )

    # A specialist that falls over must not take the turn with it. Left to
    # propagate, the exception escapes the graph and the user is shown the
    # provider's raw JSON.
    except Exception as failure:  # noqa: BLE001
        return (
            f"could not finish: {_why(failure)}. Nothing it was asked to do "
            "is known to have happened.",
            None,
        )

    return str(result["messages"][-1].content or ""), result


def reported(name: str, said: str, state: State) -> list[str]:
    """The work so far, with this specialist's line on the end."""
    return [
        *(state.get("worker_results") or []),
        f"[{name}] {said}",
    ]


def worker_node(name: str, agent):
    """A specialist that only reports back."""

    def work(state: State) -> dict:
        said, _ = run_worker(name, agent, state)

        return {"worker_results": reported(name, said, state)}

    return work


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]
