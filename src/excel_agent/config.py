"""Settings for the agent.

The API key is read from the shell environment. The .env version is kept
below as comments so it can be switched on later without changing anything
else in the project.
"""

import os
import sys
from pathlib import Path

# To read the key from a .env file instead of the shell, install the dev
# extra in pyproject.toml, and uncomment these two lines.
# from dotenv import load_dotenv
# load_dotenv()


# Where credentials.json and token.json are looked for.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

MODEL = os.environ.get("EXCEL_AGENT_MODEL", "openai/gpt-oss-120b")

# The spreadsheet being worked on, by name. Read at the moment a tool asks for
# it rather than at import, so a spreadsheet chosen part way through a
# conversation is the one that answers.
SPREADSHEET = os.environ.get("EXCEL_AGENT_SPREADSHEET")

# Tracing is LangSmith's, switched on by the environment; see .env.example.


def require_api_key() -> str:
    """Return the API key, or explain how to set it if it is missing."""
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. In PowerShell, run:\n"
            '    $env:GROQ_API_KEY = "your-key-here"'
        )
    return GROQ_API_KEY


def use_utf8_output() -> None:
    """Let the console print whatever the model says.

    Windows consoles default to a codepage that cannot render characters the
    model reaches for, such as a non-breaking hyphen, and printing one raises
    UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace") # type: ignore
