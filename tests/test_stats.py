"""Tests for the summary tool.

Everything goes through .invoke, so what is asserted on is the line the model
would read back. The numbers here are worked out by hand from the fixtures, so
a wrong answer shows up as a wrong number rather than as a crash.
"""

import make_fixtures

from excel_agent.tools.stats import sheet_stats, summarise


def line_for(answer: str, column: str) -> str:
    """The row of the table describing one column."""
    for line in answer.splitlines():
        if line.startswith(f"| {column} |"):
            return line
    raise AssertionError(f"no line for {column} in:\n{answer}")


# Summarising values


def test_numbers_get_their_range_and_their_total():
    assert summarise([7, 40, 12]) == "7 to 40, adding up to 59"


def test_a_total_does_not_trail_off_into_decimals():
    # 24.5 + 18 + 89.99 in floating point is 132.48999999999998.
    assert summarise([24.5, 18, 89.99]) == "18 to 89.99, adding up to 132.49"


def test_text_gets_whatever_turns_up_most():
    assert summarise(["EU", "US", "EU"]) == '"EU" most often, 2 times'


def test_text_that_never_repeats_says_so():
    assert summarise(["EU", "US", "APAC"]) == "every value different"


def test_true_and_false_are_not_added_up():
    # Python counts True as 1, and a column of yes and no has no total.
    assert summarise([True, False, True]) == '"True" most often, 2 times'


# Summarising a sheet


def test_every_column_is_summarised(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))

    answer = sheet_stats.invoke({})

    assert "Sheet: Sales in clean_table.xlsx (5 rows of data" in answer
    assert "| column | filled | blank | different | summary |" in answer
    # 12 + 30 + 7 + 40 + 18
    assert line_for(answer, "Units") == "| Units | 5 | 0 | 5 | 7 to 40, adding up to 107 |"
    # EU twice, US twice, APAC once.
    assert line_for(answer, "Region") == '| Region | 5 | 0 | 3 | "EU" most often, 2 times |'


def test_only_the_columns_asked_for_are_summarised(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))

    answer = sheet_stats.invoke({"columns": ["Units"]})

    assert "| Units |" in answer
    assert "| Region |" not in answer


def test_blank_cells_are_counted_rather_than_ignored(tmp_path, use_workbook):
    use_workbook(make_fixtures.blank_rows_inside(tmp_path))

    answer = sheet_stats.invoke({})

    # The data runs from row 2 to row 8 with row 5 empty, so every column has
    # six values and one gap.
    assert line_for(answer, "Product").startswith("| Product | 6 | 1 |")


def test_dates_get_a_range_and_no_total(tmp_path, use_workbook):
    use_workbook(make_fixtures.formatting_past_data(tmp_path))

    answer = sheet_stats.invoke({"columns": ["Order Date"]})

    assert "2026-01-14 to 2026-03-03" in line_for(answer, "Order Date")
    assert "adding up to" not in line_for(answer, "Order Date")


def test_a_calculated_column_says_so_instead_of_totalling_nothing(
    tmp_path, use_workbook
):
    use_workbook(make_fixtures.formulas_all_the_way_down(tmp_path))

    answer = sheet_stats.invoke({})

    # Nothing has opened this file in Excel, so the results of the formulas
    # are not in it. Reporting a total of zero would be a lie.
    summary = line_for(answer, "Total")
    assert "worked out by the sheet" in summary
    assert "adding up to 0" not in summary
    # The columns it is worked out from are ordinary numbers and still add up.
    assert "adding up to 107" in line_for(answer, "Units")


def test_an_unknown_column_is_named_and_the_real_ones_listed(tmp_path, use_workbook):
    use_workbook(make_fixtures.clean_table(tmp_path))

    answer = sheet_stats.invoke({"columns": ["Profit"]})

    assert "Unknown column(s): Profit" in answer
    assert "The sheet has: ID, Product, Region, Units, Unit Price." in answer


def test_a_named_sheet_is_the_one_summarised(tmp_path, use_workbook):
    use_workbook(make_fixtures.multi_sheet(tmp_path))

    answer = sheet_stats.invoke({"sheet": "Notes"})

    assert "Sheet: Notes in multi_sheet.xlsx" in answer
    assert "| Author |" in answer
    assert "| Product |" not in answer


def test_a_sheet_that_does_not_exist_is_refused(tmp_path, use_workbook):
    use_workbook(make_fixtures.multi_sheet(tmp_path))

    answer = sheet_stats.invoke({"sheet": "Summary"})

    assert 'There is no sheet called "Summary"' in answer
    assert "Sales, Notes" in answer