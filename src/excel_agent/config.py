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

# The spreadsheet a conversation opens on, by name, or nothing to start having
# chosen none. Only ever read: which spreadsheet is being worked on belongs to
# a Session, because two browser sessions in one process have two answers and
# a module global has one.
START_SPREADSHEET = os.environ.get("EXCEL_AGENT_SPREADSHEET")

# Tracing is LangSmith's, switched on by the environment; see .env.example.


MAX_TURNS = 20


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
