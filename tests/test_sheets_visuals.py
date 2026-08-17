"""Tests for the tools that change how a sheet looks, without Google.

Charts and styling are kept apart from the tools that change data because
neither of them touches a value: what is checked here is the request that
would have been sent, and that nothing is sent when a call is refused.

The request is read where the tool hands it to the service. The arithmetic
the service then does on it is covered in test_services_spreadsheet.
"""

import fake_sheets
import pytest
from pydantic import ValidationError

from excel_agent.services.spreadsheet import spreadsheet_service
from excel_agent.tools import charts, inspect, style

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

        properties = {"title": SHEET, "sheetId": 0}

        monkeypatch.setattr(
            spreadsheet_service, "resolve_sheet", lambda id, name=None: properties
        )
        monkeypatch.setattr(
            spreadsheet_service,
            "read_sheet",
            lambda id, name: rows if rows is not None else fake_sheets.orders(),
        )
        monkeypatch.setattr(
            spreadsheet_service, "list_charts", lambda id, name=None: list(drawn)
        )

        def recording(name, answer=None):
            def called(*arguments, **named):
                sent.append({"call": name, "args": arguments, **named})
                return answer if answer is not None else {}

            return called

        monkeypatch.setattr(
            spreadsheet_service, "add_chart", recording("add_chart", {"chartId": 77})
        )

        for name in ("update_chart_spec", "delete_chart", "format_range"):
            monkeypatch.setattr(spreadsheet_service, name, recording(name))

        return sent

    return use


def a_chart(chart_id: int, title: str, kind: str = "basic") -> dict:
    """What Google gives back for one chart."""
    spec: dict = {"title": title}
    spec.update(
        {"pieChart": {}} if kind == "pie" else {"basicChart": {"chartType": "COLUMN"}}
    )
    return {"chartId": chart_id, "spec": spec}


def drawn_chart(sent: list) -> dict:
    """The chart the service was asked to add."""
    added = [one for one in sent if one["call"] == "add_chart"]
    assert len(added) == 1
    return added[0]["args"][1]


def formatting(sent: list) -> list[dict]:
    """Every formatting call the service was asked for."""
    return [one for one in sent if one["call"] == "format_range"]


# Drawing


def test_a_chart_is_drawn_from_columns_named_by_their_headers(a_sheet):
    sent = a_sheet(charts)

    answer = charts.create_chart.invoke(
        {
            "kind": "column",
            "labels_column": "Product",
            "value_columns": ["Units"],
            "title": "Units by product",
        }
    )

    assert answer["ok"] is True
    assert answer["kind"] == "column"
    assert answer["chart_id"] == 77
    assert (answer["first_data_row"], answer["last_data_row"]) == (2, 6)

    spec = drawn_chart(sent)["spec"]
    assert spec["basicChart"]["chartType"] == "COLUMN"
    # The header row is inside the range on purpose: it is what names the
    # series in the legend instead of leaving it as "Series 1".
    domain = spec["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert domain["startRowIndex"] == 0
    assert domain["startColumnIndex"] == 3


def test_a_pie_is_drawn_from_one_column_however_many_are_given(a_sheet):
    sent = a_sheet(charts)

    answer = charts.create_chart.invoke(
        {
            "kind": "pie",
            "labels_column": "Product",
            "value_columns": ["Units", "Order ID"],
            "title": "Share",
        }
    )

    spec = drawn_chart(sent)["spec"]
    assert "pieChart" in spec and "basicChart" not in spec
    # A pie has one ring, so the second column is not drawn. What was used
    # comes back, rather than the second column being quietly dropped.
    assert answer["value_columns"] == ["Units"]


def test_a_second_chart_is_anchored_below_the_first(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(1, "First")])

    charts.create_chart.invoke(
        {
            "kind": "line",
            "labels_column": "Product",
            "value_columns": ["Units"],
            "title": "Second",
        }
    )

    anchor = drawn_chart(sent)["position"]["overlayPosition"]["anchorCell"]
    assert anchor["rowIndex"] == charts.CHART_ROWS
    # Four columns, so the first free one is index 4 counting from zero.
    assert anchor["columnIndex"] == 4


def test_a_chart_is_removed_by_its_own_id(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "First"), a_chart(22, "Second")])

    answer = charts.delete_chart.invoke({"chart_id": 22})

    assert answer["ok"] is True
    assert answer["title"] == "Second"
    # The data the chart was drawn from stays where it is.
    assert answer["data_changed"] is False

    removed = [one for one in sent if one["call"] == "delete_chart"]
    assert removed[0]["args"][1] == 22


def test_retitling_sends_the_whole_spec_back_with_one_thing_changed(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "Old name")])

    answer = charts.update_chart.invoke({"chart_id": 11, "title": "New name"})

    assert (answer["old_title"], answer["title"]) == ("Old name", "New name")

    changed = [one for one in sent if one["call"] == "update_chart_spec"][0]
    chart_id, spec = changed["args"][1], changed["args"][2]
    assert chart_id == 11
    assert spec["title"] == "New name"
    # There is no way to change a title on its own, so what the chart draws
    # has to go back untouched alongside it.
    assert "basicChart" in spec


def test_the_sort_of_chart_can_be_changed_without_redrawing_it(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "First")])

    answer = charts.update_chart.invoke({"chart_id": 11, "kind": "line"})

    assert (answer["old_kind"], answer["kind"]) == ("column", "line")

    spec = [one for one in sent if one["call"] == "update_chart_spec"][0]["args"][2]
    assert spec["basicChart"]["chartType"] == "LINE"


def test_turning_a_pie_into_a_bar_chart_is_refused_rather_than_half_done(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "Share", "pie")])

    answer = charts.update_chart.invoke({"chart_id": 11, "kind": "column"})

    # A pie and a basic chart describe their data differently, so there is no
    # spec that is both. Redrawing it is the only honest answer.
    assert answer["ok"] is False
    assert answer["error"] == "incompatible_chart_type_change"
    assert sent == []


def test_a_chart_id_that_is_not_there_is_refused(a_sheet):
    sent = a_sheet(charts, drawn=[a_chart(11, "First")])

    answer = charts.delete_chart.invoke({"chart_id": 99})

    assert answer["ok"] is False
    assert answer["error"] == "chart_not_found"
    # The ids that do exist come back, so the next call can be right.
    assert answer["available_chart_ids"] == [11]
    assert sent == []


def test_removing_when_there_are_no_charts_says_so(a_sheet):
    sent = a_sheet(charts)

    answer = charts.delete_chart.invoke({"chart_id": 1})

    assert answer["error"] == "chart_not_found"
    assert answer["available_chart_ids"] == []
    assert sent == []


def test_a_kind_that_does_not_exist_never_reaches_the_sheet(a_sheet):
    sent = a_sheet(charts)

    # The kinds are a Literal, so a wrong one is refused by the schema before
    # the tool runs at all. create_agent hands that back to the model as the
    # tool's result, so it is told what it did wrong either way.
    with pytest.raises(ValidationError):
        charts.create_chart.invoke(
            {
                "kind": "doughnut",
                "labels_column": "Product",
                "value_columns": ["Units"],
                "title": "X",
            }
        )

    assert sent == []


def test_drawing_without_saying_what_sort_of_chart_is_refused(a_sheet):
    sent = a_sheet(charts)

    # kind has no default, so a call without one never reaches the tool.
    with pytest.raises(ValidationError):
        charts.create_chart.invoke(
            {
                "labels_column": "Product",
                "value_columns": ["Units"],
                "title": "X",
            }
        )

    assert sent == []


def test_a_column_that_does_not_exist_is_refused(a_sheet):
    sent = a_sheet(charts)

    answer = charts.create_chart.invoke(
        {
            "kind": "column",
            "labels_column": "Nonsense",
            "value_columns": ["Units"],
            "title": "X",
        }
    )

    assert answer["ok"] is False
    assert answer["error"] == "unknown_columns"
    assert answer["unknown_columns"] == ["Nonsense"]
    assert answer["available_columns"] == ["Order ID", "Region", "Units", "Product"]
    assert sent == []


def test_the_charts_on_a_sheet_are_listed_when_it_is_read(a_sheet):
    a_sheet(inspect, drawn=[a_chart(11, "Units by product"), a_chart(22, "Share", "pie")])

    answer = inspect.inspect_sheet.invoke({})

    # A chart has an id but no name, so the id is the only way to point one of
    # the chart tools at it.
    assert "2 chart(s) on this sheet:" in answer
    assert "chart_id=11: Units by product (column)" in answer
    assert "chart_id=22: Share (pie)" in answer


# How the sheet looks


def test_a_column_can_be_given_a_number_format(a_sheet):
    sent = a_sheet(style)

    answer = style.format_range.invoke(
        {"columns": ["Units"], "number_format": "#,##0.00"}
    )

    assert answer["ok"] is True
    # Formatting says how a value is shown, never what it is.
    assert answer["values_changed"] is False

    asked = formatting(sent)[0]
    assert asked["cell_format"]["numberFormat"] == {
        "type": "NUMBER",
        "pattern": "#,##0.00",
    }
    assert asked["fields"] == ["userEnteredFormat.numberFormat"]


def test_what_sort_of_format_it_is_comes_from_the_pattern(a_sheet):
    sent = a_sheet(style)

    for pattern in ("0%", "dd/mm/yyyy", "$#,##0"):
        style.format_range.invoke({"columns": ["Units"], "number_format": pattern})

    # Google refuses a date pattern labelled as a number, and what the pattern
    # is made of is enough to tell which it is.
    kinds = [
        one["cell_format"]["numberFormat"]["type"] for one in formatting(sent)
    ]
    assert kinds == ["PERCENT", "DATE", "CURRENCY"]


def test_a_run_of_rows_across_every_column_can_be_styled(a_sheet):
    sent = a_sheet(style)

    answer = style.format_range.invoke(
        {"first_row": 1, "last_row": 1, "bold": True, "background": "yellow"}
    )

    assert answer["ok"] is True
    # No columns named, so every named column is covered.
    assert answer["columns"] == ["Order ID", "Region", "Units", "Product"]

    ranges = formatting(sent)[0]["ranges"]
    assert all(one["startRowIndex"] == 0 for one in ranges)
    assert all(one["endRowIndex"] == 1 for one in ranges)


def test_a_colour_becomes_fractions_of_one(a_sheet):
    sent = a_sheet(style)

    style.format_range.invoke({"columns": ["Units"], "background": "#ffffff"})

    assert formatting(sent)[0]["cell_format"]["backgroundColor"] == {
        "red": 1.0,
        "green": 1.0,
        "blue": 1.0,
    }


def test_only_the_parts_named_are_touched(a_sheet):
    sent = a_sheet(style)

    style.format_range.invoke({"columns": ["Units"], "bold": True})

    # Setting one thing must not clear a format set earlier, which is what the
    # field mask is for.
    assert formatting(sent)[0]["fields"] == ["userEnteredFormat.textFormat.bold"]


def test_a_colour_that_cannot_be_read_is_refused(a_sheet):
    sent = a_sheet(style)

    answer = style.format_range.invoke(
        {"columns": ["Units"], "background": "octarine"}
    )

    assert answer["ok"] is False
    assert answer["error"] == "invalid_colour"
    assert sent == []


def test_styling_needs_something_to_change(a_sheet):
    sent = a_sheet(style)

    answer = style.format_range.invoke({"columns": ["Units"]})

    assert answer["error"] == "no_format_change"
    assert sent == []


def test_a_column_that_is_not_there_is_refused_before_anything_is_styled(a_sheet):
    sent = a_sheet(style)

    answer = style.format_range.invoke({"columns": ["Nonsense"], "bold": True})

    assert answer["ok"] is False
    assert answer["error"] == "unknown_columns"
    assert sent == []
