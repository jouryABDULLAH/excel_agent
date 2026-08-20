"""Tests for the turn runner.

The runner is the line between the agent and whatever is talking to the user,
so what is checked here is what crosses it: plain events, in order, holding
strings and dicts and nothing else.
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel, calling

from excel_agent.model import CUT_OFF
from excel_agent.runner import Answer, Approval, Session, Text, ToolCall, rendered
from excel_agent.graph.state import State
from excel_agent.tools import TOOLS


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


def test_a_tools_output_is_never_mistaken_for_the_answer(a_spreadsheet):
    a_spreadsheet()
    session = session_reading(
        [
            calling("update_row", "1", row=2, values={"Units": 99}),
            AIMessage(""),
        ],
        tools=TOOLS,
    )

    answer = [one for one in session.ask("set row 2 units to 99") if isinstance(one, Answer)]

    # A tool's result is a message carrying content, so taking the last of
    # those handed back "Updated row 2: Units = 99." as though the model had
    # said it. Silence is silence, and whoever draws it decides what to show.
    assert answer == [Answer("")]


def test_a_tool_call_arrives_before_the_answer(a_spreadsheet):
    a_spreadsheet()
    session = session_reading(
        [
            calling("update_row", "1", row=2, values={"Units": 99}),
            AIMessage("Set row 2 to 99."),
        ],
        tools=TOOLS,
    )

    events = list(session.ask("set row 2 units to 99"))

    assert events[0] == ToolCall("update_row", {"row": 2, "values": {"Units": 99}})
    assert events[-1] == Answer("Set row 2 to 99.")


def test_the_arguments_come_through_as_data_not_as_a_sentence(a_spreadsheet):
    a_spreadsheet()
    session = session_reading(
        [calling("inspect_sheet", "1", max_rows=3), AIMessage("read it")],
        tools=TOOLS,
    )

    call = next(event for event in session.ask("read it") if isinstance(event, ToolCall))

    # A pre-written string would have to be taken apart again by anything that
    # wanted to send this as JSON, so the arguments stay a dict.
    assert call.arguments == {"max_rows": 3}
    assert rendered(call) == "inspect_sheet(max_rows=3)"


def test_nothing_from_langchain_crosses_the_line(a_spreadsheet):
    a_spreadsheet()
    session = session_reading(
        [calling("inspect_sheet", "1"), AIMessage("read it")],
        tools=TOOLS,
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


def test_a_turn_that_writes_still_writes(a_spreadsheet):
    sent = a_spreadsheet()
    session = session_reading(
        [
            calling("update_row", "1", row=2, values={"Units": 99}),
            AIMessage("done"),
        ],
        tools=TOOLS,
    )

    list(session.ask("set row 2 units to 99"))

    # The events are only half of a turn. What the runner hands back says
    # nothing about whether the write went out, so this looks at what did.
    assert sent


def test_a_row_added_in_one_turn_can_be_removed_in_the_next(a_spreadsheet):
    sent = a_spreadsheet()
    session = session_reading(
        [
            calling(
                "append_row", "1",
                values={"Product": "Monitor Arm", "Region": "US"},
            ),
            AIMessage("Added it as row 7."),
            # Row 6, not the 7 just added: the sheet these tools read is built
            # by hand and does not grow, so 7 is not there to be removed.
            calling("delete_row", "2", row=6),
            AIMessage("Removed it again."),
        ],
        tools=TOOLS,
    )

    list(session.ask("add a row for a Monitor Arm in the US"))
    assert any("Monitor Arm" in str(write) for write in sent)

    # The second turn only makes sense against what the first one left behind,
    # which is the conversation the session is holding.
    list(session.ask("actually, remove that row again"))
    assert any(write["call"] == "delete_rows" for write in sent)

    said = session.agent.get_state(
        {"configurable": {"thread_id": session.thread_id}}
    ).values["messages"]
    assert len(said) == 8
    assert sum(1 for message in said if getattr(message, "tool_calls", None)) == 2

# Two conversations in one process


def two_sessions():
    """Two sessions on one agent, the way Streamlit serves two browser tabs."""
    agent = create_agent(
        ScriptedModel(script=[AIMessage("a"), AIMessage("b")]),
        [],
        system_prompt="terse",
        state_schema=State,
        checkpointer=InMemorySaver(),
    )

    return Session(agent), Session(agent)


def test_two_conversations_work_on_two_spreadsheets():
    """REGRESSION: they shared one, through a module global.

    Streamlit serves every browser session from one process. The chosen
    spreadsheet lived in config.SPREADSHEET, so whichever tab picked last
    picked for both, and a write meant for one file landed in the other.
    """
    first, second = two_sessions()

    first.use("TEST - Sales Orders")
    second.use("TEST - Raw Contacts")

    assert first.in_use() == "TEST - Sales Orders"
    assert second.in_use() == "TEST - Raw Contacts"

    # And changing one does not reach the other.
    first.use("TEST - Book Collection")

    assert second.in_use() == "TEST - Raw Contacts"


def test_a_conversation_starts_on_nothing_unless_the_environment_names_one():
    first, _ = two_sessions()

    assert first.in_use() is None


def test_a_new_conversation_does_not_inherit_the_last_ones_spreadsheet():
    first, _ = two_sessions()
    first.use("TEST - Sales Orders")

    first.reset()

    assert first.in_use() is None


# Cut off, which is not the same as having nothing to say


def turn_ending_with(message):
    """One turn whose last model message is this one."""
    agent = create_agent(
        ScriptedModel(script=[message]),
        [],
        system_prompt="terse",
        state_schema=State,
        checkpointer=InMemorySaver(),
    )

    return [one for one in Session(agent).ask("go") if isinstance(one, Answer)]


def test_a_reply_cut_off_by_its_length_limit_says_so():
    """It used to be indistinguishable from the model saying nothing.

    Both arrive as an AIMessage with empty content, so the page told the user
    the turn "finished without a written response" and sent them to go and
    check the spreadsheet, when the work may well have been done and only the
    writing up was lost.
    """
    answers = turn_ending_with(
        AIMessage(content="", response_metadata={"finish_reason": "length"})
    )

    assert answers == [Answer(CUT_OFF)]


def test_saying_nothing_is_still_saying_nothing():
    answers = turn_ending_with(
        AIMessage(content="", response_metadata={"finish_reason": "stop"})
    )

    assert answers == [Answer("")]


def test_an_answer_that_arrived_is_not_overridden_by_the_length_limit():
    answers = turn_ending_with(
        AIMessage(content="Here it is.", response_metadata={"finish_reason": "length"})
    )

    assert answers == [Answer("Here it is.")]


# The contract a paused turn will use, frozen before anything pauses


def test_resume_carries_on_without_breaking_a_turn():
    """Nothing interrupts yet, so this only has to be harmless.

    Approval and Session.resume are part of the contract now so that a paused
    turn has a shape the front ends already understand. Wiring it later then
    costs one middleware line rather than reopening runner, ui and cli after
    they have been declared stable.
    """
    agent = create_agent(
        ScriptedModel(script=[AIMessage("first"), AIMessage("second")]),
        [],
        system_prompt="terse",
        state_schema=State,
        checkpointer=InMemorySaver(),
    )
    session = Session(agent)

    assert [one for one in session.ask("hello") if isinstance(one, Answer)] == [
        Answer("first")
    ]

    # A thread that was not waiting for anything carries on and says nothing.
    carried_on = [one for one in session.resume() if isinstance(one, Answer)]

    assert carried_on == [Answer("")]


def test_an_approval_names_the_tool_it_is_waiting_on():
    waiting = Approval(tool="delete_row", arguments={"row": 5}, id="a-call")

    assert (waiting.tool, waiting.arguments["row"]) == ("delete_row", 5)


def test_every_turn_starts_with_last_turn_forgotten():
    """Every finish and error path clears these, but a turn dying outside
    them leaves the next turn out of steps and able to answer from work it
    never did."""
    asked: list[dict] = []

    class Recording:
        """An agent that only remembers what it was handed."""

        def stream(self, payload, config=None, **settings):
            asked.append(payload)
            return iter(())

        def get_state(self, where):
            class Snapshot:
                values: dict = {}

            return Snapshot()

        def update_state(self, where, values):
            pass

    list(Session(Recording()).ask("how many rows?"))

    assert asked[0]["delegations"] == 0
    assert asked[0]["worker_results"] == []
    assert asked[0]["drawn_tables"] == []
    # Feedback left by a validator whose rewrite never landed would make the
    # next question a rewrite-only turn that cannot delegate.
    assert asked[0]["correction"] is None


def test_an_answer_the_stream_lost_is_recovered_from_state():
    """REGRESSION: a turn reached the user empty while its trace showed a
    written answer sitting in state. The checkpoint is the source of truth;
    when the stream hands over nothing, the runner reads it directly."""

    class Quiet:
        """An agent whose stream says nothing, though its state answered."""

        def stream(self, payload, config=None, **settings):
            return iter(())

        def get_state(self, where):
            class Snapshot:
                values = {"final_answer": "هذه أول خمسة صفوف."}

            return Snapshot()

        def update_state(self, where, values):
            pass

    answers = [
        one for one in Session(Quiet()).ask("اظهر اول خمسة صفوف")
        if isinstance(one, Answer)
    ]

    assert [one.text for one in answers] == ["هذه أول خمسة صفوف."]
