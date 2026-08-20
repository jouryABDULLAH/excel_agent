"""The last look at an answer before the user sees it.

The validator reads the finished answer and either lets it through or sends
it back to the supervisor once, with what is wrong written into state. It
never speaks to the user, never touches a spreadsheet, and never rewrites
anything itself: the supervisor owns the product's voice, so the correction
is the supervisor's to make.
"""

from langchain_core.messages import AIMessage

from excel_agent.graph.replies import arabic, asked_for, repeated_sentence
from excel_agent.graph.state import WORKERS, State


AUTHORED = (
    "I used every step this request is allowed",
    "I could not produce an answer for that.",
    "I could not write up an answer",
    "Something went wrong working that out:",
)


# Internal names cannot appear in ordinary prose.
INTERNAL = (*WORKERS, "delegate tool", "specialist")


# What ends the turn once the validator is done with it. The turn-scoped
# fields are cleared here rather than at the supervisor's finish, because the
# validator needs the evidence the supervisor used: an answer cannot be
# checked against worker reports that were thrown away in the same update
# that wrote it.
DONE: dict = {
    "task": None,
    "worker_results": [],
    "drawn_tables": [],
    "delegations": 0,
    "correction": None,
}


JUDGING = """\
You review one answer from a spreadsheet assistant before the user sees it.
You are given the user's question, reports from the specialists that did the
work, and the answer. Judge only these two things:

1. UNSUPPORTED SUCCESS: the answer claims a change was made that no report
   establishes. A report saying a change failed, partially succeeded, or
   never happened means the answer must not claim it succeeded.
2. SCOPE: the answer ignores what was asked and answers something else.
   Extra helpful detail is fine; answering a different question is not.

Judge nothing else. Style, length, formatting and language are not yours.

Reply with exactly one line:
PASS
or
FAIL: <one sentence saying what is wrong, addressed to the writer>
"""


def authored(answer: str) -> bool:
    """Whether the answer is one of the fixed fallback sentences, not the model's."""
    return answer.startswith(AUTHORED)


def judged(model, answer: str, question: str, reports: list[str]) -> str | None:
    """What the judge finds wrong, or None.

    Prose in, one line out. Asked for the verdict inside a schema. Anything else it says is
    treated as a pass, because an unreadable verdict must never block a good
    answer.
    """
    if model is None:
        return None

    try:
        said = model.invoke(
            [
                {"role": "system", "content": JUDGING},
                {
                    "role": "user",
                    "content": (
                        f"QUESTION:\n{question}\n\n"
                        "SPECIALIST REPORTS:\n"
                        + ("\n".join(reports) or "(none)")
                        + f"\n\nANSWER:\n{answer}"
                    ),
                },
            ]
        )

    # A judge that cannot be reached must never take the answer down with it.
    except Exception:  # noqa: BLE001
        return None

    verdict = str(said.content or "").strip()

    if verdict.upper().startswith("FAIL"):
        return verdict.split(":", 1)[-1].strip() or None

    return None


def problems(answer: str, question: str) -> list[str]:
    """Everything deterministically wrong with the answer, as feedback.

    Each entry is written to the supervisor, which is who acts on it.
    """
    found = []

    # One-directional on purpose: an Arabic answer to an English question
    # was seen and accepted, so only the reverse is a failure.
    if arabic(question) and not arabic(answer):
        found.append(
            "The user wrote in Arabic but the answer contains no Arabic. "
            "Reply in the user's language."
        )

    if "QUESTION:" in answer:
        found.append(
            'The answer leaks the internal "QUESTION:" marker. Ask the '
            "user plainly, without the marker."
        )

    said = answer.casefold()

    for name in INTERNAL:
        if name in said:
            found.append(
                f'The answer mentions "{name}", which is internal '
                "machinery the user should never see. Say what happened "
                "without naming who or what did it."
            )
            break

    repeat = repeated_sentence(answer)

    if repeat is not None:
        found.append(
            f'The answer says "{repeat[:80]}" more than once. Say each '
            "thing exactly once."
        )

    return found


def acceptable(answer: str, question: str) -> bool:
    """Whether a rewrite is worth shipping in place of the original."""
    return (
        bool(answer.strip())
        and not authored(answer)
        and not problems(answer, question)
    )


def validator_node(model=None):
    """The node, holding the judge it consults for the semantic checks."""

    def validate(state: State, config=None) -> dict:
        """Pass the answer, or send it back to the supervisor exactly once."""
        answer = str(state.get("final_answer") or "")
        question = asked_for(state)

        # The supervisor already rewrote once, so the loop ends here: the
        # better of the two answers is kept, and the thread settled to match.
        if state.get("correction") is not None:
            last = (state.get("messages") or [None])[-1]
            original = str(getattr(last, "content", "") or "")

            kept = answer if acceptable(answer, question) else original

            return {
                **DONE,
                "final_answer": kept,
                "messages": [AIMessage(kept, id=last.id)],
            }

        if authored(answer):
            return DONE

        found = problems(answer, question)

        # The judge runs only when the cheap checks found nothing.
        if not found:
            semantic = judged(
                model,
                answer,
                question,
                state.get("worker_results") or [],
            )

            if semantic is not None:
                found = [semantic]

        if found:
            # No cleanup: the supervisor needs the evidence to rewrite.
            return {
                "correction": "\n".join(f"- {one}" for one in found)
            }

        return DONE

    return validate


def route_correction(state: State) -> str:
    """Back to the supervisor while feedback is pending, otherwise out."""
    return (
        "supervisor"
        if state.get("correction") is not None
        else "end"
    )
