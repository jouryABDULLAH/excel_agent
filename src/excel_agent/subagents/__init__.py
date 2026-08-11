"""The multi agent variant: an orchestrator and the subagents it delegates to."""

from excel_agent.subagents.factory import build, build_orchestrator
from excel_agent.subagents.registry import SUBAGENTS, SubagentSpec

__all__ = ["SUBAGENTS", "SubagentSpec", "build", "build_orchestrator"]
