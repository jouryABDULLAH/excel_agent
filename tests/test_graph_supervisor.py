"""Tests for the planner node.

The model is a script here, so nothing below tests whether the supervisor
routes sensibly. What is tested is that its two decisions become the right
state, and that the prompt it reads is built from the state it was handed.
"""

from langchain_core.messages import AIMessage
from scripted import ScriptedModel, calling

from excel_agent.graph.supervisor import (
    build_supervisor,
    supervisor_instructions,
    supervisor_node,
)


ASKED = {
    "messages": [{"role": "user", "content": "how many rows?"}],
    "spreadsheet_name": "TEST - Sales Orders",
    "spreadsheet_id": "an-id",
    "worker_results": [],
}


def deciding(script, **state) -> dict:
    """Run the node once with a scripted decision."""
    node = supervisor_node(build_supervisor(ScriptedModel(script=script)))

    return node({**ASKED, **state})


# What a decision becomes


def test_delegating_names_the_worker_and_what_it_should_do():
    written = deciding(
        [calling("delegate", "1", next="analyst", task="count the rows")]
    )

    assert written["route"] == "analyst"
    assert written["task"] == "count the rows"


def test_delegating_clears_any_answer_left_from_before():
    written = deciding(
        [calling("delegate", "1", next="analyst", task="count the rows")],
        final_answer="an answer from the last turn",
    )

    # Left alone, the runner would show last turn's answer for this one.
    assert written["final_answer"] is None


def test_a_blank_decision_is_asked_again():
    """REGRESSION: the model returned no tool call and no text, and that
    became an empty reply -- shown as a turn that said nothing."""
    written = deciding(
        [AIMessage(""), AIMessage("There are 51 rows.")],
    )

    assert written["route"] == "end"
    assert written["final_answer"] == "There are 51 rows."


def test_two_blank_decisions_still_answer_with_something():
    written = deciding(
        [AIMessage(""), AIMessage("")],
        worker_results=["[file_manager] Selected it."],
    )

    assert written["route"] == "end"
    # Never a silent blank: the user is told what was done.
    assert "Selected it." in written["final_answer"]


def test_a_table_the_supervisor_rebuilt_is_cut_when_one_is_drawn():
    """The worker's table was stripped; the supervisor wrote it out again
    from the report and the user saw the data twice."""
    written = deciding(
        [AIMessage(
            "Here they are:\n| Title | Author |\n|---|---|\n| Dune | Herbert |"
        )],
        drawn_columns=["Title", "Author"],
    )

    assert written["final_answer"] == "Here they are:"


def test_a_table_the_supervisor_wrote_itself_is_kept():
    """REGRESSION: an answer that WAS a table reached the user as nothing.

    Asked to suggest columns, the planner replies with a table of its own.
    Cutting every table whenever one had been drawn threw that away, and the
    turn arrived empty.
    """
    suggestion = (
        "Columns worth adding:\n"
        "| Column | Why |\n|---|---|\n| Pages | length of each book |"
    )

    written = deciding(
        [AIMessage(suggestion)],
        drawn_columns=["Title", "Author", "Rating"],
    )

    assert written["final_answer"] == suggestion


def test_a_table_stays_when_nothing_is_drawn():
    written = deciding(
        [AIMessage("Compare:\n| A | B |")],
        worker_results=["[analyst] plain report, nothing drawn"],
    )

    assert "| A | B |" in written["final_answer"]


def test_an_answer_said_twice_is_said_once():
    """The model sometimes emits its whole answer twice: "Fantasy.Fantasy."."""
    written = deciding([AIMessage("Fantasy.Fantasy.")])

    assert written["final_answer"] == "Fantasy."


def test_an_answer_that_only_nearly_repeats_is_left_alone():
    """Deliberately narrow. Anything looser starts correcting the model's
    writing, and a clumsy answer should stay visible rather than be tidied
    into looking right."""
    clumsy = "J.R.R Tolkien.J.R.R. Tolkien."

    assert deciding([AIMessage(clumsy)])["final_answer"] == clumsy


def test_an_ordinary_answer_is_untouched():
    for said in (
        "There are 51 rows.",
        "Yes. No.",
        "The total is 6.6.",
    ):
        assert deciding([AIMessage(said)])["final_answer"] == said


def test_the_delivery_note_never_reaches_the_user():
    from excel_agent.graph.state import DELIVERED

    written = deciding(
        [AIMessage(f"هذه أول خمسة صفوف:\n{DELIVERED}")],
        worker_results=[f"[analyst] هذه أول خمسة صفوف:\n{DELIVERED}"],
    )

    # The note tells the supervisor the table was drawn; a model composing
    # from the report sometimes copies it out.
    assert DELIVERED not in written["final_answer"]
    assert written["final_answer"] == "هذه أول خمسة صفوف:"


def test_finishing_carries_the_answer_and_ends():
    written = deciding(
        [AIMessage("There are 51 rows.")],
        worker_results=["[analyst] 51 rows"],
    )

    assert written["route"] == "end"
    assert written["final_answer"] == "There are 51 rows."


def test_finishing_forgets_the_work_it_was_based_on():
    written = deciding(
        [AIMessage("There are 51 rows.")],
        worker_results=["[analyst] 51 rows"],
    )

    # worker_results belongs to one turn. Kept, the next turn's supervisor
    # would read work it did not do as though it had just happened.
    assert written["worker_results"] == []


# What the supervisor is told


def test_the_prompt_names_the_spreadsheet_in_hand():
    prompt = supervisor_instructions("TEST - Sales Orders", [])

    assert "TEST - Sales Orders" in prompt


def test_the_prompt_says_when_no_spreadsheet_has_been_chosen():
    prompt = supervisor_instructions(None, [])

    assert "None chosen yet" in prompt
    assert "file_manager" in prompt


def test_the_prompt_lists_what_the_workers_have_reported():
    prompt = supervisor_instructions(
        "TEST - Sales Orders",
        ["[analyst] 51 rows", "[row_editor] Deleted row 51"],
    )

    # Without this the supervisor composes its final answer knowing only what
    # the last worker said, and forgets the count it asked for first.
    assert "[analyst] 51 rows" in prompt
    assert "[row_editor] Deleted row 51" in prompt


def test_the_supervisor_is_free_to_answer_without_calling_anything(monkeypatch):
    """REGRESSION: with a response_format the supervisor cannot answer at all.

    ToolStrategy binds the model with tool_choice="any", so every call has to
    be a tool call. Asked for the answer inside a schema, the real model
    returned malformed JSON about half the time; free to write a sentence, it
    does not. A response_format here brings that back.
    """
    from excel_agent.graph import supervisor as supervisor_module
    from excel_agent.graph.state import DELEGATE

    asked: dict = {}

    def capture(**arguments):
        asked.update(arguments)
        return object()

    monkeypatch.setattr(supervisor_module, "create_agent", capture)

    build_supervisor(ScriptedModel(script=[]))

    assert asked.get("response_format") is None
    assert [one.name for one in asked["tools"]] == [DELEGATE]


class Refusing(ScriptedModel):
    """A model that fails the way the provider fails."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError(
            "Failed to parse tool call arguments as JSON "
            "{'failed_generation': '{\"next\": '}"
        )


def test_a_planner_that_falls_over_ends_the_turn_with_a_sentence():
    """REGRESSION: it escaped the graph and reached the user as a traceback.

    Workers already survive their own failures; the planner did not, so a
    malformed tool call from the model killed the whole turn.
    """
    node = supervisor_node(build_supervisor(Refusing(script=[])))

    written = node(ASKED)

    assert written["route"] == "end"
    assert "Something went wrong" in written["final_answer"]

    # And not the provider's own JSON, which carries the model's bad output.
    assert "failed_generation" not in written["final_answer"]


def test_the_planner_retries_before_giving_up(monkeypatch):
    from excel_agent.graph import supervisor as supervisor_module

    asked: dict = {}

    def capture(**arguments):
        asked.update(arguments)
        return object()

    monkeypatch.setattr(supervisor_module, "create_agent", capture)

    build_supervisor(ScriptedModel(script=[]))

    kinds = [type(one).__name__ for one in asked["middleware"]]
    assert "ModelRetryMiddleware" in kinds
