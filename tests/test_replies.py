"""Tests for the text that reaches the user.

Pure functions: no model, no graph, no Google.
"""

from excel_agent.graph.replies import (
    table_free,
    undoubled,
    without_drawn_table,
)


DRAWN = [["Order ID", "Region", "Units", "Product"]]

THE_SAME_TABLE = (
    "Here they are:\n"
    "| Order ID | Region | Units | Product |\n"
    "|---|---|---|---|\n"
    "| ORD-1 | West | 3 | Desk |"
)


def test_the_table_the_application_drew_is_removed():
    assert without_drawn_table(THE_SAME_TABLE, DRAWN) == "Here they are:"


def test_a_table_sharing_only_some_columns_is_kept():
    """REGRESSION: overlap was enough, so two unrelated tables sharing a
    couple of column names cost the user an answer the planner wrote."""
    said = "Compare:\n| Region | Units |\n|---|---|\n| West | 12 |"

    assert without_drawn_table(said, DRAWN) == said


def test_a_table_of_its_own_is_kept():
    said = "Add these:\n| Column | Why |\n|---|---|\n| Pages | length |"

    assert without_drawn_table(said, DRAWN) == said


def test_a_single_column_table_can_match():
    """Matching on overlap of two could never recognise a one-column table."""
    said = "Rows:\n| Title |\n|---|\n| Dune |"

    assert without_drawn_table(said, [["Title"]]) == "Rows:"


def test_each_drawn_table_is_matched_on_its_own():
    # Pooled into one set of columns, a heading naming half of each would
    # have passed for a real table.
    two = [["Title", "Author"], ["Region", "Units"]]
    said = "Mixed:\n| Title | Units |\n|---|---|\n| Dune | 3 |"

    assert without_drawn_table(said, two) == said


def test_nothing_drawn_leaves_every_table_alone():
    assert without_drawn_table(THE_SAME_TABLE, []) == THE_SAME_TABLE
    assert without_drawn_table(THE_SAME_TABLE, None) == THE_SAME_TABLE


# Repetition, and the line that keeps a stripped report speaking


def test_an_answer_said_exactly_twice_is_said_once():
    assert undoubled("Fantasy.Fantasy.") == "Fantasy."
    assert undoubled("6.6.") == "6."
    assert undoubled("The author is X. The author is X.") == "The author is X."


def test_a_near_repeat_is_left_as_written():
    """Anything looser starts correcting the model's writing, and a clumsy
    answer should stay visible rather than be tidied into looking right."""
    for said in (
        "J.R.R Tolkien.J.R.R. Tolkien.",
        "193193.1937.",
        "Yes. No.",
        "There are 51 rows.",
        "aa",
    ):
        assert undoubled(said) == said


def test_a_table_is_cut_but_the_words_around_it_survive():
    said = "Here:\n| a | b |\n|---|---|\n| 1 | 2 |\nThat is all."

    assert table_free(said) == "Here:\nThat is all."
