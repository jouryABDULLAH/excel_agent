"""Tests for the last look at an answer.

The validator reads the finished answer and either lets it through or sends
it back to the supervisor once. What it thinks of an answer is a judgment,
so the judge here is scripted: these pin the contract around it -- pass
through, one rewrite and out, never a loop -- rather than the judgment.
"""

from langchain_core.messages import AIMessage, HumanMessage
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph
from excel_agent.graph.validator import route_correction, validator_node
from excel_agent.runner import Answer, Session


def judging(*verdicts: str):
    """A validator whose judge says these things, in order."""
    return validator_node(
        ScriptedModel(script=[AIMessage(one) for one in verdicts])
    )


def finished(answer, question="how many rows?", **state):
    """State as the supervisor's finish leaves it."""
    return {
        "messages": [
            HumanMessage(question),
            AIMessage(answer),
        ],
        "final_answer": answer,
        "worker_results": ["[analyst] 5 rows"],
        "drawn_tables": [],
        "delegations": 1,
        "correction": None,
        **state,
    }


# Passing through


def test_an_answer_the_judge_accepts_ends_the_turn_and_clears_it():
    written = judging("PASS")(finished("The sheet has 5 rows."))

    assert written["correction"] is None
    assert written["worker_results"] == []
    assert written["drawn_tables"] == []
    assert written["delegations"] == 0
    # The answer is not touched, so nothing to write.
    assert "final_answer" not in written


def test_our_own_fallbacks_are_never_judged():
    # We wrote this sentence; a rewrite could only make it worse, so the
    # judge is not even asked -- an empty script would raise if it were.
    written = validator_node(ScriptedModel(script=[]))(
        finished("I could not produce an answer for that. Please try again.")
    )

    assert written["correction"] is None


def test_a_judge_that_cannot_be_reached_never_blocks_an_answer():
    class Broken(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("Groq said no")

    written = validator_node(Broken(script=[]))(finished("Five rows."))

    # A guard must not take the answer down with it.
    assert written["correction"] is None


# What gets sent back


def test_an_answer_the_judge_refuses_goes_back_with_the_reason():
    written = judging("FAIL: You answered in English; they wrote in Arabic.")(
        finished("There are 5 rows.", question="كم عدد الصفوف؟")
    )

    assert "Arabic" in written["correction"]
    # The evidence stays: the supervisor rewrites from it.
    assert "worker_results" not in written


def test_a_verdict_the_code_cannot_read_is_taken_as_a_pass():
    written = judging("I think this is probably fine, mostly?")(
        finished("Five rows.")
    )

    assert written["correction"] is None


# The second visit


def test_a_rewrite_the_judge_accepts_is_kept_and_settles_the_thread():
    state = finished("There are 5 rows.", question="كم عدد الصفوف؟")
    state["correction"] = "- Reply in Arabic."
    state["final_answer"] = "يوجد 5 صفوف."

    written = judging("PASS")(state)

    assert written["final_answer"] == "يوجد 5 صفوف."
    assert written["correction"] is None
    # The thread's last message is replaced in place, so the next turn's
    # supervisor remembers saying what the user actually read.
    (settled,) = written["messages"]
    assert settled.content == "يوجد 5 صفوف."
    assert settled.id == state["messages"][-1].id


def test_a_rewrite_still_wrong_never_ships_the_fault():
    """REGRESSION: a false claim was judged wrong, the rewrite failed too,
    and keeping the original shipped the very answer the judge rejected."""
    state = finished("All rows have been deleted.")
    state["correction"] = "- No report confirms that deletion."
    state["final_answer"] = "Every row is gone."

    written = judging("FAIL: still claims a deletion no report shows.")(state)

    assert written["final_answer"] != "All rows have been deleted."
    assert written["final_answer"] != "Every row is gone."
    assert "could not confirm" in written["final_answer"]
    assert "[analyst] 5 rows" in written["final_answer"]


def test_an_honest_giving_up_beats_a_wrong_answer():
    state = finished("All rows have been deleted.")
    state["correction"] = "- No report confirms that deletion."
    state["final_answer"] = (
        "I could not write up an answer, but this much was done:\n"
        "- [analyst] 5 rows"
    )

    written = judging("FAIL: it does not answer.")(state)

    assert written["final_answer"] == state["final_answer"]


def test_the_second_visit_always_ends_the_turn():
    state = finished("Something wrong.")
    state["correction"] = "- Wrong."
    state["final_answer"] = "Still wrong."

    written = judging("FAIL: still wrong.")(state)

    assert written["correction"] is None
    assert route_correction({**state, **written}) == "end"


def test_whatever_ships_is_free_of_characters_that_say_nothing():
    state = finished("The table below shows the rows.")
    state["correction"] = "- It came apart."
    state["final_answer"] = "Here 201" + "​ ​ ​ ​ ​" + " 2011."

    written = judging("PASS")(state)

    assert "​" not in written["final_answer"]


def test_pending_feedback_routes_back_to_the_supervisor():
    assert route_correction({"correction": "- fix it"}) == "supervisor"
    assert route_correction({"correction": None}) == "end"


# The whole loop, through the graph


def refusing_then_accepting():
    """A judge that turns one answer back, then accepts its rewrite."""
    return ScriptedModel(
        script=[
            AIMessage("FAIL: it leaks a marker that belongs to the machine."),
            AIMessage("PASS"),
        ]
    )


def test_a_bad_answer_is_rewritten_once_and_reaches_the_user_corrected():
    session = Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="count"),
                    AIMessage("done"),
                    AIMessage("QUESTION: which sheet did you mean?"),
                    AIMessage("Which sheet did you mean?"),
                ]
            ),
            judge=refusing_then_accepting(),
        )
    )
    session.use("TEST - Sales Orders")

    answers = [
        one.text for one in session.ask("how many rows?")
        if isinstance(one, Answer)
    ]

    assert answers == ["Which sheet did you mean?"]


def test_a_stray_delegation_during_the_rewrite_is_ignored_not_obeyed():
    """On the rewrite pass a call is ignored, but text riding along with it
    is still the rewrite: throwing both away lost a usable answer."""
    session = Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="count"),
                    AIMessage("done"),
                    AIMessage("QUESTION: which sheet?"),
                    AIMessage(
                        content="Which sheet did you mean?",
                        tool_calls=[
                            {
                                "name": "delegate",
                                "args": {"next": "analyst", "task": "again"},
                                "id": "9",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            ),
            judge=refusing_then_accepting(),
        )
    )
    session.use("TEST - Sales Orders")

    answers = [
        one.text for one in session.ask("how many rows?")
        if isinstance(one, Answer)
    ]

    assert answers == ["Which sheet did you mean?"]
