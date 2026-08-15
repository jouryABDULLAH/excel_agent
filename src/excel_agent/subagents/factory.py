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

    @tool(spec.name, description=spec.description)
    def delegate(
        instruction: str,
        runtime: ToolRuntime
    ) -> dict:
        """Delegate a spreadsheet task to this specialized subagent."""

        spreadsheet_id = runtime.state.get("spreadsheet_id")
        spreadsheet_name = runtime.state.get("spreadsheet_name")

        subagent_instruction = (
            f"Current spreadsheet: "
            f"{spreadsheet_name or 'Not selected'}\n"
            f"Current spreadsheet ID: "
            f"{spreadsheet_id or 'Not selected'}\n\n"
            f"Task:\n{instruction}"
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": subagent_instruction}]},
             config={"recursion_limit": RECURSION_LIMIT, "run_name": spec.name},
        )

        messages = result["messages"]


        if spec.return_tool_results:
            return {
                "response": str(messages[-1].content),
                "tool_results": [
                    message.content
                    for message in messages
                    if isinstance(message, ToolMessage) and message.content
                ],
            }


        return {
            "response": str(messages[-1].content),
        }
        
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
