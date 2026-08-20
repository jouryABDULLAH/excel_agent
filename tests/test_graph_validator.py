"""Tests for the last look at an answer.

The validator reads, decides, and at most sends the answer back once. What
is pinned here is that one-visit contract: pass through untouched, or one
rewrite pass and out, never a loop.
"""

from langchain_core.messages import AIMessage, HumanMessage
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph
from excel_agent.graph.validator import route_correction, validator_node
from excel_agent.runner import Answer, Session


# The judge is off here: these pin the deterministic checks and the one-visit
# contract, which must hold with no model at all.
checked = validator_node()


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


def test_a_clean_answer_ends_the_turn_and_clears_it():
    written = checked(finished("The sheet has 5 rows."))

    assert written["correction"] is None
    assert written["worker_results"] == []
    assert written["drawn_tables"] == []
    assert written["delegations"] == 0
    # The answer is not touched, so nothing to write.
    assert "final_answer" not in written


def test_our_own_fallbacks_are_never_judged():
    # We wrote this sentence; a rewrite could only make it worse.
    written = checked(
        finished("I could not produce an answer for that. Please try again.")
    )

    assert written["correction"] is None


# What gets sent back


def test_an_english_answer_to_an_arabic_question_goes_back():
    written = checked(
        finished("There are 5 rows.", question="كم عدد الصفوف؟")
    )

    assert "Arabic" in written["correction"]
    # The evidence stays: the supervisor rewrites from it.
    assert "worker_results" not in written


def test_an_arabic_answer_to_an_english_question_is_fine():
    # Seen live and accepted, so only the reverse direction is a failure.
    written = checked(finished("يوجد 5 صفوف."))

    assert written["correction"] is None


def test_a_leaked_question_marker_goes_back():
    written = checked(
        finished("QUESTION: which column did you mean?")
    )

    assert "QUESTION:" in written["correction"]


def test_naming_the_machinery_goes_back():
    written = checked(
        finished("The row_editor added your row.")
    )

    assert "row_editor" in written["correction"]


def test_a_sentence_said_twice_goes_back():
    written = checked(
        finished(
            "The highest rated book is Dune, with a rating of 5. "
            "Some other sentence. "
            "The highest rated book is Dune, with a rating of 5."
        )
    )

    assert "more than once" in written["correction"]


# The second visit


def test_the_rewrite_is_kept_and_settles_the_thread():
    state = finished("There are 5 rows.", question="كم عدد الصفوف؟")
    state["correction"] = "- Reply in Arabic."
    state["final_answer"] = "يوجد 5 صفوف."

    written = checked(state)

    assert written["final_answer"] == "يوجد 5 صفوف."
    assert written["correction"] is None
    # The thread's last message is replaced in place, so the next turn's
    # supervisor remembers saying what the user actually read.
    (settled,) = written["messages"]
    assert settled.content == "يوجد 5 صفوف."
    assert settled.id == state["messages"][-1].id


def test_a_rewrite_that_is_worse_ships_the_original():
    state = finished("There are 5 rows.")
    state["correction"] = "- Say it better."
    state["final_answer"] = ""

    written = checked(state)

    # An empty rewrite loses to the answer the model already wrote.
    assert written["final_answer"] == "There are 5 rows."
    assert written["correction"] is None


def test_a_still_broken_rewrite_never_goes_around_again():
    state = finished("QUESTION: which one?")
    state["correction"] = "- Drop the marker."
    state["final_answer"] = "QUESTION: which one?"

    written = checked(state)

    # Second visit always ends the turn, however the rewrite came out.
    assert written["correction"] is None
    assert route_correction({**state, **written}) == "end"


def test_pending_feedback_routes_back_to_the_supervisor():
    assert route_correction({"correction": "- fix it"}) == "supervisor"
    assert route_correction({"correction": None}) == "end"


# The whole loop, through the graph


def test_a_bad_answer_is_rewritten_once_and_reaches_the_user_corrected():
    session = Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="count"),
                    # The analyst has no sheet stubbed here; whatever it
                    # reports, the supervisor's answer is what is under test.
                    AIMessage("done"),
                    AIMessage("QUESTION: which sheet did you mean?"),
                    AIMessage("Which sheet did you mean?"),
                ]
            ),
            judge=None,
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
                    # The rewrite arrives with a hallucinated call attached.
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
            judge=None,
        )
    )
    session.use("TEST - Sales Orders")

    answers = [
        one.text for one in session.ask("how many rows?")
        if isinstance(one, Answer)
    ]

    # The text shipped and the call did not run: a script with no entry for
    # a second analyst visit would have failed loudly here if it had.
    assert answers == ["Which sheet did you mean?"]
