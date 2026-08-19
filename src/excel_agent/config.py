"""Settings for the agent.

Read from the environment, and from a .env file beside the project when there
is one. The shell wins where both name the same thing, so a value set for one
run is not quietly overruled by a file.
"""

import os
import sys
from pathlib import Path

# Where credentials.json, token.json and .env are looked for.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

try:
    from dotenv import load_dotenv
except ImportError:
    # Nothing to read a .env with. The shell is then the only source.
    pass
else:
    # Before anything below is read, and named from the project root rather
    # than from wherever the process was started, so `streamlit run` from any
    # directory finds the same file.
    #
    # This was switched off, and a .env holding the tracing key looked like
    # configuration while doing nothing: every run was sent to LangSmith
    # without a key and refused, so the traces someone went looking for after
    # a bad turn had never been recorded.
    load_dotenv(PROJECT_ROOT / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

MODEL = os.environ.get("EXCEL_AGENT_MODEL", "openai/gpt-oss-120b")

# The spreadsheet a conversation opens on, by name, or nothing to start having
# chosen none. Only ever read: which spreadsheet is being worked on belongs to
# a Session, because two browser sessions in one process have two answers and
# a module global has one.
START_SPREADSHEET = os.environ.get("EXCEL_AGENT_SPREADSHEET")

# Tracing is LangSmith's, switched on by the environment; see .env.example.


MAX_TURNS = 20

# Run the turn through the StateGraph instead of the older orchestrator.
# Transitional: goes away once the graph is the only one left.
USE_GRAPH = os.environ.get("EXCEL_AGENT_GRAPH", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


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
