"""State schema owned by the orchestrator."""

from langchain.agents import AgentState


class OrchestratorState(AgentState):
    """Conversation state owned by the outer orchestrator."""

    spreadsheet_id: str | None
    spreadsheet_name: str | None
    delegated_task: str | None
    next_agent: str | None
    render_data: bool
    last_agent_response: str | None
