"""Factory for building the agents."""

from langchain.agents import create_agent, AgentState
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from excel_agent.model import RECURSION_LIMIT, build_model
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.subagents.registry import SUBAGENTS, SubagentSpec
from excel_agent.tools import find_spreadsheet, list_workbooks, use_spreadsheet
from langchain_core.messages import (
    HumanMessage,
    ToolMessage,
)

def _original_user_request(runtime: ToolRuntime) -> str:
    """Return the latest real user message from the orchestrator state."""
    messages = runtime.state.get("messages") or []

    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content

            if isinstance(content, str):
                return content

            return str(content)

    return ""

class OrchestratorState(AgentState):
    """State that belongs to the user's spreadsheet session."""
    
    spreadsheet_id: str | None
    spreadsheet_name: str | None
    # active_sheet_id: int | None
    # active_sheet_name: str | None


def as_tool(spec: SubagentSpec, model):
    """Wrap one subagent as a tool available to the orchestrator."""

    agent = create_agent(
        model=model, 
        tools=list(spec.tools), 
        system_prompt=spec.system_prompt
    )

    @tool(
        spec.name,
        description=spec.description,
        response_format="content_and_artifact",
    )
    def delegate(
        instruction: str,
        runtime: ToolRuntime,
        render_data: bool = False,
    ) -> tuple[str, dict | None]:
        """Delegate a spreadsheet task to this specialized subagent."""

        spreadsheet_id = runtime.state.get("spreadsheet_id")
        spreadsheet_name = runtime.state.get("spreadsheet_name")

        original_request = _original_user_request(
            runtime
        )

        subagent_instruction = (
            "ORIGINAL USER REQUEST:\n"
            f"{original_request or '(not available)'}\n\n"
            "CURRENT SPREADSHEET:\n"
            f"{spreadsheet_name or 'Not selected'}\n"
            "CURRENT SPREADSHEET ID:\n"
            f"{spreadsheet_id or 'Not selected'}\n\n"
            "TASK:\n"
            f"{instruction}"
        )

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": subagent_instruction,
                    }
                ]
            },
            config={
                "recursion_limit": RECURSION_LIMIT,
                "run_name": spec.name,
            },
        )

        messages = result["messages"]
        response = str(messages[-1].content)

        if not spec.return_tool_results:
            return response, None

        tool_results = []
        tool_artifacts = []

        for message in messages:
            if not isinstance(message, ToolMessage):
                continue

            if message.content:
                tool_results.append(message.content)

            if message.artifact is not None:
                tool_artifacts.append(message.artifact)


        return (
            response,
            {
                "subagent": spec.name,
                "response": response,
                "tool_results": tool_results,
                "tool_artifacts": tool_artifacts,
                "render_data": render_data,
            },
        )
    
    return delegate

def build_orchestrator():
    """Build the orchestrator and its specialized subagents."""

    model = build_model()

    delegates = [
        as_tool(spec, model) 
        for spec in SUBAGENTS
    ]

    return create_agent(
        model=model,
        tools=[
            list_workbooks,
            find_spreadsheet,
            use_spreadsheet, 
            *delegates
        ],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=OrchestratorState,
        checkpointer=InMemorySaver(),
    )
