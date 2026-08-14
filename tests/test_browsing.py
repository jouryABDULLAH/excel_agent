"""Tests for what the front end is told about the spreadsheets it can work on.

Answered without Google: what a spreadsheet holds is built by hand, and the
names sheets.py would have gone out to fetch are replaced by the a_drive
fixture.
"""

from excel_agent import browsing, config


# What a few rows are worth asking about


def test_a_column_of_numbers_is_offered_as_a_total():
    asks = browsing.asks_for({"Product": ["Laptop", "Monitor"], "Units": [1, 2]})

    assert asks[:2] == browsing.GENERIC
    assert "What is the total Units?" in asks
    assert "Draw a bar chart of Units by Product" in asks


def test_a_column_of_identifiers_is_offered_as_nothing():
    # An ID is a number, and a total of it would mean nothing.
    assert browsing.asks_for({"Order ID": [1001, 1002]}) == browsing.GENERIC


def test_an_identifier_goes_by_more_names_than_id():
    # A bar for every SKU is 25 bars and nothing to read off them.
    asks = browsing.asks_for(
        {"SKU": ["SKU-001"], "Category": ["Office"], "Units In Stock": [120]}
    )

    assert "Draw a bar chart of Units In Stock by Category" in asks


def test_a_column_only_ending_in_an_identifier_word_is_left_alone():
    # "Reorder Level" is a measure, whatever "Reorder" looks like.
    asks = browsing.asks_for({"Product": ["Desk"], "Reorder Level": [25]})

    assert "What is the total Reorder Level?" in asks


def test_a_column_of_neither_is_left_alone():
    # Mixed values are not a total and not a label, so nothing is claimed.
    assert browsing.asks_for({"Notes": ["a", 2]}) == browsing.GENERIC


def test_an_empty_column_is_not_mistaken_for_one_of_labels():
    assert browsing.asks_for({"Region": [], "Units": [1]}) == [
        *browsing.GENERIC,
        "What is the total Units?",
    ]


def test_the_spreadsheets_on_drive_are_listed_by_name(a_drive):
    a_drive(files=(("one", "TEST - Sales Orders"), ("two", "TEST - Raw Contacts")))

    assert browsing.workbooks() == ["TEST - Sales Orders", "TEST - Raw Contacts"]


def test_a_spreadsheet_is_read_for_what_it_holds(a_drive, monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", "TEST - Sales Orders")
    a_drive()

    asks = browsing.suggestions()

    assert asks[:2] == browsing.GENERIC
    assert "What is the total Units?" in asks
    # Not "by Order ID", though that is the first column of text: a bar for
    # every row is a chart with nothing to read off it.
    assert "Draw a bar chart of Units by Region" in asks


def test_an_identifier_is_no_good_as_a_label_either():
    asks = browsing.asks_for(
        {"Order ID": ["ORD-1001"], "Region": ["North"], "Units": [1]}
    )

    assert "Draw a bar chart of Units by Region" in asks


def test_nothing_is_read_before_a_spreadsheet_is_chosen(monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", None)

    # Reading would mean asking Drive which file, and there is no answer yet.
    assert browsing.suggestions() == browsing.GENERIC
    assert browsing.in_use() is None
    assert browsing.where() == "[no spreadsheet chosen yet]"


def test_a_spreadsheet_that_will_not_open_still_offers_something(monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", "TEST - Sales Orders")

    def refuse(name=None):
        raise ValueError("There is no spreadsheet called that.")

    monkeypatch.setattr("excel_agent.sheets.resolve_spreadsheet", refuse)

    assert browsing.suggestions() == browsing.GENERIC


def test_where_the_work_goes_names_the_sheet_and_the_file(a_drive, monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", "TEST - Sales Orders")
    a_drive()

    # Neither on its own says where a change lands: a spreadsheet holds
    # several sheets, and the one used when none is named is simply the first.
    assert browsing.where() == "Sales Orders in TEST - Sales Orders"


def test_the_spreadsheet_can_be_opened_where_it_really_lives(a_drive, monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", "TEST - Sales Orders")
    a_drive()

    # The page draws no table of its own: the sheet is the view of the sheet,
    # and it is right in a way a copy stops being the moment anything writes.
    assert browsing.link() == "https://docs.google.com/spreadsheets/d/an-id"


def test_there_is_nowhere_to_go_before_a_spreadsheet_is_chosen(monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", None)

    assert browsing.link() is None


def test_choosing_stores_the_name_drive_really_holds(a_drive, monkeypatch):
    monkeypatch.setattr(config, "SPREADSHEET", None)
    a_drive()

    browsing.choose("sales orders")

    assert config.SPREADSHEET == "TEST - Sales Orders"
