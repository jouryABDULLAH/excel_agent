"""The last look at an answer before the user sees it.

The validator reads the finished answer and either lets it through or sends
it back to the supervisor once, with what is wrong written into state. It
never speaks to the user, never touches a spreadsheet, and never rewrites
anything itself: the supervisor owns the product's voice, so the correction
is the supervisor's to make.
"""

from langchain_core.messages import AIMessage

from excel_agent.graph.replies import asked_for, spoken, visible
from excel_agent.graph.state import State


AUTHORED = (
    "I used every step this request is allowed",
    "I could not produce an answer for that.",
    "I could not write up an answer",
    "Something went wrong working that out:",
)


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
You are the last reader of an answer from a spreadsheet assistant before the
person who asked sees it. You are given their question, reports from the
specialists that did the work, and the answer.

Judge one thing: is this a finished answer that person can read?

It is not, if any of these is true:
- It claims a change was made that no report establishes. A report saying a
  change failed, partly succeeded, or never happened means the answer must
  not claim it succeeded.
- It answers a different question than the one asked. Extra detail is fine;
  answering something else is not.
- It is written in a language the person has not used in this conversation.
  Once they have written to you in a language, answering in that language is
  right, even if a later question of theirs is in another one.
- It shows the writer's working rather than the result: correcting itself
  mid-sentence, weighing what to say, or talking to itself.
- It says the same thing twice, or repeats the question back instead of
  answering it.
- It is garbled, cut off mid-word, or padded with characters that carry no
  meaning.
- It names the machinery: a specialist, an agent, a tool, or a marker such
  as "QUESTION:" that belongs to the machine rather than the conversation.

Asking the person a genuine question is a finished answer, when what they
asked for cannot be done without knowing something only they know.

When a table is named below as drawn, the person is already looking at those
rows beneath the answer. The answer should introduce them in a sentence and
must not list them again: "here are the first five rows" is finished, not
unfinished.

Reply with exactly one line:
PASS
or
FAIL: <one sentence saying what is wrong, addressed to the writer>
"""


def authored(answer: str) -> bool:
    """Whether the answer is one of the fixed fallback sentences, not the model's."""
    return answer.startswith(AUTHORED)


def judged(
    model,
    answer: str,
    question: str,
    reports: list[str],
    tables: list[list[str]] | None = None,
    history: list[str] | None = None,
) -> str | None:
    """What the judge finds wrong, or None.

    Prose in, one line out: this model returned malformed JSON about half the
    time when asked for a schema. Anything that is not a FAIL line is treated
    as a pass, because an unreadable verdict must never block a good answer.
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
                        "EVERYTHING THEY HAVE WRITTEN, OLDEST FIRST:\n"
                        + ("\n".join(history) if history else question)
                        + f"\n\nTHEIR QUESTION NOW:\n{question}\n\n"
                        + "SPECIALIST REPORTS:\n"
                        + ("\n".join(reports) or "(none)")
                        + "\n\nTABLES DRAWN BELOW THE ANSWER:\n"
                        + (
                            "\n".join(
                                ", ".join(one) for one in tables or []
                            )
                            or "(none)"
                        )
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


def unverified(state: State) -> str:
    """The honest reply when an answer claimed work its reports do not show.

    Used when both the answer and its rewrite failed the claims check: the
    turn has to say something, and the one safe thing is what the reports
    actually establish.
    """
    done = "\n".join(
        f"- {one}" for one in state.get("worker_results") or []
    )

    if not done:
        return (
            "I could not confirm that as done. No change is known to have "
            "been made."
        )

    return (
        "I could not confirm that as done. What actually happened:\n"
        f"{done}"
    )


def validator_node(model=None):
    """The node, holding the judge that reads the answer."""

    def verdict(state: State, answer: str) -> str | None:
        """What is wrong with this answer, in a sentence, or nothing."""
        return judged(
            model,
            answer,
            asked_for(state),
            state.get("worker_results") or [],
            state.get("drawn_tables") or [],
            spoken(state),
        )

    def validate(state: State, config=None) -> dict:
        """Pass the answer, or send it back to the supervisor exactly once."""
        answer = str(state.get("final_answer") or "")

        if state.get("correction") is None:
            # Our own fallback sentences are not the model's to be judged,
            # and a rewrite could only make them worse.
            if authored(answer):
                return DONE

            wrong = verdict(state, answer)

            # No cleanup on the way back: the supervisor needs the turn's
            # evidence to rewrite from.
            return DONE if wrong is None else {"correction": wrong}

        # The supervisor has had its one rewrite, so the turn ends here
        # whatever this answer looks like. An authored rewrite is it giving
        # up honestly, which is worth shipping and worth not judging.
        kept = answer if authored(answer) else None

        if kept is None:
            kept = (
                answer
                if answer.strip() and verdict(state, answer) is None
                # Still wrong, so say only what the reports establish
                # rather than ship a fault the judge has named twice.
                else unverified(state)
            )

        # The characters carry no meaning, so taking them out changes
        # nothing but the mess.
        kept = visible(kept)

        # Settled by id, so the thread holds what the user actually read.
        last = (state.get("messages") or [None])[-1]

        return {
            **DONE,
            "final_answer": kept,
            "messages": [AIMessage(kept, id=last.id)],
        }

    return validate


def route_correction(state: State) -> str:
    """Back to the supervisor while feedback is pending, otherwise out."""
    return (
        "supervisor"
        if state.get("correction") is not None
        else "end"
    )
