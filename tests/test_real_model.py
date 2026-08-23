"""The paths a scripted model cannot reach.

Run deliberately, because these cost money and are not deterministic:

    pytest -m real

Real model, stubbed Google. Every bug these exist for was in how the model is
asked and how its answer is read, not in how Google is called -- and the
no_google guard has to keep standing so no test can write to a real
spreadsheet.

ScriptedModel answers whatever it was scripted to answer, whether or not the
binding allowed it. Only a real model shows what the model actually does when
asked to delegate or to write a sentence.
"""

import re

import fake_sheets
import pytest

from excel_agent.graph.graph import build_graph
from excel_agent.graph.supervisor import build_supervisor, supervisor_node
from excel_agent.model import build_model
from excel_agent.runner import Answer, Session, ToolCall
from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import inspect as inspect_tool

pytestmark = pytest.mark.real


SPREADSHEET = "TEST - Sales Orders"


@pytest.fixture
def a_sheet(monkeypatch):
    """A sheet built by hand, so a real model reads real-shaped data."""
    monkeypatch.setattr(
        inspect_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
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


def asked(**state) -> dict:
    return {
        "messages": [{"role": "user", "content": "how many rows are in the sheet?"}],
        "spreadsheet_name": SPREADSHEET,
        "spreadsheet_id": "an-id",
        "worker_results": [],
        **state,
    }


# The supervisor's two decisions, against the real model


def test_a_question_about_the_sheet_is_delegated_not_answered():
    decided = supervisor_node(build_supervisor(build_model()))(asked())

    # It cannot know the row count without looking, so it must delegate.
    assert decided["route"] == "analyst"
    assert decided["task"].strip()


def test_the_supervisor_finishes_once_the_work_is_reported():
    """REGRESSION: asked for the answer inside a schema, this model emitted

    {"name": "answer", "arguments": There are 5 rows...} -- the wrong tool name
    and unquoted arguments -- and about half of these turns died there. Writing
    the answer as prose is what fixed it, so this is the path that broke.
    """
    decided = supervisor_node(build_supervisor(build_model()))(
        asked(worker_results=["[analyst] The sheet has 5 rows of data."])
    )

    assert decided["route"] == "end"
    assert decided["final_answer"].strip()


# A whole turn


def test_a_read_question_goes_through_the_graph_and_comes_back_answered(a_sheet):
    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    events = list(session.ask("How many rows of data are in the sheet?"))

    answer = next(one.text for one in events if isinstance(one, Answer))
    work = [one.name for one in events if isinstance(one, ToolCall)]

    assert "inspect_sheet" in work
    assert answer.strip()

    # REGRESSION: with only the task and no context, the analyst passed the
    # spreadsheet name as a sheet name and the turn came back as a question.
    assert "5" in answer


def test_the_worker_is_told_which_spreadsheet_and_does_not_confuse_it(a_sheet):
    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    events = list(session.ask("What are the column names?"))

    for call in (one for one in events if isinstance(one, ToolCall)):
        # "TEST - Sales Orders" is a spreadsheet; "Sales Orders" is a sheet.
        assert call.arguments.get("sheet") != SPREADSHEET


def test_one_add_writes_one_row(a_sheet, monkeypatch):
    """REGRESSION: one add-a-row request wrote the row 19 times.

    The supervisor could not see it had already delegated, so the row editor
    was sent out again and again, and each pass wrote the row again. The
    write is stubbed here; what is measured is how many times it happens.
    """
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    written: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "update_cells",
        lambda **sent: written.append(sent) or {"totalUpdatedCells": 4},
    )
    monkeypatch.setattr(
        spreadsheet_service,
        "insert_rows",
        lambda **sent: None,
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    list(
        session.ask(
            "Add a row: Order ID ORD-1100, Region West, Product Desk, Units 3."
        )
    )

    assert len(written) == 1


def test_a_spilling_formula_is_not_filled_down(a_sheet, monkeypatch):
    """REGRESSION: an ARRAYFORMULA put in every row of a column left every
    cell reading #REF!, while the tool reported that it had worked.

    Nothing but the tool's own description tells the model to choose spill
    here, so this measures whether that description is enough.
    """
    from excel_agent.tools import columns as columns_tool

    monkeypatch.setattr(
        columns_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )
    monkeypatch.setattr(
        spreadsheet_service,
        "resolve_sheet",
        lambda id, name=None: {
            "title": "Sales Orders",
            "sheetId": 0,
            "gridProperties": {"columnCount": 26},
        },
    )

    written: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "repeat_cell",
        lambda **sent: written.append(sent) or {},
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    list(
        session.ask(
            "Set the Units column to the formula =ARRAYFORMULA(A2:A & B2:B)."
        )
    )

    # Writing a column formula now waits to be allowed, so nothing lands
    # until the turn is resumed.
    assert written == []

    list(session.resume({"decisions": [{"type": "approve"}]}))

    assert written, "the formula was never written"

    covered = written[0]["grid_range"]

    assert covered["endRowIndex"] - covered["startRowIndex"] == 1, (
        "a spilling formula was written into more than one row"
    )


# Answer quality, measured rather than asserted


REPEATED = re.compile(r"(?<=[.!?])\s*|\n+")


def said_twice(answer: str) -> bool:
    """Whether a sentence appears more than once, however punctuated."""
    counted: dict[str, int] = {}

    for one in REPEATED.split(answer):
        key = re.sub(r"[^\w]+", "", one).casefold()

        if len(key) > 8:
            counted[key] = counted.get(key, 0) + 1

    return any(count > 1 for count in counted.values())


def test_short_follow_ups_do_not_come_back_said_twice(a_sheet):
    """The model repeats itself on short follow-ups late in a conversation.

    A rate, not a guarantee: this is model quality, and the run is allowed
    one bad answer out of the four so a single flake does not fail the
    build. Two means it got worse.
    """
    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    doubled = 0

    for question in (
        "how many rows are in the sheet?",
        "and what are the column names?",
        "which region appears most often?",
        "and how many times?",
    ):
        answer = next(
            one.text
            for one in session.ask(question)
            if isinstance(one, Answer)
        )

        doubled += said_twice(answer)

    assert doubled <= 1, f"{doubled} of 4 answers repeated themselves"


def test_the_judge_reads_a_verdict_the_code_can_parse():
    """The semantic judge asks for prose starting PASS or FAIL, because this
    model returned malformed JSON about half the time when asked for a
    schema. Measured 12 of 12 on the probe; this keeps that measurable."""
    from excel_agent.graph.validator import judged

    model = build_model()

    caught = judged(
        model,
        "The row was duplicated 15 times as requested.",
        "duplicate the row 15 times",
        ["[row_editor] Appended 9 copies of row 2, then ran out of steps."],
    )

    assert caught, "a claim of 15 against a report of 9 went unchallenged"

    clean = judged(
        model,
        "Row 3 has been deleted.",
        "delete row 3",
        ["[row_editor] Deleted row 3 from Sales Orders."],
    )

    assert clean is None


def test_deleting_several_rows_asks_for_permission_once(a_sheet, monkeypatch):
    """The point of the rows argument: one request, one approval, one batch.

    Three separate calls would be three approval prompts, which is the
    exact experience the batched form exists to remove.
    """
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    deleted: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "delete_rows",
        lambda **sent: deleted.append(sent) or {},
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    waiting = [
        one for one in session.ask("delete rows 3, 4 and 5")
        if isinstance(one, Approval)
    ]

    assert len(waiting) == 1, f"{len(waiting)} approvals for one deletion"
    assert deleted == []

    list(session.resume({"decisions": [{"type": "approve"}]}))

    assert [one["ranges"] for one in deleted] == [[(3, 5)]]


def test_a_refused_deletion_ends_with_a_graceful_answer(a_sheet, monkeypatch):
    """REGRESSION: told "rejected, do not retry" as middleware feedback, the
    model retried the deletion, paused the turn a second time and the user
    was shown a turn that finished without a written response.

    Refusal is now the tool call's own result, in the ok:false shape every
    tool failure takes, which this model reads as final.
    """
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool
    from excel_agent.ui import REFUSED

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    deleted: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "delete_rows",
        lambda **sent: deleted.append(sent) or {},
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    list(session.ask("delete rows 3 and 4"))

    events = list(
        session.resume(
            {"decisions": [{"type": "respond", "message": REFUSED}]}
        )
    )

    answer = next(
        (one.text for one in events if isinstance(one, Answer)), ""
    )

    assert deleted == []
    assert not [one for one in events if isinstance(one, Approval)], (
        "the refused deletion was asked for again"
    )
    assert answer.strip(), "the refusal ended without a written answer"


def test_filling_many_rows_is_one_call_not_one_per_row(a_sheet, monkeypatch):
    """REGRESSION, from a live trace: "fill the empty rows" became twenty
    update_row calls, ran out of steps at row 47, and came back having done
    part of the job. One block, one call."""
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    written: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "update_cells",
        lambda **sent: written.append(sent) or {"totalUpdatedCells": 8},
    )
    monkeypatch.setattr(
        spreadsheet_service, "insert_rows", lambda **sent: None
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    waiting = [
        one
        for one in session.ask(
            "Set the Region for rows 2 to 6 to North, South, East, West "
            "and North in that order."
        )
        if isinstance(one, Approval)
    ]

    assert waiting, "the block never asked to be allowed"
    assert written == []

    list(session.resume({"decisions": [{"type": "approve"}]}))

    assert len(written) == 1, f"{len(written)} writes for one block of rows"

    ranges = [
        one["range"]
        for update in written
        for one in update["updates"]
    ]
    assert len(ranges) == 5


def test_sorting_uses_the_sort_tool_not_delete_and_re_add(a_sheet, monkeypatch):
    """REGRESSION: with no sort tool, "sort books by Rating" was answered by
    deleting the columns and adding them back."""
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    sent: list[str] = []

    for name in ("sort_range", "delete_columns", "insert_columns", "delete_rows"):
        monkeypatch.setattr(
            spreadsheet_service,
            name,
            lambda _name=name, **args: sent.append(_name) or {},
        )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    waiting = [
        one for one in session.ask("sort the rows by Units, highest first")
        if isinstance(one, Approval)
    ]

    assert waiting, "sorting never asked to be allowed"

    list(session.resume({"decisions": [{"type": "approve"}]}))

    assert sent == ["sort_range"], f"sorting reached for {sent}"


def test_a_lowercased_column_name_reaches_the_column(a_sheet, monkeypatch):
    """REGRESSION, from the traces: column_not_found on 'profit margin'
    against a 'Profit Margin' header. The column tools were fixed first;
    this is a row tool, which resolves names through header_map."""
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    written: list[dict] = []

    monkeypatch.setattr(
        spreadsheet_service,
        "update_cells",
        lambda **sent: written.append(sent) or {"totalUpdatedCells": 1},
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    list(session.ask("set the region in row 3 to Central"))
    list(session.resume({"decisions": [{"type": "approve"}]}))

    assert written, "the lowercase column name never reached a write"
    ranges = [one["range"] for update in written for one in update["updates"]]
    # Region is column B, whatever case the model wrote it in.
    assert any("B3" in one for one in ranges), ranges


def test_the_judge_catches_the_model_thinking_out_loud():
    """REGRESSION, seen live: "The books Emma and Ulyshan...? Wait, the
    second book is Ulysses have been added." The judge's rubric covered
    false claims and scope but not the writer's working reaching the page.

    Measured 18 of 18 on the probe; this keeps the clause honest, and the
    passing half keeps it from firing on ordinary answers."""
    from excel_agent.graph.validator import judged

    model = build_model()

    thinking = judged(
        model,
        'The books "Emma" (rating 4) and "Ulyshan...? Wait, the second book '
        'is "Ulysses" (rating 2) have been added.',
        "add Emma and Ulysses",
        ["[row_editor] Appended 2 rows: Emma, Ulysses."],
    )

    assert thinking, "the writer's working reached the user unchallenged"

    finished = judged(
        model,
        "Emma and Ulysses have been added.",
        "add Emma and Ulysses",
        ["[row_editor] Appended 2 rows: Emma, Ulysses."],
    )

    assert finished is None, f"a clean answer was sent back: {finished}"


def test_a_write_far_past_the_data_does_not_pad_the_sheet(a_sheet, monkeypatch):
    """REGRESSION, seen live: asked to put a value in row 500, the model
    tried to add 489 empty rows first -- two approval cards for hundreds of
    blank rows -- because nothing told it a write may simply land there."""
    from excel_agent.runner import Approval
    from excel_agent.tools import rows as rows_tool

    monkeypatch.setattr(
        rows_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    monkeypatch.setattr(
        spreadsheet_service,
        "update_cells",
        lambda **sent: {"totalUpdatedCells": 1},
    )
    monkeypatch.setattr(
        spreadsheet_service, "insert_rows", lambda **sent: None
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    waiting = [
        one
        for one in session.ask('put "audit" in the Region column of row 500')
        if isinstance(one, Approval)
    ]

    assert waiting, "the write never asked to be allowed"

    asked = waiting[0]
    rows = asked.arguments.get("rows")

    # One row is being written, not a block of hundreds of empty ones.
    assert not (isinstance(rows, list) and len(rows) > 5), (
        f"{asked.tool} was asked to write {len(rows or [])} rows"
    )


def test_sorting_reaches_the_editor_that_owns_it(a_sheet):
    """REGRESSION, seen live: "sort by Rating" was answered "not possible
    with the available tools". row_editor holds sort_rows; the supervisor's
    routing had no line for sorting, so the request went elsewhere."""
    from excel_agent.runner import Approval

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    events = list(session.ask("sort the rows by Units"))

    tools = [one.name for one in events if isinstance(one, ToolCall)]
    waiting = [one.tool for one in events if isinstance(one, Approval)]

    assert "sort_rows" in tools + waiting, (
        f"sorting never reached sort_rows; called {tools}"
    )


def test_a_column_asked_for_by_its_letter_is_read(a_sheet, monkeypatch):
    """REGRESSION, from the traces: sheet_stats(column='E') and
    find_data(column='title') both came back column_not_found. A letter is
    an address now, and a name is matched however it was capitalised."""
    from excel_agent.tools import stats as stats_tool

    monkeypatch.setattr(
        stats_tool,
        "resolve_spreadsheet",
        lambda name=None: ("an-id", name or SPREADSHEET),
    )

    session = Session(build_graph(build_model()))
    session.use(SPREADSHEET)

    answer = next(
        one.text
        for one in session.ask("what is the total of column C?")
        if isinstance(one, Answer)
    )

    # Column C is Units: 1+2+3+4+5.
    assert "15" in answer, answer
