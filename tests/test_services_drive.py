"""Tests for the Drive service the refactor pulled out of sheets.py.

Two jobs live here: building the queries Drive is asked, and deciding which
file a name means. The second is the one worth the most tests, because getting
it wrong sends a change to the wrong spreadsheet, and nothing here can undo
that afterwards.

Everything runs against a Google built by hand, so what a test asserts is the
query that would have gone out rather than what Drive happened to hold.
"""

import pytest

from excel_agent.services.drive import SPREADSHEET_MIME, DriveService, quoted

from fake_google import Endpoint, FakeGoogle


def drive_holding(*files, method="list"):
    """A DriveService whose Drive answers with the given (id, name) pairs."""
    endpoint = Endpoint(
        answers={method: {"files": [{"id": id, "name": name} for id, name in files]}}
    )
    service = DriveService(FakeGoogle(files=endpoint))
    return service, endpoint


def drive_matching(*titles):
    """A DriveService that answers the way Drive does: anything containing the name.

    Drive matches a name by what contains it, so asking for one file can bring
    back several. Resolution is entirely about that, so a test of it needs a
    Drive that really behaves this way rather than one answering a fixed list.
    """
    google = FakeGoogle()
    service = DriveService(google)

    def search(name=None):
        return [
            (f"id-{title}", title)
            for title in titles
            if not name or name.lower() in title.lower()
        ]

    service.search_spreadsheets = search
    return service


# Escaping what goes into a query


@pytest.mark.parametrize(
    "text, escaped",
    (
        ("plain", "plain"),
        ("Bob's file", "Bob\\'s file"),
        ("back\\slash", "back\\\\slash"),
        ("both'\\ways", "both\\'\\\\ways"),
        ("", ""),
    ),
)
def test_a_quote_cannot_close_the_query_early(text, escaped):
    # Search terms reach here from the model, so this is not only about names
    # that happen to have an apostrophe in them.
    assert quoted(text) == escaped


def test_a_name_with_a_quote_reaches_drive_escaped():
    service, endpoint = drive_holding()

    service.search_spreadsheets("Bob's")

    assert "Bob\\'s" in endpoint.asked["q"]


# Asking Drive for spreadsheets by name


def test_only_spreadsheets_that_are_not_in_the_bin_are_asked_for():
    service, endpoint = drive_holding()

    service.search_spreadsheets()

    query = endpoint.asked["q"]
    assert f"mimeType = '{SPREADSHEET_MIME}'" in query
    assert "trashed = false" in query
    # Nothing narrows it when no name is given: that is the whole list.
    assert "name contains" not in query


def test_a_name_narrows_the_search():
    service, endpoint = drive_holding()

    service.search_spreadsheets("sales")

    assert "name contains 'sales'" in endpoint.asked["q"]


def test_the_names_come_back_in_order_and_in_one_page():
    service, endpoint = drive_holding()

    service.search_spreadsheets()

    assert endpoint.asked["orderBy"] == "name"
    assert endpoint.asked["pageSize"] == 50
    # Only the two fields anything here uses. Asking for a whole file listing
    # is a bigger answer for no more information.
    assert endpoint.asked["fields"] == "files(id,name)"


def test_what_comes_back_is_ids_and_names():
    service, _ = drive_holding(("id-1", "One"), ("id-2", "Two"))

    assert service.search_spreadsheets() == [("id-1", "One"), ("id-2", "Two")]


def test_a_drive_holding_nothing_is_an_empty_list_not_an_error():
    service = DriveService(FakeGoogle(files=Endpoint(answers={"list": {}})))

    assert service.search_spreadsheets() == []


def test_the_search_goes_through_execute_so_it_is_retried():
    service, _ = drive_holding(("id-1", "One"))
    google = service._google

    service.search_spreadsheets()

    # Everything must be sent through execute(), or it loses the retries that
    # are the reason that method exists.
    assert len(google.executed) == 1


# Asking Drive what is inside a spreadsheet


def test_searching_the_contents_asks_for_full_text():
    service, endpoint = drive_holding()

    service.search_spreadsheets_by_content("ORD-1042")

    query = endpoint.asked["q"]
    assert "fullText contains 'ORD-1042'" in query
    assert f"mimeType = '{SPREADSHEET_MIME}'" in query
    assert "trashed = false" in query


def test_a_content_search_asks_for_no_order_at_all():
    """Drive refuses a fullText query that names an order.

    It returns these by how well they match instead, which is the more useful
    order anyway. Adding orderBy here would fail every content search.
    """
    service, endpoint = drive_holding()

    service.search_spreadsheets_by_content("anything")

    assert "orderBy" not in endpoint.asked


def test_a_content_search_asks_for_fewer_files():
    service, endpoint = drive_holding()

    service.search_spreadsheets_by_content("anything")

    assert endpoint.asked["pageSize"] == 25


def test_content_search_escapes_what_it_is_given():
    service, endpoint = drive_holding()

    service.search_spreadsheets_by_content("Bob's order")

    assert "Bob\\'s order" in endpoint.asked["q"]


def test_content_search_finding_nothing_is_an_empty_list():
    service, _ = drive_holding()

    assert service.search_spreadsheets_by_content("nothing holds this") == []


# Turning a name into the one file it means


def test_a_name_that_matches_one_file_reaches_it():
    service = drive_matching("TEST - Sales Orders", "TEST - Raw Contacts")

    assert service.resolve_spreadsheet("TEST - Sales Orders") == (
        "id-TEST - Sales Orders",
        "TEST - Sales Orders",
    )


def test_a_name_another_file_begins_with_still_reaches_its_own():
    service = drive_matching("TEST - Sales Orders", "TEST - Sales Orders - scratch")

    # Drive returns both, because one name contains the other. Only one is
    # called this, and that is the answer: otherwise a file could be made
    # unreachable by creating another beside it with a longer name.
    assert service.resolve_spreadsheet("TEST - Sales Orders")[1] == "TEST - Sales Orders"


def test_part_of_a_name_reaches_the_only_file_holding_it():
    service = drive_matching("TEST - Sales Orders", "TEST - Raw Contacts")

    assert service.resolve_spreadsheet("raw")[1] == "TEST - Raw Contacts"


def test_a_name_matching_several_and_none_exactly_is_refused():
    service = drive_matching("TEST - Sales Orders", "TEST - Sales Orders - scratch")

    with pytest.raises(ValueError) as refused:
        service.resolve_spreadsheet("Sales")

    # Asking for a full name is something the tools can act on, which asking
    # for an id is not: no tool takes one.
    assert 'No spreadsheet is called exactly "Sales"' in str(refused.value)
    assert "by its full name" in str(refused.value)
    assert "TEST - Sales Orders" in str(refused.value)


def test_two_files_really_sharing_a_name_are_refused():
    service = drive_matching("TEST - Simple Budget", "TEST - Simple Budget")

    # Nothing here can tell them apart, and picking one would be picking for
    # the user.
    with pytest.raises(ValueError, match="More than one spreadsheet is called"):
        service.resolve_spreadsheet("TEST - Simple Budget")


def test_a_name_reaching_nothing_says_so_and_nothing_more():
    """Saying what to do next is not this layer's job any more.

    use_spreadsheet catches this and answers with the names that do exist, so
    a refusal naming a tool would be telling the reader to do the thing that
    just happened. The service says what went wrong and stops there.
    """
    service = drive_matching("TEST - Sales Orders")

    with pytest.raises(ValueError) as refused:
        service.resolve_spreadsheet("Nonsense")

    assert "There is no spreadsheet called" in str(refused.value)
    assert "list_workbooks" not in str(refused.value)


@pytest.mark.parametrize("given", ("", "   ", "\t\n"))
def test_a_name_that_is_no_name_is_refused_before_drive_is_asked(given):
    service, endpoint = drive_holding(("id-1", "One"))

    with pytest.raises(ValueError, match="A spreadsheet name is required"):
        service.resolve_spreadsheet(given)

    assert endpoint.calls == []


@pytest.mark.parametrize(
    "given", ("TEST - Sales Orders", "test - sales orders", "  TEST - Sales Orders  ")
)
def test_case_and_surrounding_space_do_not_change_which_file_is_meant(given):
    service = drive_matching("TEST - Sales Orders", "TEST - Raw Contacts")

    assert service.resolve_spreadsheet(given)[0] == "id-TEST - Sales Orders"


def test_an_exact_match_wins_even_when_it_is_not_the_first_answer():
    service = drive_matching("A Sales Orders file", "Sales Orders")

    assert service.resolve_spreadsheet("Sales Orders")[1] == "Sales Orders"


# What it remembers


def test_a_name_already_resolved_is_not_asked_about_twice():
    service, endpoint = drive_holding(("id-1", "Sales"))

    service.resolve_spreadsheet("Sales")
    service.resolve_spreadsheet("Sales")

    # One round trip, not two. This is the whole reason the cache exists.
    assert len(endpoint.calls) == 1


def test_a_remembered_name_answers_with_the_name_that_was_asked_for():
    """A cache hit hands back what was typed rather than the file's real title.

    Carried over unchanged from the old sheets.py, so it is not new, but it is
    worth pinning: resolving "sales orders" gives the true title the first
    time and the typed one every time after, from the same argument. Anything
    that stores what comes back stores two different things depending on
    whether it asked first.
    """
    service, _ = drive_holding(("id-1", "TEST - Sales Orders"))

    first = service.resolve_spreadsheet("sales orders")
    again = service.resolve_spreadsheet("sales orders")

    assert first == ("id-1", "TEST - Sales Orders")
    assert again == ("id-1", "sales orders")
    assert first[1] != again[1]


def test_a_refused_name_is_not_remembered_as_an_answer():
    service = drive_matching("TEST - Sales Orders", "TEST - Sales Orders - scratch")

    with pytest.raises(ValueError):
        service.resolve_spreadsheet("Sales")
    with pytest.raises(ValueError):
        service.resolve_spreadsheet("Sales")

    assert "Sales" not in service._spreadsheet_ids


def test_forgetting_a_spreadsheet_drops_every_name_that_reached_it():
    service, endpoint = drive_holding(("id-1", "TEST - Sales Orders"))
    service.resolve_spreadsheet("TEST - Sales Orders")
    service.resolve_spreadsheet("sales")

    service.forget("id-1")

    # Both names pointed at the one file, so both have to go: leaving one
    # would answer the next question out of what was just thrown away.
    assert service._spreadsheet_ids == {}
    service.resolve_spreadsheet("sales")
    assert len(endpoint.calls) == 3


def test_forgetting_leaves_the_other_spreadsheets_alone():
    service = drive_matching("One", "Two")
    service.resolve_spreadsheet("One")
    service.resolve_spreadsheet("Two")

    service.forget("id-One")

    assert service._spreadsheet_ids == {"Two": "id-Two"}


def test_forgetting_something_never_seen_is_harmless():
    service = drive_matching("One")
    service.resolve_spreadsheet("One")

    service.forget("id-that-was-never-here")

    assert service._spreadsheet_ids == {"One": "id-One"}


# How it is put together


def test_it_uses_the_shared_google_when_given_none():
    from excel_agent.services.google import google_api

    assert DriveService()._google is google_api


def test_it_can_be_given_a_google_of_its_own():
    mine = FakeGoogle()

    assert DriveService(mine)._google is mine


def test_two_services_do_not_share_what_they_remember():
    one, other = DriveService(FakeGoogle()), DriveService(FakeGoogle())

    one._spreadsheet_ids["Sales"] = "id-1"

    assert other._spreadsheet_ids == {}
