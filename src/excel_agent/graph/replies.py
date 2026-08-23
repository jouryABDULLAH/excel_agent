"""Text handling for what the user is shown.

The graph's state stays in state.py; everything here works on the words that
cross the worker/supervisor boundary or reach the user.
"""

import re

from langchain_core.messages import HumanMessage


def asked_for(state) -> str:
    """What the user actually wrote, which is also what says their language."""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)

    return ""


# Appended to a report whose table was cut because the application draws it:
# the supervisor cannot see the drawing, and an intro pointing at nothing
# reads as a worker that returned nothing, so it sends the task out again.
# The supervisor strips it from the final answer; the user never reads it.
DELIVERED = (
    "(The rows are shown to the user in a table drawn below your reply. "
    "Done. If you mention it, say the table below, never above.)"
)


def table_free(said: str) -> str:
    """The text with any markdown table removed.

    A table line is one that starts with a pipe; everything around it
    survives. Used on the worker's report, whose table the application draws.
    """
    return "\n".join(
        line
        for line in said.splitlines()
        if not line.lstrip().startswith("|")
    ).strip()


def undoubled(said: str) -> str:
    """The reply with an exactly repeated copy of itself removed.

    Deliberately narrow. The model sometimes emits its whole answer twice --
    "Fantasy.Fantasy." -- and only that is taken out: the two halves must be
    character for character the same. Anything looser starts correcting the
    model's writing, and an answer that is wrong or clumsy should stay
    visible rather than be tidied into looking right.
    """
    text = said.strip()

    for separator in ("", " ", "\n", "\n\n"):
        half, odd = divmod(len(text) - len(separator), 2)

        # A single character repeated is a word like "aa", not an answer
        # said twice.
        if odd or half < 2:
            continue

        first = text[:half]
        between = text[half:half + len(separator)]
        second = text[half + len(separator):]

        if between == separator and first == second:
            return first

    return said


def _named(line: str) -> frozenset[str]:
    """The column names one markdown table row carries."""
    return frozenset(
        cell
        for cell in (
            one.strip().strip("*_ ").casefold()
            for one in line.strip().strip("|").split("|")
        )
        if cell
    )


# What a cell holds when the planner sketched the shape of a table rather
# than its contents.
PLACEHOLDER = frozenset(
    {"(data)", "data", "...", "…", "-", "--", "—", "value", "(value)", ""}
)


def _sketch(block: list[str]) -> bool:
    """Whether a markdown table has a shape but nothing in it.

    Asked for columns B and D, the planner wrote "| B | D |" over two rows
    of "(data)" and left the real rows to the drawn table. That is not a
    duplicate of anything, so nothing matched it; it is a table with no
    contents, which is never worth showing.
    """
    body = [
        line
        for line in block[1:]
        if set(line.replace("|", "").replace(" ", "")) - {"-", ":"}
    ]

    if not body:
        return False

    return all(
        all(
            one.strip().strip("*_ ").casefold() in PLACEHOLDER
            for one in line.strip().strip("|").split("|")
        )
        for line in body
    )


def without_drawn_table(said: str, tables: list[list[str]] | None) -> str:
    """The reply with only an already-drawn table removed.

    The planner cannot see the tables the application draws, and writing the
    same rows out again shows the data twice. A prose table goes when its
    heading names exactly the columns of one drawn table -- overlap used to
    be enough, and two tables sharing a couple of column names cost the user
    an answer the planner had written itself -- or when it holds nothing but
    placeholders, which is a table of no contents whatever it is headed. A
    near miss otherwise errs the visible way: a duplicate on screen, never a
    deleted answer.
    """
    drawn = {
        frozenset(one.strip().casefold() for one in table if one.strip())
        for table in tables or []
    }
    drawn.discard(frozenset())

    if not drawn:
        return said

    kept: list[str] = []
    block: list[str] = []

    def settle() -> None:
        if block and _named(block[0]) not in drawn and not _sketch(block):
            kept.extend(block)

        block.clear()

    for line in said.splitlines():
        if line.lstrip().startswith("|"):
            block.append(line)
            continue

        settle()
        kept.append(line)

    settle()

    return "\n".join(kept).strip()


def arabic(said: str) -> bool:
    """Whether any of the text is written in Arabic script."""
    return any("؀" <= one <= "ۿ" for one in said)


def repeated_sentence(said: str) -> str | None:
    """A sentence the reply says more than once, or None.

    Looser than undoubled, which is why it only ever reports: the repeat is
    handed back to the model to rewrite rather than cut out of its answer.
    """
    counted: dict[str, str] = {}

    for one in re.split(r"(?<=[.!?؟])\s+|\n+", said):
        key = re.sub(r"[^\w]+", "", one, flags=re.UNICODE).casefold()

        if len(key) > 8:
            if key in counted:
                return counted[key].strip()

            counted[key] = one

    return None
