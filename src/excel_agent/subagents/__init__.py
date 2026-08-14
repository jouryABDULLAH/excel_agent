"""The agent: an orchestrator and the subagents it delegates to."""

from excel_agent.subagents.factory import build_orchestrator
from excel_agent.subagents.registry import SUBAGENTS, SubagentSpec

__all__ = ["SUBAGENTS", "SubagentSpec", "build_orchestrator"]
