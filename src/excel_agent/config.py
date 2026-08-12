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


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Every workbook the agent may open lives here. Nothing outside this folder is
# reachable, which is what makes it safe to let a name arrive from the model.
DATA_DIR = PROJECT_ROOT / "data"

WORKBOOK_PATH = DATA_DIR / "sample.xlsx"

# Backups are kept away from the data folder so they cannot be mistaken for
# workbooks to work on.
BACKUP_DIR = PROJECT_ROOT / "backups"

# Max backups to keep per file. Older ones are deleted as new ones are
# taken.
BACKUP_KEEP = 3

WORKBOOK_SUFFIX = ".xlsx"

# lists files
def workbook_names() -> list[str]:
    """The workbooks that can be worked on, in alphabetical order."""
    if not DATA_DIR.is_dir():
        return []
    return sorted(path.name for path in DATA_DIR.glob(f"*{WORKBOOK_SUFFIX}"))


# name -> file PATH
def resolve_workbook(name: str | None = None) -> Path:
    """Turn the name of a workbook into the path of a file to open.

    Names arrive from the model, so only a plain file name is accepted: one
    carrying a folder of its own could otherwise reach anywhere on the disk.
    The suffix is optional, because "sales" is what a person would say.

    Returns the default workbook when given nothing. Raises ValueError, with
    a message worth showing to the model, when the name reaches nowhere.
    """
    if not name:
        return WORKBOOK_PATH

    wanted = name.strip()
    if wanted != Path(wanted).name or wanted in (".", ".."):
        raise ValueError(
            f'"{name}" is not a workbook name. Give the name of a file in the '
            "data folder."
        )

    if not wanted.lower().endswith(WORKBOOK_SUFFIX):
        wanted += WORKBOOK_SUFFIX

    # Matched against the folder listing rather than by asking the file system
    # whether the file is there. Windows would open "SAMPLE.XLSX" happily and
    # hand back a path spelled the way it was asked for, which would then show
    # up in messages and in the names of backup files. Going through the
    # listing returns the name the file really has, on every platform.
    for candidate in workbook_names():
        if candidate.lower() == wanted.lower():
            return DATA_DIR / candidate

    available = ", ".join(workbook_names()) or "no workbooks at all"
    raise ValueError(f'There is no workbook called "{name}". The folder has: {available}.')

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

MODEL = os.environ.get("EXCEL_AGENT_MODEL", "openai/gpt-oss-120b")

# Which set of tools the agent is given: "local" for the workbooks in the data
# folder, "sheets" for spreadsheets on Google Drive. The tools package reads
# this and hands over one set or the other.
BACKEND = os.environ.get("EXCEL_AGENT_BACKEND", "local")


MAX_TURNS = 10


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
            stream.reconfigure(encoding="utf-8", errors="replace")
