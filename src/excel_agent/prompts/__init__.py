"""What the agent is told, for whichever backend it is working through.

A prompt names the tools the agent has and describes what they do, so it
cannot be shared between backends: told the local rules, the Google agent
would refuse formatting and sorting it can do perfectly well.

Importing CANNOT_DO or SYSTEM_PROMPT from here gives the pair for the backend
in use, so nothing downstream has to know there is more than one.
"""

from excel_agent.config import BACKEND
from excel_agent.prompts import local, sheets

MODULES = {"local": local, "sheets": sheets}


def prompts_for(backend: str):
    """The prompt module for one backend.

    Raises ValueError, naming both backends, when asked for neither.
    """
    if backend not in MODULES:
        raise ValueError(f'EXCEL_AGENT_BACKEND is "{backend}". Use "local" or "sheets".')

    return MODULES[backend]


IN_USE = prompts_for(BACKEND)

CANNOT_DO = IN_USE.CANNOT_DO
SYSTEM_PROMPT = IN_USE.SYSTEM_PROMPT
