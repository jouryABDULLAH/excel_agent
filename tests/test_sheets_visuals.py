"""Tests for the two tools that change how a sheet looks, without Google.

Charts and styling are kept apart from the tools that change data because
neither of them touches a value: what is checked here is the request that
would have been sent, and that nothing is sent when a call is refused.
"""

import fake_sheets
import pytest
from pydantic import ValidationError

from excel_agent.tools.sheets import charts, inspect, style

SPREADSHEET = "TEST - Sales Orders"
SHEET = "Sales Orders"


@pytest.fixture
def a_sheet(monkeypatch):
    """Point a tool at a sheet built by hand, recording what it would send."""

    def use(module, rows=None, drawn=()):
        sent: list = []
        monkeypatch.setattr(
            module, "resolve_spreadsheet", lambda name=None: ("an-id", SPREADSHEET)
        )
        monkeypatch.setattr(
            module,
            "resolve_sheet",
            lambda id, name=None: {"title": SHEET, "sheetId": 0},
        )
        monkeypatch.setattr(module, "grid", lambda id, title: rows or fake_sheets.orders())
        # A reading tool has neither of these, so what it has is what is patched.
        if hasattr(module, "batch"):
            monkeypatch.setattr(module, "batch", lambda id, requests: sent.append(requests))
        if hasattr(module, "charts_in"):
            monkeypatch.setattr(module, "charts_in", lambda id, title: list(drawn))
        return sent

    return use


def a_chart(chart_id: int, title: str, kind: str = "basic") -> dict:
    """What Google gives back for one chart."""
    spec: dict = {"title": title}
    spec.update(
        {"pieChart": {}} if kind == "pie" else {"basicChart": {"chartType": "COLUMN"}}
    )
    return {"chartId": chart_id, "spec": spec}


# Drawing


def test_a_chart_is_drawn_from_columns_named_by_their_headers(a_sheet):
    sent = a_sheet(charts)

    answer = charts.modify_chart.invoke(
        {
            "action": "add",
            "kind": "column",
            "labels_column": "Product",
            "value_columns": ["Units"],
            "title": "Units by product",
        }
    )

    assert 'Drew a column chart called "Units by product"' in answer
    assert "rows 2 to 6" in answer

    spec = sent[0][0]["addChart"]["chart"]["spec"]
    assert spec["basicChart"]["chartType"] == "COLUMN"
    # The header row is inside the range on purpose: it is what names the
    # series in the legend instead of leaving it as "Series 1".
    domain = spec["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert domain["startRowIndex"] == 0
    assert domain["startColumnIndex"] == 3


def test_a_pie_is_described_differently_from_the_rest(a_sheet):
    sent = a_sheet(charts)

    answer = charts.modify_chart.invoke(
        {
            "action": "add",
            "kind": "pie",
            "labels_column": "Product",
            "value_columns": ["Units", "Order ID"],
            "title": "Share",
        }
    )

    spec = sent[0][0]["addChart"]["chart"]["spec"]
    assert "pieChart" in spec and "basicChart" not in spec
    # A pie has one ring, so the second column is not drawn, and that is said
    # rather than quietly dropped.
    assert "only the first was used" in answer


def test_a_second_chart_is_anchored_below_the_first(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(1, "First")])

    charts.modify_chart.invoke(
        {
            "action": "add",
            "kind": "line",
            "labels_column": "Product",
            "value_columns": ["Units"],
            "title": "Second",
        }
    )

    anchor = sent[0][0]["addChart"]["chart"]["position"]["overlayPosition"]["anchorCell"]
    assert anchor["rowIndex"] == charts.CHART_ROWS
    # Four columns, so the first free one is index 4 counting from zero.
    assert anchor["columnIndex"] == 4


def test_a_chart_is_removed_by_the_number_it_was_listed_as(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "First"), a_chart(22, "Second")])

    answer = charts.modify_chart.invoke({"action": "remove", "chart": 2})

    assert 'Removed chart 2, "Second"' in answer
    assert sent[0] == [{"deleteEmbeddedObject": {"objectId": 22}}]


def test_retitling_sends_the_whole_spec_back_with_one_thing_changed(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "Old name")])

    answer = charts.modify_chart.invoke(
        {"action": "retitle", "chart": 1, "title": "New name"}
    )

    assert 'from "Old name" to "New name"' in answer
    changed = sent[0][0]["updateChartSpec"]
    assert changed["chartId"] == 11
    assert changed["spec"]["title"] == "New name"
    # There is no way to change a title on its own, so what the chart draws
    # has to go back untouched alongside it.
    assert "basicChart" in changed["spec"]


def test_a_chart_number_that_is_not_there_is_refused(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "First")])

    answer = charts.modify_chart.invoke({"action": "remove", "chart": 99})

    assert "There is no chart 99" in answer
    assert "numbered 1 to 1" in answer
    assert sent == []


def test_removing_when_there_are_no_charts_says_so(a_sheet):
    sent = a_sheet(charts)

    assert "no charts on this sheet" in charts.modify_chart.invoke(
        {"action": "remove", "chart": 1}
    )
    assert sent == []


def test_a_kind_that_does_not_exist_never_reaches_the_sheet(a_sheet):
    sent = a_sheet(charts)

    # The kinds are a Literal, so a wrong one is refused by the schema before
    # the tool runs at all. create_agent hands that back to the model as the
    # tool's result, so it is told what it did wrong either way.
    with pytest.raises(ValidationError):
        charts.modify_chart.invoke(
            {
                "action": "add",
                "kind": "doughnut",
                "labels_column": "Product",
                "value_columns": ["Units"],
                "title": "X",
            }
        )

    assert sent == []


def test_adding_without_saying_what_sort_of_chart_is_refused(a_sheet):
    sent = a_sheet(charts)

    answer = charts.modify_chart.invoke(
        {
            "action": "add",
            "labels_column": "Product",
            "value_columns": ["Units"],
            "title": "X",
        }
    )

    assert "column, bar, line, scatter, pie" in answer
    assert sent == []


def test_a_column_that_does_not_exist_is_refused(a_sheet):
    sent = a_sheet(charts)

    answer = charts.modify_chart.invoke(
        {
            "action": "add",
            "kind": "column",
            "labels_column": "Nonsense",
            "value_columns": ["Units"],
            "title": "X",
        }
    )

    assert "Unknown column(s): Nonsense" in answer
    assert sent == []


def test_the_charts_on_a_sheet_are_listed_when_it_is_read(a_sheet):
    a_sheet(inspect, drawn=[a_chart(11, "Units by product"), a_chart(22, "Share", "pie")])

    answer = inspect.inspect_sheet.invoke({})

    # A chart has an id but no name, so this numbering is the only way
    # modify_chart can be pointed at one.
    assert "2 chart(s) on this sheet:" in answer
    assert "  1. Units by product (column)" in answer
    assert "  2. Share (pie)" in answer


# How the sheet looks


def test_a_column_can_be_given_a_number_format(a_sheet):
    sent = a_sheet(style)

    answer = style.modify_style.invoke({"column": "Units", "number_format": "#,##0.00"})

    assert "are now shown as #,##0.00" in answer
    assert "values themselves are unchanged" in answer
    asked = sent[0][0]["repeatCell"]
    assert asked["cell"]["userEnteredFormat"]["numberFormat"] == {
        "type": "NUMBER",
        "pattern": "#,##0.00",
    }
    assert asked["fields"] == "userEnteredFormat.numberFormat"


def test_what_sort_of_format_it_is_comes_from_the_pattern(a_sheet):
    sent = a_sheet(style)

    for pattern in ("0%", "dd/mm/yyyy", "$#,##0"):
        style.modify_style.invoke({"column": "Units", "number_format": pattern})

    # Google refuses a date pattern labelled as a number, and what the pattern
    # is made of is enough to tell which it is.
    kinds = [
        one[0]["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]["type"]
        for one in sent
    ]
    assert kinds == ["PERCENT", "DATE", "CURRENCY"]


def test_a_run_of_rows_across_every_column_can_be_styled(a_sheet):
    sent = a_sheet(style)

    answer = style.modify_style.invoke(
        {"first_row": 1, "last_row": 1, "bold": True, "background": "yellow"}
    )

    assert "Rows 1 to 1 of every column are now bold, filled yellow" in answer
    asked = sent[0][0]["repeatCell"]
    # No column named, so no column bounds: the whole width of those rows.
    assert "startColumnIndex" not in asked["range"]
    assert asked["range"]["startRowIndex"] == 0
    assert asked["range"]["endRowIndex"] == 1


def test_a_colour_becomes_fractions_of_one(a_sheet):
    sent = a_sheet(style)

    style.modify_style.invoke({"column": "Units", "background": "#ffffff"})

    assert sent[0][0]["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"] == {
        "red": 1.0,
        "green": 1.0,
        "blue": 1.0,
    }


def test_only_the_parts_named_are_touched(a_sheet):
    sent = a_sheet(style)

    style.modify_style.invoke({"column": "Units", "bold": True})

    # Setting one thing must not clear a format set earlier, which is what the
    # field mask is for.
    assert sent[0][0]["repeatCell"]["fields"] == "userEnteredFormat.textFormat.bold"


def test_a_colour_that_cannot_be_read_is_refused(a_sheet):
    sent = a_sheet(style)

    answer = style.modify_style.invoke({"column": "Units", "background": "octarine"})

    assert "not a colour I can read" in answer
    assert "#fff2cc" in answer
    assert sent == []


def test_styling_needs_something_to_change(a_sheet):
    sent = a_sheet(style)

    assert "Say what to change" in style.modify_style.invoke({"column": "Units"})
    assert sent == []


def test_styling_needs_somewhere_to_change_it(a_sheet):
    sent = a_sheet(style)

    assert "Say which cells to change" in style.modify_style.invoke({"bold": True})
    assert sent == []


def test_a_column_that_is_not_there_is_refused_before_anything_is_styled(a_sheet):
    sent = a_sheet(style)

    answer = style.modify_style.invoke({"column": "Nonsense", "bold": True})

    assert 'There is no column called "Nonsense"' in answer
    assert sent == []
