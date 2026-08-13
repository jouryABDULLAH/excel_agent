"""Tests for the turn runner.

The runner is the line between the agent and whatever is talking to the user,
so what is checked here is what crosses it: plain events, in order, holding
strings and dicts and nothing else.
"""

import make_fixtures
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from openpyxl import load_workbook
from scripted import ScriptedModel, calling

from excel_agent.runner import Answer, Session, Text, ToolCall, rendered
from excel_agent.tools import LOCAL_TOOLS


def session_reading(script, tools=(), **settings) -> Session:
    """A session whose model will say the given things, in order."""
    return Session(
        create_agent(
            ScriptedModel(script=list(script)),
            list(tools),
            system_prompt="terse",
            checkpointer=InMemorySaver(),
        ),
        **settings,
    )


# What comes back


def test_a_plain_turn_gives_back_one_answer():
    session = session_reading([AIMessage("all done")])

    events = list(session.ask("say something"))

    assert events == [Answer("all done")]


def test_a_tools_output_is_never_mistaken_for_the_answer(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    session = session_reading(
        [
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage(""),
        ],
        tools=LOCAL_TOOLS,
    )

    answer = [one for one in session.ask("set row 2 units to 99") if isinstance(one, Answer)]

    # A tool's result is a message carrying content, so taking the last of
    # those handed back "Updated row 2: Units = 99." as though the model had
    # said it. Silence is silence, and whoever draws it decides what to show.
    assert answer == [Answer("")]


def test_a_tool_call_arrives_before_the_answer(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    session = session_reading(
        [
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage("Set row 2 to 99."),
        ],
        tools=LOCAL_TOOLS,
    )

    events = list(session.ask("set row 2 units to 99"))

    assert events[0] == ToolCall("modify_row", {"action": "edit", "row": 2, "values": {"Units": 99}})
    assert events[-1] == Answer("Set row 2 to 99.")


def test_the_arguments_come_through_as_data_not_as_a_sentence(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    session = session_reading(
        [calling("inspect_sheet", "1", max_rows=3), AIMessage("read it")],
        tools=LOCAL_TOOLS,
    )

    call = next(event for event in session.ask("read it") if isinstance(event, ToolCall))

    # A pre-written string would have to be taken apart again by anything that
    # wanted to send this as JSON, so the arguments stay a dict.
    assert call.arguments == {"max_rows": 3}
    assert rendered(call) == "inspect_sheet(max_rows=3)"


def test_nothing_from_langchain_crosses_the_line(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
    session = session_reading(
        [calling("inspect_sheet", "1"), AIMessage("read it")],
        tools=LOCAL_TOOLS,
    )

    for event in session.ask("read the sheet"):
        assert type(event).__module__ == "excel_agent.runner"
        for value in vars(event).values():
            assert type(value).__module__ in ("builtins", "excel_agent.runner")


# The conversation


def test_a_session_remembers_across_turns():
    session = session_reading([AIMessage("first"), AIMessage("second")])

    list(session.ask("my name is Joori"))
    list(session.ask("what is my name?"))

    said = session.agent.get_state(
        {"configurable": {"thread_id": session.thread_id}}
    ).values["messages"]
    assert [message.content for message in said] == [
        "my name is Joori",
        "first",
        "what is my name?",
        "second",
    ]


def test_resetting_starts_a_conversation_with_nothing_in_it():
    session = session_reading([AIMessage("first"), AIMessage("second")])
    list(session.ask("my name is Joori"))
    before = session.thread_id

    session.reset()

    assert session.thread_id != before
    list(session.ask("what is my name?"))
    said = session.agent.get_state(
        {"configurable": {"thread_id": session.thread_id}}
    ).values["messages"]
    assert [message.content for message in said] == ["what is my name?", "second"]


# Streaming the words as they come


def test_the_answer_can_arrive_a_piece_at_a_time():
    session = session_reading([AIMessage("hello there")], stream_text=True)

    events = list(session.ask("say hello"))

    pieces = [event.text for event in events if isinstance(event, Text)]
    assert pieces
    # The pieces spell out the same words the answer holds, which is why a
    # caller shows one or the other rather than both.
    assert "".join(pieces) == "hello there"
    assert events[-1] == Answer("hello there")


def test_the_pieces_are_left_out_unless_they_are_asked_for():
    session = session_reading([AIMessage("hello there")])

    assert not [event for event in session.ask("say hello") if isinstance(event, Text)]


def test_a_turn_that_writes_still_writes(tmp_path, use_workbook):
    path = use_workbook(make_fixtures.clean_table(tmp_path))
    session = session_reading(
        [
            calling("modify_row", "1", action="edit", row=2, values={"Units": 99}),
            AIMessage("done"),
        ],
        tools=LOCAL_TOOLS,
    )

    list(session.ask("set row 2 units to 99"))

    assert load_workbook(path).active["D2"].value == 99