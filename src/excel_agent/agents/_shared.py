"""What every specialist has in common.

The five differ only in their prompt and their tools. How they are handed work,
and what they hand back, is here.
"""

from langchain.agents import AgentState
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from excel_agent.graph.replies import DELIVERED, table_free
from excel_agent.graph.state import State


# What a gated tool offers: run it or do not. Editing the arguments is a way
# of writing a spreadsheet change by hand, which is what the agent is for.
CONFIRMED = {"allowed_decisions": ["approve", "reject"]}


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


def run_worker(
    name: str,
    agent,
    state: State,
    config=None,
) -> tuple[str, dict | None]:
    """Run one specialist on the task the supervisor set.

    Gives back what it said, and its whole result for a node that needs to read
    more than that. The result is None when the run failed.

    The node's config is passed on, so the specialist's own model and tool
    calls are recorded inside this node's run rather than beside it. Without
    it every call in the turn arrives in the trace as a top-level sibling and
    nothing shows who did what.
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
            },
            config,
        )

    # A pause for approval is not a failure: it has to reach the graph so the
    # turn can be resumed rather than reported as broken.
    except GraphInterrupt:
        raise

    # A specialist that falls over must not take the turn with it. Left to
    # propagate, the exception escapes the graph and the user is shown the
    # provider's raw JSON.
    except Exception as failure:  # noqa: BLE001
        return (
            f"could not finish: {_why(failure)}. Nothing it was asked to do "
            "is known to have happened.",
            None,
        )

    said = str(result["messages"][-1].content or "")

    # When a read is being drawn by the application, the same table written
    # out as prose would show the data twice. The prompt says not to; the
    # model does it anyway, so the table is cut here, where nothing later can
    # repeat it.
    if any(
        isinstance(getattr(message, "artifact", None), dict)
        and message.artifact.get("render_data")
        for message in result["messages"]
    ):
        said = without_table_lines(said)

    return said, result


def without_table_lines(said: str) -> str:
    """The report with its table removed and the delivery note added.

    A report that was nothing but the table still has to say something, or
    the supervisor is left composing an answer from nothing. The note is
    there because the supervisor cannot see the drawn table: an intro like
    "here are the rows:" followed by nothing reads as a worker that returned
    nothing, and the supervisor sends the task out again.
    """
    return (
        table_free(said)
        or "The requested rows are shown in the table below."
    ) + f"\n{DELIVERED}"


def drawn_tables(result: dict | None, state: State) -> list[list[str]]:
    """The columns of each table the application drew, this turn so far.

    One entry per table, because the supervisor matches a whole heading
    against a whole table: pooling every column together let two unrelated
    tables sharing a couple of names pass for each other.
    """
    seen = [list(one) for one in state.get("drawn_tables") or []]

    for message in (result or {}).get("messages", []) or []:
        artifact = getattr(message, "artifact", None)

        if not isinstance(artifact, dict) or not artifact.get("render_data"):
            continue

        # inspect_sheet names its columns; find_data carries them on each
        # match instead.
        columns = list(artifact.get("headers") or [])

        for match in artifact.get("matches") or []:
            for column in (match.get("values") or {}):
                if column not in columns:
                    columns.append(column)

        if columns and columns not in seen:
            seen.append(columns)

    return seen


def reported(name: str, said: str, state: State) -> list[str]:
    """The work so far, with this specialist's line on the end."""
    return [
        *(state.get("worker_results") or []),
        f"[{name}] {said}",
    ]


def answered(name: str, said: str, state: State) -> list[ToolMessage]:
    """The report, as the answer to the supervisor's delegate call.

    In the thread it is what shows the supervisor its delegation happened, so
    it does not hand out the same task again. A tool call left unanswered
    would also make the whole thread invalid to the provider.
    """
    last = (state.get("messages") or [None])[-1]
    calls = getattr(last, "tool_calls", None) or []

    if not calls:
        return []

    return [
        ToolMessage(
            content=f"[{name}] {said}",
            tool_call_id=calls[0]["id"],
        )
    ]


def worker_node(name: str, agent):
    """A specialist that only reports back."""

    def work(state: State, config=None) -> dict:
        said, result = run_worker(name, agent, state, config)

        return {
            "worker_results": reported(name, said, state),
            "messages": answered(name, said, state),
            "drawn_tables": drawn_tables(result, state),
        }

    return work


def _why(failure: Exception) -> str:
    """One line about a failure, without the provider's JSON body."""
    head = str(failure).strip().split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]
