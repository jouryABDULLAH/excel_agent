"""Tests for the planner node.

The model is a script here, so nothing below tests whether the supervisor
routes sensibly. What is tested is that its two decisions become the right
state, and that the prompt it reads is built from the state it was handed.
"""

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
        [calling("Delegate", "1", next="analyst", task="count the rows")]
    )

    assert written["route"] == "analyst"
    assert written["task"] == "count the rows"


def test_delegating_clears_any_answer_left_from_before():
    written = deciding(
        [calling("Delegate", "1", next="analyst", task="count the rows")],
        final_answer="an answer from the last turn",
    )

    # Left alone, the runner would show last turn's answer for this one.
    assert written["final_answer"] is None


def test_finishing_carries_the_answer_and_ends():
    written = deciding(
        [calling("Finish", "1", final_answer="There are 51 rows.")],
        worker_results=["[analyst] 51 rows"],
    )

    assert written["route"] == "end"
    assert written["final_answer"] == "There are 51 rows."


def test_finishing_forgets_the_work_it_was_based_on():
    written = deciding(
        [calling("Finish", "1", final_answer="There are 51 rows.")],
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
