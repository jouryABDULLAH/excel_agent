"""What the orchestrator and each subagent are told, for the backend in use."""

from excel_agent.config import BACKEND
from excel_agent.subagents.prompts import local, sheets

MODULES = {"local": local, "sheets": sheets}


def prompts_for(backend: str):
    """The subagent prompt module for one backend.

    Raises ValueError, naming both backends, when asked for neither.
    """
    if backend not in MODULES:
        raise ValueError(f'EXCEL_AGENT_BACKEND is "{backend}". Use "local" or "sheets".')

    return MODULES[backend]


IN_USE = prompts_for(BACKEND)

DELEGATED = IN_USE.DELEGATED
ORCHESTRATOR_PROMPT = IN_USE.ORCHESTRATOR_PROMPT

ANALYST_PROMPT = IN_USE.ANALYST_PROMPT
ROW_EDITOR_PROMPT = IN_USE.ROW_EDITOR_PROMPT
STRUCTURE_PROMPT = IN_USE.STRUCTURE_PROMPT
CHART_PROMPT = IN_USE.CHART_PROMPT

ANALYST_DESCRIPTION = IN_USE.ANALYST_DESCRIPTION
ROW_EDITOR_DESCRIPTION = IN_USE.ROW_EDITOR_DESCRIPTION
STRUCTURE_DESCRIPTION = IN_USE.STRUCTURE_DESCRIPTION
CHART_DESCRIPTION = IN_USE.CHART_DESCRIPTION