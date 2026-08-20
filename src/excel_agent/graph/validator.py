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


# The openings of every answer this codebase writes itself: the budget-spent
# and nothing-to-say fallbacks, and the failure report. Judging our own
# sentences wastes a pass, and a rewrite could only make them worse.
AUTHORED = (
    "I used every step this request is allowed",
    "I could not produce an answer for that.",
    "I could not write up an answer",
    "Something went wrong working that out:",
)


# Names of the machinery, which no user-facing answer has any business
# saying. The specialists' underscored names cannot appear in ordinary prose,
# so matching them raises no false alarms.
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


def authored(answer: str) -> bool:
    """Whether the answer is one of our own sentences, not the model's."""
    return answer.startswith(AUTHORED)


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


def validator_node(state: State, config=None) -> dict:
    """Pass the answer, or send it back to the supervisor exactly once."""
    answer = str(state.get("final_answer") or "")
    question = asked_for(state)

    # Second visit: the supervisor already rewrote once, so whatever the
    # rewrite looks like, the loop ends here. The original is still the last
    # message -- the correction pass wrote no message -- so the better of the
    # two is kept, and the thread is settled to match what the user sees.
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

    if found:
        # No cleanup: the supervisor needs the turn's evidence to rewrite.
        return {"correction": "\n".join(f"- {one}" for one in found)}

    return DONE


def route_correction(state: State) -> str:
    """Back to the supervisor while feedback is pending, otherwise out."""
    return (
        "supervisor"
        if state.get("correction") is not None
        else "end"
    )
