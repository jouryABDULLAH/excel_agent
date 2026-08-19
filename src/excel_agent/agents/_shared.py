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


def worker_node(name: str, agent):
    """Run one specialist on the task the supervisor set."""

    def work(state: State) -> dict:
        try:
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": state["task"] or "",
                        }
                    ],
                    "spreadsheet_name": state.get("spreadsheet_name"),
                }
            )
            said = str(
                result["messages"][-1].content or ""
            )

        # A specialist that falls over must not take the turn with it. Left to
        # propagate, the exception escapes the graph and the user is shown the
        # provider's raw JSON.
        except Exception as failure:  # noqa: BLE001
            said = (
                f"could not finish: {_why(failure)}. Nothing it was asked to "
                "do is known to have happened."
            )

        return {
            "worker_results": [
                *(state.get("worker_results") or []),
                f"[{name}] {said}",
            ]
        }

    return work


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]