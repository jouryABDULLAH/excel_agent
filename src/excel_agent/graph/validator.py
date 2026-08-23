"""The last look at an answer before the user sees it.

The validator reads the finished answer and either lets it through or sends
it back to the supervisor once, with what is wrong written into state. It
never speaks to the user, never touches a spreadsheet, and never rewrites
anything itself: the supervisor owns the product's voice, so the correction
is the supervisor's to make.
"""

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from excel_agent.graph.replies import asked_for, spoken, visible
from excel_agent.graph.state import State


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "FAIL"]

    issue: str | None = Field(
        description="Failure explanation. Null when PASS."
    )

    kind: Literal[
        "unsupported_claim",
        "wrong_scope",
        "wrong_language",
        "reasoning_leak",
        "repetition",
        "garbled",
        "internal_machinery",
        "unnecessary_clarification",
    ] | None = Field(
        description="Failure category. Null when PASS."
    )

AUTHORED = (
    "I used every step this request is allowed",
    "I could not produce an answer for that.",
    "I could not write up an answer",
    "Something went wrong working that out:",
)


# Cleared here, not at the supervisor's finish: the validator checks the
# answer against these reports, so they have to outlive the answer.
DONE: dict = {
    "task": None,
    "worker_results": [],
    "drawn_tables": [],
    "delegations": 0,
    "correction": None,
}


JUDGE_PROMPT = """\
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

Return the structured verdict.

If verdict is PASS:
- issue must be null.
- kind must be null.

If verdict is FAIL:
- issue must be one concise sentence explaining what is wrong.
- kind must identify the failure category.
"""


def authored(answer: str) -> bool:
    """Whether the answer is one of the fixed fallback sentences, not the model's."""
    return answer.startswith(AUTHORED)


def judged(
    structured_judge,
    answer: str,
    question: str,
    reports: list[str],
    tables: list[list[str]] | None = None,
    history: list[str] | None = None,
) -> str | None:
    """What the judge finds wrong, or None.

    A strict json_schema, measured: 5 of 5 well-formed on the shapes that
    matter and 31 of 32 verdicts right, the same accuracy the older prose
    protocol gave. Asked for a schema through tool-calling this model used
    to return malformed JSON about half the time, which is why that is
    worth writing down rather than trying again.
    """

    if structured_judge is None:
        return None

    try:
        result: JudgeResult = structured_judge.invoke(
            [
                {"role": "system", "content": JUDGE_PROMPT},
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
                                ", ".join(one)
                                for one in tables or []
                            )
                            or "(none)"
                        )
                        + f"\n\nANSWER:\n{answer}"
                    ),
                },
            ]
        )

    # Fail open, deliberately: an answer the judge never read is far more
    # likely to be fine than not, and a guard that cannot be reached must
    # not take every turn down with it.
    except Exception:  # noqa: BLE001
        return None

    if result.verdict == "FAIL":
        return result.issue or "The answer failed validation."

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
    """Build the validator graph node."""

    structured_judge = (
        model.with_structured_output(
            JudgeResult,
            method="json_schema",
            strict=True,
        )
        if model is not None
        else None
    )

    def verdict(state: State, answer: str) -> str | None:
        return judged(
            structured_judge,
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

            if authored(answer):
                return DONE

            wrong = verdict(state, answer)

            # The supervisor needs the turn's evidence to rewrite from.
            return DONE if wrong is None else {"correction": wrong}

        kept = answer if authored(answer) else None

        if kept is None:
            kept = (
                answer
                if answer.strip() and verdict(state, answer) is None
                else unverified(state)
            )

        # Removing Characters that have no meaning: Zero-width and invisible formatting characters.
        kept = visible(kept)


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
