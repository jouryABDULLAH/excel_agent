"""Tests for the orchestrator and its subagents.

The model is a script here, so nothing below tests whether an orchestrator
routes sensibly: that is what a run by hand is for. What is tested is the
wiring, which is what would make such a run meaningless if it were wrong.
"""

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from scripted import ScriptedModel, calling

from excel_agent import config
from excel_agent.prompts import CANNOT_DO
from excel_agent.runner import Answer, Session, ToolCall
from excel_agent.subagents import SUBAGENTS
from excel_agent.subagents import factory
from excel_agent.subagents.factory import OrchestratorState, as_tool
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.tools import TOOLS

# Choosing which spreadsheet to work on is the orchestrator's own question, so
# no subagent holds any of these.
ORCHESTRATOR_ONLY = {"list_workbooks", "find_spreadsheet", "use_spreadsheet"}


# Who holds what


def test_every_tool_reaches_some_subagent():
    covered = {tool.name for spec in SUBAGENTS for tool in spec.tools}
    everything = {tool.name for tool in TOOLS} - ORCHESTRATOR_ONLY

    # A tool added to TOOLS and forgotten here can never be called at all: the
    # orchestrator does not hold it, and neither does anyone it can delegate to.
    assert everything <= covered


def test_no_subagent_holds_a_tool_that_is_not_offered():
    covered = {tool.name for spec in SUBAGENTS for tool in spec.tools}

    assert covered <= {tool.name for tool in TOOLS}


def test_reading_comes_with_every_subagent_that_writes():
    for spec in SUBAGENTS:
        names = {tool.name for tool in spec.tools}
        if names & {"update_row", "insert_row", "append_row", "delete_row",
                    "move_row", "modify_column", "modify_chart"}:
            # A row number handed from one agent to another is stale before it
            # arrives, so whoever writes has to be able to look first.
            assert "inspect_sheet" in names, spec.name


def test_each_subagent_is_described_and_told_what_it_cannot_do():
    for spec in SUBAGENTS:
        assert spec.description.strip()
        assert isinstance(spec.tools, tuple)
        # The refusals are shared, so a subagent cannot claim it can do
        # something the orchestrator would refuse.
        assert CANNOT_DO in spec.system_prompt


def test_the_orchestrator_is_told_the_same_refusals():
    assert CANNOT_DO in ORCHESTRATOR_PROMPT


def test_the_names_are_the_ones_the_orchestrator_will_call():
    assert [spec.name for spec in SUBAGENTS] == [
        "file_manager",
        "analyst",
        "row_editor",
        "structure_editor",
        "chart_maker",
    ]


# Wrapping one up as a tool


def test_a_subagent_becomes_a_tool_taking_an_instruction():
    analyst = next(spec for spec in SUBAGENTS if spec.name == "analyst")

    wrapped = as_tool(analyst, ScriptedModel(script=[AIMessage("read it")]))

    assert wrapped.name == "analyst"
    # render_data as well, because the orchestrator decides whether the rows
    # a read brings back are meant to be drawn or only reasoned about.
    assert list(wrapped.args) == ["instruction", "render_data"]
    assert wrapped.description == analyst.description


def test_the_file_manager_is_wrapped_so_it_can_change_the_chosen_spreadsheet():
    """It is the one subagent whose result updates the orchestrator's state.

    Settling which spreadsheet is in hand is not something a tool can do by
    answering, so this one is wrapped to return a Command instead.
    """
    file_manager = next(spec for spec in SUBAGENTS if spec.name == "file_manager")

    wrapped = as_tool(file_manager, ScriptedModel(script=[AIMessage("found it")]))

    assert wrapped.name == "file_manager"
    assert list(wrapped.args) == ["instruction"]


class RecordingModel(ScriptedModel):
    """A scripted model that keeps what it was asked.

    A subagent is invoked inside the tool that wraps it, so the instruction it
    was handed is not visible from outside. This is how a test reads it.
    """

    seen: list[str] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(str(messages[-1].content))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def delegating_to(spec, model, state=None):
    """An orchestrator holding one subagent, and the state it starts with.

    The wrapped subagent asks for a ToolRuntime, which only whoever calls the
    tool can supply, so a test drives it the way it really runs: through an
    agent, which injects the runtime and hides it from the model.
    """
    orchestrator = create_agent(
        ScriptedModel(
            script=[
                calling(spec.name, "1", instruction="do the thing"),
                AIMessage("Done."),
            ]
        ),
        [as_tool(spec, model)],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=OrchestratorState,
        checkpointer=InMemorySaver(),
    )
    return orchestrator.invoke(
        {"messages": [{"role": "user", "content": "do the thing"}], **(state or {})},
        config={"configurable": {"thread_id": "one"}},
    )


def test_a_subagent_does_the_work_and_hands_back_what_it_said(a_spreadsheet):
    sent = a_spreadsheet()
    row_editor = next(spec for spec in SUBAGENTS if spec.name == "row_editor")

    said = delegating_to(
        row_editor,
        ScriptedModel(
            script=[
                calling("update_row", "1", row=2, values={"Units": 99}),
                AIMessage("Set row 2 to 99."),
            ]
        ),
    )

    # The write went out, and what the subagent said came back to the
    # orchestrator as the result of the tool call that handed it the work.
    #
    # update_row reaches Google through spreadsheet_service rather than through
    # a name imported from sheets.py, so this is also what proves the fixture
    # covers that second path: without it there is no write to see here.
    assert sent
    assert sent[0]["call"] == "update_cells"
    assert any(
        isinstance(message, ToolMessage) and "Set row 2 to 99." in str(message.content)
        for message in said["messages"]
    )


# What the subagent is told about the spreadsheet


def test_a_subagent_is_told_which_spreadsheet_is_in_hand(a_spreadsheet):
    a_spreadsheet()
    analyst = next(spec for spec in SUBAGENTS if spec.name == "analyst")
    inside = RecordingModel(script=[AIMessage("read it")], seen=[])

    delegating_to(
        analyst,
        inside,
        state={"spreadsheet_id": "abc123", "spreadsheet_name": "TEST - Sales Orders"},
    )

    # A subagent holds no tool for choosing a spreadsheet, so what it is
    # working on has to arrive with the instruction.
    assert "TEST - Sales Orders" in inside.seen[0]
    assert "do the thing" in inside.seen[0]

    # The id is deliberately withheld. No tool takes one: every spreadsheet
    # argument is resolved through Drive by title, so a subagent that passed
    # the id would be told there is no spreadsheet called abc123.
    assert "abc123" not in inside.seen[0]


def test_a_subagent_is_told_when_nothing_has_been_chosen(a_spreadsheet):
    a_spreadsheet()
    analyst = next(spec for spec in SUBAGENTS if spec.name == "analyst")
    inside = RecordingModel(script=[AIMessage("read it")], seen=[])

    delegating_to(analyst, inside)

    # Nothing writes these two yet: use_spreadsheet settles config.SPREADSHEET
    # and returns a sentence, so the state stays empty and every instruction
    # says so. The tools still fall back to the file being worked on.
    assert "Not selected" in inside.seen[0]


# The orchestrator through the runner


def test_the_orchestrator_answers_through_the_same_events(a_spreadsheet):
    a_spreadsheet()
    analyst = next(spec for spec in SUBAGENTS if spec.name == "analyst")
    # A script each, so the test does not depend on the order the two of them
    # happen to reach for the model.
    inside = ScriptedModel(
        script=[
            calling("inspect_sheet", "1", max_rows=5),
            AIMessage("Five rows, ending at row 6."),
        ]
    )
    outside = ScriptedModel(
        script=[
            calling("analyst", "1", instruction="how many rows?"),
            AIMessage("There are five rows."),
        ]
    )
    orchestrator = create_agent(
        outside,
        [as_tool(analyst, inside)],
        system_prompt=ORCHESTRATOR_PROMPT,
        state_schema=OrchestratorState,
        checkpointer=InMemorySaver(),
    )

    events = list(Session(orchestrator).ask("how many rows?"))

    # Delegating changes nothing about what crosses the runner: a tool call
    # naming a subagent, then the answer, the same as any other turn.
    assert events[0] == ToolCall("analyst", {"instruction": "how many rows?"})
    assert events[-1] == Answer("There are five rows.")


# What the planner itself is told


class RecordingPlanner(ScriptedModel):
    """A scripted model that keeps the system prompt it was handed."""

    prompts: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(str(messages[0].content))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def planning_over(questions, planner, tools):
    """Run several turns on one thread, the way a conversation runs."""
    orchestrator = create_agent(
        model=planner,
        tools=tools,
        system_prompt=ORCHESTRATOR_PROMPT,
        middleware=[factory._planner_prompt],
        state_schema=OrchestratorState,
        checkpointer=InMemorySaver(),
    )

    for question in questions:
        orchestrator.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config={"configurable": {"thread_id": "one"}},
        )

    return orchestrator


def test_the_planner_is_told_which_spreadsheet_is_in_hand_on_every_turn(a_spreadsheet):
    """It was told on none of them, which is what it forgot.

    A subagent is handed the spreadsheet in its instruction. The planner was
    handed nothing, so a few turns after the file manager settled a file, the
    name had scrolled far enough back that it asked the user for a file it had
    already been given. The state held it the whole time; nothing put it in
    front of the planner.
    """
    a_spreadsheet()
    file_manager = next(spec for spec in SUBAGENTS if spec.name == "file_manager")
    analyst = next(spec for spec in SUBAGENTS if spec.name == "analyst")

    planner = RecordingPlanner(
        script=[
            calling("file_manager", "1", instruction="use the sales orders file"),
            AIMessage("Working on it."),
            calling("analyst", "2", instruction="show rows"),
            AIMessage("Here."),
            calling("analyst", "3", instruction="show them again"),
            AIMessage("Here again."),
        ],
        prompts=[],
    )

    planning_over(
        ["use the sales orders file", "show rows", "show them again"],
        planner,
        [
            as_tool(
                file_manager,
                ScriptedModel(
                    script=[
                        calling(
                            "resolve_spreadsheet_choice",
                            "i1",
                            spreadsheet="TEST - Sales Orders",
                        ),
                        AIMessage("Selected it."),
                    ]
                ),
            ),
            as_tool(
                analyst,
                ScriptedModel(script=[AIMessage("read it"), AIMessage("again")]),
            ),
        ],
    )

    # Nothing was chosen when the first call was made, and every call after
    # the file manager settled one names it, three turns included.
    assert "None has been chosen yet" in planner.prompts[0]
    assert all("TEST - Sales Orders" in prompt for prompt in planner.prompts[1:])


def test_the_planner_is_told_about_a_spreadsheet_the_sidebar_chose(monkeypatch):
    """The page writes config.SPREADSHEET and never touches the state.

    Told only what the state holds, the planner would name no spreadsheet
    while every tool wrote to the one the user picked from the sidebar.
    """
    monkeypatch.setattr(config, "SPREADSHEET", "TEST - Sales Orders")

    assert factory.current_spreadsheet({}) == "TEST - Sales Orders"
    # What the file manager settled wins: it is the more recent of the two,
    # and the one the subagents were told about.
    assert factory.current_spreadsheet({"spreadsheet_name": "Another"}) == "Another"


def test_a_spreadsheet_argument_is_a_name_and_never_an_id(monkeypatch):
    """Which is why the instruction names the spreadsheet and withholds its id.

    Drive is searched by title, so an id passed as the spreadsheet argument
    finds nothing. If a tool ever starts taking an id, this fails, and the
    instruction may start handing one over again.
    """
    from excel_agent import sheets

    monkeypatch.setattr(
        sheets._drive,
        "search_spreadsheets",
        lambda name=None: (
            [("the-id", "TEST - Sales Orders")]
            if name in (None, "TEST - Sales Orders")
            else []
        ),
    )

    assert sheets.resolve_spreadsheet("TEST - Sales Orders") == (
        "the-id",
        "TEST - Sales Orders",
    )

    with pytest.raises(ValueError):
        sheets.resolve_spreadsheet("the-id")
