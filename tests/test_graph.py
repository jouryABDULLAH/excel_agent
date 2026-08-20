"""Tests for the shape of a turn.

The model is a script, so what is checked is where control goes and what the
state looks like when it stops, not whether the routing was sensible.
"""

import fake_sheets
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tracers.base import BaseTracer
from scripted import ScriptedModel, calling

from excel_agent.graph.graph import build_graph, route_worker
from excel_agent.runner import Answer, Approval, Artifact, Session, ToolCall
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import inspect as inspect_tool


@pytest.fixture
def a_sheet(monkeypatch):
    """A sheet the reading tools can be pointed at, without Google."""
    monkeypatch.setattr(
        inspect_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or "TEST - Sales Orders"),
    )
    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {"title": "Sales Orders", "sheetId": 0},
    )
    monkeypatch.setattr(
        spreadsheet_service, "read_sheet", lambda id, name: fake_sheets.orders()
    )
    monkeypatch.setattr(
        spreadsheet_service, "list_charts", lambda id, name=None: []
    )


def a_turn(script, **state) -> dict:
    """Put one question through the graph."""
    graph = build_graph(ScriptedModel(script=script))

    return graph.invoke(
        {
            "messages": [{"role": "user", "content": "how many rows?"}],
            "spreadsheet_name": "TEST - Sales Orders",
            **state,
        },
        config={"configurable": {"thread_id": "one"}},
    )


DELEGATES_THEN_ANSWERS = [
    calling("delegate", "1", next="analyst", task="count the rows"),
    calling("inspect_sheet", "2", max_rows=5),
    AIMessage("There are 5 rows."),
    AIMessage("The sheet has 5 rows."),
]


def test_a_turn_goes_supervisor_worker_supervisor_and_stops(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    assert ended["final_answer"] == "The sheet has 5 rows."
    assert ended["route"] == "end"


def test_the_work_is_forgotten_once_the_answer_is_written(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    # The analyst did report back, and the supervisor cleared it on finishing.
    assert ended["worker_results"] == []


def test_a_supervisor_that_answers_at_once_never_reaches_a_worker(a_sheet):
    ended = a_turn(
        [AIMessage("Nothing to look up.")]
    )

    assert ended["final_answer"] == "Nothing to look up."
    assert ended["worker_results"] == []


def test_the_spreadsheet_in_hand_survives_the_turn(a_sheet):
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    # Workers write only worker_results, so what the file manager settled has
    # to still be there afterwards.
    assert ended["spreadsheet_name"] == "TEST - Sales Orders"


def test_the_thread_records_the_delegation_and_its_answer(a_sheet):
    """REGRESSION: the supervisor could not see it had already delegated.

    Its second visit was handed only the user's question, so it handed out
    the same task again -- against a real model, up to six times for a read
    and 19 duplicate rows for a write. The delegation and the report now
    live in the thread as an ordinary tool-call pair.
    """
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    shapes = [
        (
            type(one).__name__,
            [call["name"] for call in getattr(one, "tool_calls", None) or []],
        )
        for one in ended["messages"]
    ]

    assert shapes == [
        ("HumanMessage", []),
        ("AIMessage", ["delegate"]),
        ("ToolMessage", []),
        ("AIMessage", []),
    ]


def test_every_delegation_in_the_thread_is_answered(a_sheet):
    # An assistant tool call with no tool result after it makes the whole
    # thread invalid to the provider, and it poisons every later turn.
    ended = a_turn(DELEGATES_THEN_ANSWERS)

    unanswered: set[str] = set()

    for message in ended["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            unanswered.add(call["id"])

        if type(message).__name__ == "ToolMessage":
            unanswered.discard(message.tool_call_id)

    assert unanswered == set()


def test_a_supervisor_that_will_not_stop_is_stopped(a_sheet):
    """REGRESSION: nothing bounded the loop but the recursion limit.

    A supervisor stuck re-delegating burned every step and the user got
    "I ran out of steps" -- after the worker had already written its change
    to the sheet once per pass.
    """
    from excel_agent.graph.supervisor import MAX_DELEGATIONS

    forever = [
        one
        for _ in range(MAX_DELEGATIONS)
        for one in (
            calling("delegate", "1", next="analyst", task="count the rows"),
            calling("inspect_sheet", "2", max_rows=5),
            AIMessage("There are 5 rows."),
        )
    ] + [calling("delegate", "1", next="analyst", task="count the rows")]

    ended = a_turn(forever)

    # The turn ended with an answer, not GraphRecursionError, and the
    # counter is ready for the next turn.
    assert ended["route"] == "end"
    assert ended["final_answer"]
    assert ended["delegations"] == 0

    # The worker really was capped: one report per allowed delegation.
    reports = [
        one
        for one in ended["messages"]
        if type(one).__name__ == "ToolMessage"
    ]
    assert len(reports) == MAX_DELEGATIONS


def test_delegating_is_never_shown_to_the_user_as_work(a_sheet):
    """The delegate call is how the supervisor speaks, not work on a sheet.

    Only runner.DECISION_NAMES keeps it out of the action list, and it holds
    the tool's name as a string: rename one without the other and the user is
    told about a delegation on every turn.
    """
    session = Session(build_graph(ScriptedModel(script=DELEGATES_THEN_ANSWERS)))
    session.use("TEST - Sales Orders")

    work = [
        one.name
        for one in session.ask("how many rows?")
        if isinstance(one, ToolCall)
    ]

    assert work == ["inspect_sheet"]


def a_read(render_data: bool) -> list:
    """One turn whose analyst reads the sheet, asked to draw it or not."""
    session = Session(
        build_graph(
            ScriptedModel(
                script=[
                    calling("delegate", "1", next="analyst", task="show 5 rows"),
                    calling("inspect_sheet", "a", max_rows=5, render_data=render_data),
                    AIMessage("Here are the rows."),
                    AIMessage("Here are the first 5 rows."),
                ]
            )
        )
    )
    session.use("TEST - Sales Orders")

    return [
        one for one in session.ask("show me 5 rows") if isinstance(one, Artifact)
    ]


def test_rows_the_user_asked_to_see_are_drawn(a_sheet):
    """REGRESSION: nothing was drawn at all on this path.

    The runner used to unwrap artifacts from the old delegate tool's own
    artifact. A worker's tool reports its artifact directly, so every table
    was silently dropped -- and no test noticed, because they all drove the
    orchestrator.
    """
    drawn = a_read(render_data=True)

    assert [one.data["operation"] for one in drawn] == ["inspect_sheet"]
    assert len(drawn[0].data["rows"]) == 5


def test_a_read_taken_only_to_answer_is_not_drawn(a_sheet):
    # Counting rows to answer "how many?" should not dump the table underneath.
    assert a_read(render_data=False) == []


def test_a_tool_call_says_which_specialist_made_it(a_sheet):
    """The stream namespaces every event by the node that produced it.

    The UI shows progress from this. Delegating stopped being a tool call, so
    without it every action would read "Working on it...".
    """
    session = Session(build_graph(ScriptedModel(script=DELEGATES_THEN_ANSWERS)))
    session.use("TEST - Sales Orders")

    made = [
        (one.name, one.worker)
        for one in session.ask("how many rows?")
        if isinstance(one, ToolCall)
    ]

    assert made == [("inspect_sheet", "analyst")]


def test_a_turn_with_no_route_is_a_broken_supervisor_not_a_finished_turn():
    with pytest.raises(ValueError, match="no route"):
        route_worker({"route": None})

# Who exists, and what they can reach


def test_the_route_names_cover_the_workers_and_the_way_out():
    from excel_agent.graph.state import WORKERS, RouteName
    from typing import get_args

    # A union of two Literals, so the names sit one level down.
    names = {
        one for part in get_args(RouteName) for one in get_args(part)
    }

    # route is what route_worker looks up in the graph's edge map, so the
    # type has to name every worker plus "end" and nothing else.
    assert names == {*WORKERS, "end"}


def test_every_worker_the_supervisor_can_name_has_a_node():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.graph.state import WORKERS

    # Delegate.next is Literal[WORKERS], so a name here without a node is a
    # KeyError the moment the supervisor picks it.
    assert {one.NAME for one in SPECIALISTS} == set(WORKERS)


def test_every_tool_reaches_some_worker():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.tools import TOOLS

    held = {tool.name for one in SPECIALISTS for tool in one.TOOLS}
    offered = {tool.name for tool in TOOLS}

    # use_spreadsheet is wired to nobody: the file manager settles a choice
    # through resolve_spreadsheet_choice instead.
    assert offered - held == {"use_spreadsheet"}


def test_no_worker_holds_a_tool_that_is_not_offered():
    from excel_agent.agents import SPECIALISTS
    from excel_agent.tools import TOOLS

    held = {tool.name for one in SPECIALISTS for tool in one.TOOLS}

    assert held <= {tool.name for tool in TOOLS}


def test_everyone_who_writes_can_read_first():
    from excel_agent.agents import SPECIALISTS

    writes = {
        "update_row", "insert_row", "append_row", "delete_row", "move_row",
        "insert_column", "delete_column", "move_column", "set_column_formula",
        "create_chart", "delete_chart",
    }

    for one in SPECIALISTS:
        names = {tool.name for tool in one.TOOLS}

        if names & writes:
            # A row number from another agent is stale before it arrives, so
            # whoever writes has to be able to look first.
            assert "inspect_sheet" in names, one.NAME


# Tracing


class Recorder(BaseTracer):
    """Records every run and who its parent was."""

    def __init__(self):
        super().__init__()
        self.seen: list[tuple[str, str | None, str]] = []

    def _persist_run(self, run):
        pass

    def _on_run_create(self, run):
        self.seen.append(
            (
                str(run.id),
                str(run.parent_run_id) if run.parent_run_id else None,
                run.name,
            )
        )

    def ancestors(self, name: str) -> list[str]:
        """The names above the first run called `name`, nearest first."""
        by_id = {one[0]: one for one in self.seen}
        found = next(one for one in self.seen if one[2] == name)

        names, parent = [], found[1]

        while parent and parent in by_id:
            names.append(by_id[parent][2])
            parent = by_id[parent][1]

        return names


def traced(script) -> Recorder:
    """One turn through the graph, with its run tree recorded."""
    watching = Recorder()

    build_graph(ScriptedModel(script=script)).invoke(
        {
            "messages": [{"role": "user", "content": "how many rows?"}],
            "spreadsheet_name": "TEST - Sales Orders",
        },
        config={
            "configurable": {"thread_id": "traced"},
            "callbacks": [watching],
        },
    )

    return watching


def test_a_turn_is_one_run_not_a_pile_of_siblings(a_sheet):
    """A turn has to reach the trace as one tree.

    Workers and the planner are separate agents invoked from inside a node.
    Handed no parent context, each would start its own run tree and every
    model call, middleware step and tool would arrive as a top-level sibling
    with nothing showing who did what. Today the node's config is passed
    down; these hold whether that stays true.
    """
    watching = traced(DELEGATES_THEN_ANSWERS)

    assert len([one for one in watching.seen if one[1] is None]) == 1


def test_a_workers_tools_are_recorded_inside_that_worker(a_sheet):
    watching = traced(DELEGATES_THEN_ANSWERS)

    # The tool the analyst called has the analyst node above it.
    assert "analyst" in watching.ancestors("inspect_sheet")


def test_the_planners_middleware_is_recorded_inside_the_planner(a_sheet):
    watching = traced(DELEGATES_THEN_ANSWERS)

    assert "supervisor" in watching.ancestors("stop_at_delegation.after_model")


# Pausing for approval


@pytest.fixture
def a_deletable_row(a_sheet, monkeypatch):
    """A row the editor can delete, and a record of whether it did."""
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or "TEST - Sales Orders"),
    )

    deleted: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "delete_rows",
        lambda **sent: deleted.append(sent) or {},
    )

    return deleted


DELETES_A_ROW = [
    calling("delegate", "1", next="row_editor", task="delete row 3"),
    calling("delete_row", "2", row=3),
    AIMessage("Row 3 is gone."),
    AIMessage("Deleted row 3."),
]


def asking_to_delete(script):
    """A session that has asked for a row to be deleted."""
    session = Session(build_graph(ScriptedModel(script=list(script))))
    session.use("TEST - Sales Orders")

    return session, list(session.ask("delete row 3"))


def test_a_deletion_waits_to_be_allowed(a_deletable_row):
    """There is no undo, so a delete is shown before it happens."""
    _, events = asking_to_delete(DELETES_A_ROW)

    waiting = [one for one in events if isinstance(one, Approval)]

    assert [(one.tool, one.arguments) for one in waiting] == [
        ("delete_row", {"row": 3})
    ]
    # Asked once, not once per namespace the pause is streamed under.
    assert len(waiting) == 1

    # Nothing is written, and the turn does not pretend to be finished.
    assert a_deletable_row == []
    assert [one for one in events if isinstance(one, Answer)] == []


def test_allowing_a_deletion_carries_the_turn_on(a_deletable_row):
    session, _ = asking_to_delete(DELETES_A_ROW)

    events = list(session.resume({"decisions": [{"type": "approve"}]}))

    assert [one["start_row"] for one in a_deletable_row] == [3]
    assert [one.text for one in events if isinstance(one, Answer)] == [
        "Deleted row 3."
    ]


def test_refusing_a_deletion_leaves_the_row_alone(a_deletable_row):
    session, _ = asking_to_delete(DELETES_A_ROW)

    events = list(session.resume({"decisions": [{"type": "reject"}]}))

    # The turn still ends with something said, rather than dying unanswered.
    assert a_deletable_row == []
    assert [one for one in events if isinstance(one, Answer)]


# What a worker is allowed to write


def _specialists():
    from excel_agent.agents import SPECIALISTS

    return SPECIALISTS




# Shared state a worker may add to. Everything else on State belongs to the
# supervisor: a worker writing route, task or final_answer would be deciding
# what happens next or answering the user, which is not its job.
WORKER_WRITES = {"worker_results", "messages", "drawn_tables"}

# The one exception, and the reason it exists: choosing the file is what the
# file manager is for, so it alone may say which one is in hand.
CHOOSING = WORKER_WRITES | {"spreadsheet_id", "spreadsheet_name"}


def running(specialist, script):
    """Run one specialist node and give back what it wrote to shared state."""
    node = specialist.build(ScriptedModel(script=list(script)))

    return node(
        {
            "messages": [
                {"role": "user", "content": "how many rows?"},
                calling("delegate", "1", next=specialist.NAME, task="look"),
            ],
            "task": "look",
            "spreadsheet_name": "TEST - Sales Orders",
            "spreadsheet_id": "an-id",
        }
    )


@pytest.mark.parametrize(
    "specialist",
    [pytest.param(one, id=one.NAME) for one in _specialists()],
)
def test_a_worker_writes_only_what_it_is_allowed_to(specialist, a_sheet):
    written = set(running(specialist, [AIMessage("Nothing to report.")]))

    allowed = (
        CHOOSING if specialist.NAME == "file_manager" else WORKER_WRITES
    )

    assert written <= allowed, (
        f"{specialist.NAME} wrote {sorted(written - allowed)}"
    )


@pytest.mark.parametrize(
    "specialist",
    [pytest.param(one, id=one.NAME) for one in _specialists()],
)
def test_a_worker_never_decides_what_happens_next(specialist, a_sheet):
    """Routing and the answer belong to the supervisor. A worker writing
    either would end the turn or send it somewhere from inside a node."""
    written = running(specialist, [AIMessage("Nothing to report.")])

    assert not {"route", "task", "final_answer"} & set(written)


def test_every_change_in_one_pause_gets_its_own_decision(a_deletable_row):
    """REGRESSION: one decision was sent however many changes were waiting,
    so asking to change several rows answered only the first and the rest
    were quietly dropped."""
    from excel_agent.ui import _decisions

    asking = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "delete_row",
                "args": {"row": 3},
                "id": "a",
                "type": "tool_call",
            },
            {
                "name": "delete_row",
                "args": {"row": 4},
                "id": "b",
                "type": "tool_call",
            },
        ],
    )

    session, events = asking_to_delete(
        [
            calling("delegate", "1", next="row_editor", task="delete two rows"),
            asking,
            AIMessage("Both are gone."),
            AIMessage("Deleted rows 3 and 4."),
        ]
    )

    waiting = [
        {"tool": one.tool, "arguments": one.arguments, "id": one.id}
        for one in events
        if isinstance(one, Approval)
    ]

    assert len(waiting) == 2

    list(
        session.resume(
            _decisions(waiting, {"type": "approve"})
        )
    )

    assert [one["start_row"] for one in a_deletable_row] == [3, 4]
