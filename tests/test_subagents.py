"""Tests for the multi agent variant.

The model is a script here, so nothing below tests whether an orchestrator
routes sensibly: that is what the comparison run is for. What is tested is the
wiring, which is what would make a comparison meaningless if it were wrong.
"""

import make_fixtures
import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from openpyxl import load_workbook
from scripted import ScriptedModel, calling

from excel_agent.prompts import CANNOT_DO, SYSTEM_PROMPT
from excel_agent.runner import Answer, Session, ToolCall
from excel_agent.subagents import SUBAGENTS, build
from excel_agent.subagents.factory import as_tool
from excel_agent.subagents.prompts import ORCHESTRATOR_PROMPT
from excel_agent.tools import TOOLS

# list_workbooks is the orchestrator's own, so no subagent holds it.
ORCHESTRATOR_ONLY = {"list_workbooks"}


# Who holds what


def test_every_tool_reaches_some_subagent():
    covered = {tool.name for spec in SUBAGENTS for tool in spec.tools}
    everything = {tool.name for tool in TOOLS} - ORCHESTRATOR_ONLY

    # A tool added to TOOLS and forgotten here would leave the multi agent
    # variant quietly unable to do something the single agent can, and the
    # comparison would read it as a routing failure.
    assert everything <= covered


def test_no_subagent_holds_a_tool_that_is_not_offered():
    covered = {tool.name for spec in SUBAGENTS for tool in spec.tools}

    assert covered <= {tool.name for tool in TOOLS}


def test_reading_comes_with_every_subagent_that_writes():
    for spec in SUBAGENTS:
        names = {tool.name for tool in spec.tools}
        if names & {"modify_sheet", "modify_column", "modify_chart"}:
            # A row number handed from one agent to another is stale before it
            # arrives, so whoever writes has to be able to look first.
            assert "inspect_sheet" in names, spec.name


def test_each_subagent_is_described_and_told_what_it_cannot_do():
    for spec in SUBAGENTS:
        assert spec.description.strip()
        assert isinstance(spec.tools, tuple)
        # The refusals are shared so a subagent cannot claim it can do
        # something the single agent refuses.
        assert CANNOT_DO in spec.system_prompt


def test_the_orchestrator_is_told_the_same_refusals():
    assert CANNOT_DO in ORCHESTRATOR_PROMPT
    assert CANNOT_DO in SYSTEM_PROMPT


def test_the_names_are_the_ones_the_orchestrator_will_call():
    assert [spec.name for spec in SUBAGENTS] == [
        "analyst",
        "row_editor",
        "structure_editor",
        "chart_maker",
    ]


# Wrapping one up as a tool


def test_a_subagent_becomes_a_tool_taking_an_instruction():
    wrapped = as_tool(SUBAGENTS[0], ScriptedModel(script=[AIMessage("read it")]))

    assert wrapped.name == "analyst"
    assert list(wrapped.args) == ["instruction"]
    assert wrapped.description == SUBAGENTS[0].description


def test_a_subagent_does_the_work_and_says_which_tools_it_used(tmp_path, use_workbook):
    path = use_workbook(make_fixtures.clean_table(tmp_path))
    row_editor = next(spec for spec in SUBAGENTS if spec.name == "row_editor")
    wrapped = as_tool(
        row_editor,
        ScriptedModel(
            script=[
                calling("modify_sheet", "1", action="edit", row=2, values={"Units": 99}),
                AIMessage("Set row 2 to 99."),
            ]
        ),
    )

    answer = wrapped.invoke({"instruction": "set row 2 units to 99"})

    assert "Set row 2 to 99." in answer
    # What the tool returned travels with the answer, so the orchestrator has
    # the evidence rather than a summary it would have to take on trust.
    assert "What the tools returned:" in answer
    assert "Updated row 2" in answer
    assert load_workbook(path).active["D2"].value == 99


# Choosing a variant


def test_a_name_that_is_neither_variant_is_refused():
    with pytest.raises(ValueError, match="single, multi"):
        build("both")


# The orchestrator through the runner


def test_the_orchestrator_answers_through_the_same_events(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))
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
        checkpointer=InMemorySaver(),
    )

    events = list(Session(orchestrator).ask("how many rows?"))

    # The events look the same as the single agent's. What changed is that the
    # tool call names a subagent rather than a spreadsheet tool.
    assert events[0] == ToolCall("analyst", {"instruction": "how many rows?"})
    assert events[-1] == Answer("There are five rows.")