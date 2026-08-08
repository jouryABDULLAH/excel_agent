"""Settings for the agent.

The API key is read from the shell environment. The .env version is kept
below as comments so it can be switched on later without changing anything
else in the project.
"""

import os
from pathlib import Path

# To read the key from a .env file instead of the shell, install the dev
# extra in pyproject.toml, and uncomment these two lines.
# from dotenv import load_dotenv
# load_dotenv()


PROJECT_ROOT = Path(__file__).resolve().parents[2]

WORKBOOK_PATH = PROJECT_ROOT / "data" / "sample.xlsx"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

MODEL = os.environ.get("EXCEL_AGENT_MODEL", "openai/gpt-oss-120b")


MAX_TURNS = 10


def require_api_key() -> str:
    """Return the API key, or explain how to set it if it is missing."""
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. In PowerShell, run:\n"
            '    $env:GROQ_API_KEY = "your-key-here"'
        )
    return GROQ_API_KEY