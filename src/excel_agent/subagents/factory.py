"""Build spreadsheet subagents and expose them to the orchestrator as tools."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    dynamic_prompt,
)
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from excel_agent.config import MAX_TURNS
from excel_agent.model import RECURSION_LIMIT, build_model
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.subagents.registry import SUBAGENTS


# How full the context is allowed to get before the older half of the
# conversation is replaced with a summary of it, and how much of the recent
# conversation is kept verbatim when that happens.
SUMMARISE_AT = 0.7
KEEP_MESSAGES = 20


class OrchestratorState(AgentState):
    """Conversation state owned by the outer orchestrator."""

    spreadsheet_id: str | None
    spreadsheet_name: str | None


class SpreadsheetState(AgentState):
    """What a specialist is given about the file it is working on.

    The name only, because a name is the whole of what a tool accepts. It is
    here rather than in the instruction alone so that a tool called without a
    spreadsheet argument has somewhere to read it from that is not a module
    global shared by every session in the process.
    """

    spreadsheet_name: str | None


def _original_user_request(
    runtime: ToolRuntime,
) -> str:
    """Return the latest real user message from the outer conversation."""
    messages = runtime.state.get("messages") or []

    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue

        if isinstance(message.content, str):
            return message.content

        return str(message.content)

    return ""


def _subagent_instruction(
    *,
    instruction: str,
    runtime: ToolRuntime,
) -> str:
    """Build the context passed from the orchestrator to a specialist.

    The name is given and the id is not, because the name is the only one of
    the two any tool accepts: every spreadsheet argument is resolved through
    Drive by title. A specialist told the id would sooner or later pass it,
    and be answered that there is no spreadsheet called 1szALpie23-...
    """
    original_request = _original_user_request(runtime)

    spreadsheet_name = current_spreadsheet(
        runtime.state
    )

    return (
        "ORIGINAL USER REQUEST:\n"
        f"{original_request or '(not available)'}\n\n"
        "CURRENT SPREADSHEET:\n"
        f"{spreadsheet_name or 'Not selected'}\n"
        "Pass this exact name as the spreadsheet argument, or omit the "
        "argument to work on it.\n\n"
        "TASK:\n"
        f"{instruction}"
    )


def _collect_inner_results(
    messages: list,
) -> tuple[
    list[str],
    list[dict],
    list[dict],
]:
    """Collect results, artifacts and tool calls from a nested subagent run."""
    tool_results: list[str] = []
    tool_artifacts: list[dict] = []
    tool_calls: list[dict] = []

    for message in messages:
        for call in (
            getattr(
                message,
                "tool_calls",
                None,
            )
            or []
        ):
            tool_calls.append(
                {
                    "name": call["name"],
                    "arguments": dict(
                        call.get("args")
                        or {}
                    ),
                }
            )

        if not isinstance(
            message,
            ToolMessage,
        ):
            continue

        if message.content:
            tool_results.append(
                str(message.content)
            )

        if isinstance(
            message.artifact,
            dict,
        ):
            tool_artifacts.append(
                message.artifact
            )

    return (
        tool_results,
        tool_artifacts,
        tool_calls,
    )


def _selected_spreadsheet(
    messages: list,
) -> dict | None:
    """Find a successful spreadsheet selection made by the file manager."""
    for message in reversed(messages):
        if not isinstance(
            message,
            ToolMessage,
        ):
            continue

        artifact = message.artifact

        if not isinstance(
            artifact,
            dict,
        ):
            continue

        if (
            artifact.get("operation")
            == "resolve_spreadsheet_choice"
            and artifact.get("ok") is True
        ):
            return artifact

    return None


def _why(failure: Exception) -> str:
    """One short line about a failure, without the provider's raw payload.

    A provider refusal arrives as a sentence followed by a JSON body carrying
    the model's own bad output, all on one line. Keeping the sentence and
    dropping the body from the first brace onwards is what stops that output
    reaching the user, or going back into the model's context to be copied.
    The whole of it is in the trace either way.
    """
    text = str(failure).strip()

    head = text.split("{", 1)[0].strip(" -:\t")

    return (head or type(failure).__name__)[:160]


def _run_subagent(
    spec,
    agent,
    payload: dict,
) -> tuple[dict | None, str | None]:
    """Run one specialist, and turn a failure into an answer rather than a raise.

    Left to propagate, an exception escapes the whole turn: the user is shown
    the provider's raw JSON, and the checkpoint keeps an assistant message
    whose tool call was never answered. No API accepts a conversation in that
    shape, so every later turn on the thread fails too and the agent looks like
    it has stopped responding for good.

    Answering the tool call, even with a failure, is what keeps the
    conversation valid.
    """
    try:
        result = agent.invoke(
            payload,
            config={
                "recursion_limit": (
                    RECURSION_LIMIT
                ),
                "run_name": spec.name,
            },
        )

    except Exception as failure:  # noqa: BLE001 - a turn survives, whatever broke
        return None, (
            f"The {spec.name} could not finish: {_why(failure)}. "
            "Nothing it was asked to do is known to have happened. Tell the "
            "user that, and do not repeat the same step."
        )

    return result, None


def _delegate_tool(
    spec,
    agent,
):
    """Expose one ordinary spreadsheet specialist as an orchestrator tool."""

    @tool(
        spec.name,
        description=spec.description,
        response_format="content_and_artifact",
    )
    def delegate(
        instruction: str,
        runtime: ToolRuntime,
        render_data: bool = False,
    ) -> tuple[str, dict]:
        subagent_instruction = (
            _subagent_instruction(
                instruction=instruction,
                runtime=runtime,
            )
        )

        result, failure = _run_subagent(
            spec,
            agent,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            subagent_instruction
                            + "\n\n"
                            + (
                                "RENDER DATA:\ntrue"
                                if render_data
                                else "RENDER DATA:\nfalse"
                            )
                        ),
                    }
                ],
                # So a tool called without a spreadsheet argument reads the
                # file from here rather than from a process-wide global.
                "spreadsheet_name": (
                    current_spreadsheet(
                        runtime.state
                    )
                ),
            },
        )

        if failure:
            return (
                failure,
                {
                    "subagent": spec.name,
                    "ok": False,
                    "error": "subagent_failed",
                    "response": failure,
                    "tool_calls": [],
                    "tool_results": [],
                    "tool_artifacts": [],
                    "render_data": False,
                },
            )

        messages = (result or {}).get(
            "messages",
            [],
        )

        response = ""

        if messages:
            response = str(
                messages[-1].content
                or ""
            )

        (
            tool_results,
            tool_artifacts,
            tool_calls,
        ) = _collect_inner_results(
            messages
        )

        return (
            response,
            {
                "subagent": spec.name,
                "response": response,
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "tool_artifacts": tool_artifacts,
                "render_data": render_data,
            },
        )

    return delegate


def _file_manager_tool(
    spec,
    agent,
):
    """Expose the file manager and commit its selection to outer state."""

    @tool(
        spec.name,
        description=spec.description,
    )
    def delegate(
        instruction: str,
        runtime: ToolRuntime,
    ) -> Command:
        subagent_instruction = (
            _subagent_instruction(
                instruction=instruction,
                runtime=runtime,
            )
        )

        result, failure = _run_subagent(
            spec,
            agent,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            subagent_instruction
                        ),
                    }
                ],
                "spreadsheet_name": (
                    current_spreadsheet(
                        runtime.state
                    )
                ),
            },
        )

        if failure:
            # Answering the call and settling nothing: the spreadsheet in hand
            # is left as it was, which is true, and the thread stays valid.
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=failure,
                            tool_call_id=(
                                runtime.tool_call_id
                            ),
                        )
                    ]
                }
            )

        messages = (result or {}).get(
            "messages",
            [],
        )

        response = ""

        if messages:
            response = str(
                messages[-1].content
                or ""
            )

        (
            tool_results,
            tool_artifacts,
            tool_calls,
        ) = _collect_inner_results(
            messages
        )

        delegate_artifact = {
            "subagent": spec.name,
            "response": response,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "tool_artifacts": tool_artifacts,
            "render_data": False,
        }

        selected = _selected_spreadsheet(
            messages
        )

        if selected is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=response,
                            artifact=delegate_artifact,
                            tool_call_id=(
                                runtime.tool_call_id
                            ),
                        )
                    ]
                }
            )

        selected_id = selected[
            "spreadsheet_id"
        ]

        selected_name = selected[
            "spreadsheet_name"
        ]

        return Command(
            update={
                "spreadsheet_id": (
                    selected_id
                ),
                "spreadsheet_name": (
                    selected_name
                ),
                "messages": [
                    ToolMessage(
                        content=response,
                        artifact=delegate_artifact,
                        tool_call_id=(
                            runtime.tool_call_id
                        ),
                    )
                ],
            }
        )

    return delegate


def build_subagent(spec, model):
    """Build one stateless specialist agent."""
    return create_agent(
        model=model,
        tools=spec.tools,
        system_prompt=spec.system_prompt,
        state_schema=SpreadsheetState,
    )


def as_tool(spec, model):
    """Build one specialist and expose it to the orchestrator as a tool.

    The file manager is wrapped differently because it is the only one whose
    result changes the orchestrator's own state: it settles which spreadsheet
    everything after it works on.
    """
    agent = build_subagent(
        spec,
        model,
    )

    if spec.name == "file_manager":
        return _file_manager_tool(
            spec,
            agent,
        )

    return _delegate_tool(
        spec,
        agent,
    )


def current_spreadsheet(state) -> str | None:
    """The spreadsheet being worked on.

    One place asks this question, and one place answers it: the conversation's
    own state. The file manager writes it from inside a turn, and the page
    writes it through Session.use when someone picks from the sidebar.
    """
    return state.get("spreadsheet_name")


@dynamic_prompt
def _planner_prompt(request) -> str:
    """Say which spreadsheet is in hand on every model call.

    A subagent is told this in its instruction, but the planner was told
    nothing: it had to remember the name from a tool result several turns
    back, and a few turns in it stopped remembering and asked the user for a
    file it had already been given. State is not what was lost; nothing was
    ever putting it in front of the planner.
    """
    chosen = current_spreadsheet(request.state)

    if not chosen:
        return (
            f"{ORCHESTRATOR_PROMPT}\n"
            "CURRENT SPREADSHEET\n"
            "- None has been chosen yet. Delegate the choice to file_manager "
            "before any work that needs one.\n"
        )

    return (
        f"{ORCHESTRATOR_PROMPT}\n"
        "CURRENT SPREADSHEET\n"
        f'- "{chosen}" is the spreadsheet being worked on.\n'
        "- This is current. Do not ask the user which file is meant, and do "
        "not delegate to file_manager to find it out again.\n"
        "- Delegate to file_manager only if the user asks for a different "
        "spreadsheet, or asks what other spreadsheets exist.\n"
    )


def build_orchestrator():
    """Build the persistent planner and all of its specialist tools."""

    model = build_model()

    delegates = [
        as_tool(spec, model)
        for spec in SUBAGENTS
    ]

    return create_agent(
        model=model,
        tools=delegates,
        system_prompt=(
            ORCHESTRATOR_PROMPT
        ),
        middleware=[
            _planner_prompt,
            # A conversation is one thread that never forgets, so a long one
            # eventually leaves the model no room to answer in: it stops
            # mid-sentence, which reads as the agent having nothing to say.
            SummarizationMiddleware(
                model=model,
                trigger=(
                    "fraction",
                    SUMMARISE_AT,
                ),
                keep=(
                    "messages",
                    KEEP_MESSAGES,
                ),
            ),
            # A planner that keeps delegating gets stopped here rather than by
            # the recursion limit, which leaves a tool call unanswered and the
            # thread invalid for every turn after it.
            ModelCallLimitMiddleware(
                run_limit=MAX_TURNS,
                exit_behavior="end",
            ),
        ],
        state_schema=(
            OrchestratorState
        ),
        checkpointer=(
            InMemorySaver()
        ),
    )
