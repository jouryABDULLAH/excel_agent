"""What the tools were asked for, and what they gave back.

Written for debugging the Google tools, where a failure is usually an
argument the model built wrongly or an error the API returned. One JSON
object per call, appended as it happens, so a run that dies still leaves a
readable file up to the point it stopped.
"""

import contextvars
import functools
import json
import time
from contextlib import contextmanager
from datetime import datetime

from excel_agent.config import TRACE_FILE, TRACING

# Who is holding the work, from the top down. One name for the agent that was
# asked, and another for each subagent it has handed the work on to. A stack
# rather than a name, so a call says who delegated it as well as who ran it.
#
# It belongs to the turn rather than to the program, which is why it is a
# context variable and not a global: two turns running at once, two browser
# tabs say, each keep their own.
CALLERS = contextvars.ContextVar("callers", default=("agent",))


def caller() -> str:
    """Who is running a tool, from the top down: "orchestrator > row_editor"."""
    return " > ".join(CALLERS.get())


@contextmanager
def asked(name: str):
    """Name the agent at the top for the length of one turn."""
    token = CALLERS.set((name,))
    try:
        yield
    finally:
        CALLERS.reset(token)


@contextmanager
def called_by(name: str):
    """Record tools called inside this block as that subagent's work."""
    token = CALLERS.set((*CALLERS.get(), name))
    try:
        yield
    finally:
        CALLERS.reset(token)


def record(entry: dict) -> None:
    """Append one object to the trace file."""
    if not TRACING:
        return

    TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now().strftime("%H:%M:%S.%f")[:-3], **entry}
    with open(TRACE_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def traced(function):
    """Record what a tool was called with and what came back.

    Goes under @tool, so what is recorded is the arguments the tool really
    received, after the model's JSON has been parsed into them. A tool that
    raises is recorded too, then left to raise: an API error is the thing
    worth seeing, not something to swallow.
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        # An argument left out arrives as None, and every tool here has more
        # optional arguments than a call usually gives. Keeping them would
        # bury the two or three that were actually asked for.
        given = {name: value for name, value in kwargs.items() if value is not None}
        entry = {"by": caller(), "tool": function.__name__, "arguments": given}

        try:
            answer = function(*args, **kwargs)
        except Exception as failure:
            entry["raised"] = f"{type(failure).__name__}: {failure}"
            entry["ms"] = round((time.perf_counter() - started) * 1000)
            record(entry)
            raise

        entry["returned"] = answer
        entry["ms"] = round((time.perf_counter() - started) * 1000)
        record(entry)
        return answer

    return wrapper
