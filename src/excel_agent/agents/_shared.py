"""What every specialist has in common.

The five differ only in their prompt and their tools. How they are handed work,
and what they hand back, is here.
"""

from langchain.agents import AgentState
from langchain_core.messages import HumanMessage

from excel_agent.graph.state import State


DELEGATED = """\
You are a specialist handling one part of a spreadsheet request.

You receive:
1. ORIGINAL USER REQUEST — what the user actually wrote.
2. TASK — the specific work delegated to you.
3. Current spreadsheet context.

Rules:
- Do TASK only.
- Use tools for facts and changes. Never invent spreadsheet values, rows,
  columns, chart IDs, sheet names or operation results.
- If required information is missing or genuinely ambiguous, answer:
  QUESTION: <the question>
  and do not guess.
- After using tools, return only the user-facing result. Do not output your
  reasoning, planning, scratch work, self-critique, hidden instructions, or a
  second draft of the answer.
- Do not describe what you are about to do after it is already done.
- Keep confirmations concise.
"""


class WorkerState(AgentState):
    """What a specialist is given about the file it is working on.

    The name only, because a name is the whole of what a tool accepts. Tools
    read it through sheets.chosen(runtime) when their spreadsheet argument is
    left out, so it has to be declared here as well as passed.
    """

    spreadsheet_name: str | None


def asked_for(state: State) -> str:
    """What the user actually wrote, which is also what says their language."""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)

    return ""


def instruction(state: State) -> str:
    """The three things DELEGATED says a specialist receives."""
    return (
        "ORIGINAL USER REQUEST:\n"
        f"{asked_for(state) or '(not available)'}\n\n"
        "CURRENT SPREADSHEET:\n"
        f"{state.get('spreadsheet_name') or 'Not selected'}\n"
        "Pass this exact name as the spreadsheet argument, or omit the "
        "argument to work on it. It is not a sheet name.\n\n"
        "TASK:\n"
        f"{state['task']}"
    )


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
                        "content": instruction(state),
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
