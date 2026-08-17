"""Build spreadsheet subagents and expose them to the orchestrator as tools."""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import dynamic_prompt
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from excel_agent import config
from excel_agent.model import RECURSION_LIMIT, build_model
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.subagents.registry import SUBAGENTS


class OrchestratorState(AgentState):
    """Conversation state owned by the outer orchestrator."""

    spreadsheet_id: str | None
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
    """Build the context passed from the orchestrator to a specialist."""
    original_request = _original_user_request(runtime)

    spreadsheet_id = runtime.state.get(
        "spreadsheet_id"
    )

    spreadsheet_name = runtime.state.get(
        "spreadsheet_name"
    )

    return (
        "ORIGINAL USER REQUEST:\n"
        f"{original_request or '(not available)'}\n\n"
        "CURRENT SPREADSHEET:\n"
        f"{spreadsheet_name or 'Not selected'}\n"
        "CURRENT SPREADSHEET ID:\n"
        f"{spreadsheet_id or 'Not selected'}\n\n"
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

        result = agent.invoke(
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
                ]
            },
            config={
                "recursion_limit": (
                    RECURSION_LIMIT
                ),
                "run_name": spec.name,
            },
        )

        messages = result.get(
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

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            subagent_instruction
                        ),
                    }
                ]
            },
            config={
                "recursion_limit": (
                    RECURSION_LIMIT
                ),
                "run_name": spec.name,
            },
        )

        messages = result.get(
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

        # Temporary compatibility until spreadsheet state is
        # removed from config during service/discovery cleanup.
        config.SPREADSHEET = (
            selected_name
        )

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
    """The spreadsheet being worked on, whoever settled it.

    The file manager writes it into the orchestrator's state; the sidebar
    writes only config.SPREADSHEET. Both have to be able to answer this, or
    picking a file from the page leaves the planner talking about one
    spreadsheet while the tools write to another.
    """
    return state.get("spreadsheet_name") or config.SPREADSHEET


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
            _planner_prompt
        ],
        state_schema=(
            OrchestratorState
        ),
        checkpointer=(
            InMemorySaver()
        ),
    )
