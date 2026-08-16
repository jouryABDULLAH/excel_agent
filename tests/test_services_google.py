"""Tests for the Google infrastructure the refactor pulled out of sheets.py.

This is the layer that builds clients, retries what is worth retrying, and
turns Google's errors into sentences. None of it knows what a spreadsheet is,
which is the point of it being its own module, so everything here is checked
without one.

Nothing reaches the network: build and get_credentials are replaced, and the
suite wide guard refuses any client this file does not build itself.
"""

import pytest
from googleapiclient.errors import HttpError

from excel_agent.services import google as google_module
from excel_agent.services.google import (
    MAX_ATTEMPTS,
    MAX_BACKOFF,
    RETRY_ON,
    GoogleAPI,
    readable,
)

from fake_google import Request, error

# Taken at import, before the guard in conftest replaces it, so the tests that
# are about building a client can put the real one back.
REAL_SERVICE = GoogleAPI.service


@pytest.fixture
def building(monkeypatch):
    """Let service() run, with a build that hands back a name instead of a client."""
    built: list[tuple] = []

    def build(api, version, credentials=None, cache_discovery=None):
        built.append((api, version, credentials, cache_discovery))
        return f"{api}-{version}-client"

    monkeypatch.setattr(GoogleAPI, "service", REAL_SERVICE)
    monkeypatch.setattr(google_module, "build", build)
    monkeypatch.setattr(google_module, "get_credentials", lambda scopes: "the-credentials")
    return built


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Record the waits instead of serving them, so a retry test is instant."""
    waits: list[float] = []
    monkeypatch.setattr(google_module.time, "sleep", waits.append)
    return waits


# Building the clients


def test_a_client_is_built_once_and_then_reused(building):
    google = GoogleAPI()

    first = google.service("drive", "v3")
    second = google.service("drive", "v3")

    assert first is second
    assert len(building) == 1


def test_the_two_apis_are_different_clients(building):
    google = GoogleAPI()

    assert google.sheets == "sheets-v4-client"
    assert google.drive == "drive-v3-client"
    assert len(building) == 2


def test_two_versions_of_one_api_are_not_confused(building):
    """The key holds the version, so v2 does not answer a request for v3.

    The old sheets.py keyed this cache on the api alone, which meant the first
    version asked for was the one every later caller got.
    """
    google = GoogleAPI()

    assert google.service("drive", "v2") == "drive-v2-client"
    assert google.service("drive", "v3") == "drive-v3-client"
    assert len(building) == 2


def test_a_client_is_built_signed_in_and_without_discovery_over_the_network(building):
    google = GoogleAPI()

    google.drive

    api, version, credentials, cache_discovery = building[0]
    assert (api, version) == ("drive", "v3")
    assert credentials == "the-credentials"
    # The discovery documents ship inside the library. Fetching them would be a
    # network call before any real work, on every process start.
    assert cache_discovery is False


def test_the_scopes_asked_for_are_the_projects_own(building, monkeypatch):
    asked: list = []
    monkeypatch.setattr(
        google_module, "get_credentials", lambda scopes: asked.append(scopes) or "creds"
    )

    GoogleAPI().sheets

    assert asked == [google_module.SCOPES]


def test_two_instances_do_not_share_their_clients(building):
    """The cache is per instance, so a test double cannot leak into the shared one."""
    one, other = GoogleAPI(), GoogleAPI()

    one.drive
    other.drive

    assert len(building) == 2


# Retrying what is worth retrying


def test_a_request_that_works_is_executed_once(no_waiting):
    request = Request(answer={"ok": True})

    assert GoogleAPI().execute(request) == {"ok": True}
    assert request.attempts == 1
    assert no_waiting == []


@pytest.mark.parametrize("status", RETRY_ON)
def test_every_status_worth_retrying_is_retried(status, no_waiting):
    request = Request(answer={"ok": True}, failures=[error(status)])

    assert GoogleAPI().execute(request) == {"ok": True}
    assert request.attempts == 2


@pytest.mark.parametrize("status", (400, 401, 403, 404, 409, 418))
def test_a_request_that_is_wrong_is_not_sent_again(status, no_waiting):
    request = Request(failures=[error(status)] * MAX_ATTEMPTS)

    with pytest.raises(HttpError):
        GoogleAPI().execute(request)

    # It would be just as wrong the second time, and each try costs a call
    # against the quota and a wait the user sits through.
    assert request.attempts == 1
    assert no_waiting == []


def test_it_gives_up_after_the_last_attempt_and_raises_what_google_said(no_waiting):
    request = Request(failures=[error(503, f"attempt {n}") for n in range(MAX_ATTEMPTS)])

    with pytest.raises(HttpError) as refused:
        GoogleAPI().execute(request)

    assert request.attempts == MAX_ATTEMPTS
    # The error raised is the last one, not the first: whoever reads it is
    # being told how the request ended.
    assert f"attempt {MAX_ATTEMPTS - 1}" in str(refused.value)


def test_it_waits_between_tries_and_not_after_the_last_one(no_waiting):
    request = Request(failures=[error(429)] * MAX_ATTEMPTS)

    with pytest.raises(HttpError):
        GoogleAPI().execute(request)

    # Waiting after the final failure would delay the error for nothing.
    assert len(no_waiting) == MAX_ATTEMPTS - 1


def test_each_wait_is_longer_than_the_last_with_a_little_randomness(no_waiting):
    request = Request(failures=[error(500)] * MAX_ATTEMPTS)

    with pytest.raises(HttpError):
        GoogleAPI().execute(request)

    for attempt, waited in enumerate(no_waiting):
        # Doubling, plus up to a second, so several agents that were refused
        # together do not come back in step with each other.
        assert 2**attempt <= waited < 2**attempt + 1
    assert no_waiting == sorted(no_waiting)


def test_the_wait_stops_growing_at_the_cap(monkeypatch, no_waiting):
    """The cap only bites if more attempts are allowed than are today.

    Five attempts means the longest wait is eight seconds plus change, so
    MAX_BACKOFF is never reached as things stand. Raising the attempts is the
    only way to see the cap work, and it is worth knowing it does before
    anybody raises them.
    """
    monkeypatch.setattr(google_module, "MAX_ATTEMPTS", 10)
    request = Request(failures=[error(502)] * 10)

    with pytest.raises(HttpError):
        GoogleAPI().execute(request)

    assert max(no_waiting) <= MAX_BACKOFF
    assert no_waiting[-1] == MAX_BACKOFF


def test_the_longest_wait_today_is_far_short_of_the_cap(no_waiting):
    request = Request(failures=[error(503)] * MAX_ATTEMPTS)

    with pytest.raises(HttpError):
        GoogleAPI().execute(request)

    assert max(no_waiting) < MAX_BACKOFF
    # Five attempts is about fifteen seconds of waiting all told, which is
    # what somebody sits through before a rate limit becomes an error.
    assert sum(no_waiting) < 20


def test_an_error_that_is_not_googles_is_not_retried(no_waiting):
    class Exploding:
        attempts = 0

        def execute(self):
            Exploding.attempts += 1
            raise ValueError("the request itself was built wrong")

    with pytest.raises(ValueError):
        GoogleAPI().execute(Exploding())

    assert Exploding.attempts == 1


def test_an_error_carrying_no_status_is_not_retried(no_waiting):
    """HttpError without a usable response reads as a status of None.

    None is not in RETRY_ON, so it is raised rather than repeated, which is
    the safe way round: a request nobody can classify is not sent again.
    """

    class Odd:
        def execute(self):
            failure = HttpError(None, b"")
            raise failure

    with pytest.raises((HttpError, AttributeError)):
        GoogleAPI().execute(Odd())


def test_the_shared_instance_is_one_object():
    from excel_agent.services.google import google_api

    assert isinstance(google_api, GoogleAPI)
    assert google_api is google_module.google_api


# Saying what went wrong


def test_a_rejected_sign_in_says_how_to_sign_in_again():
    said = readable(error(401))

    assert "token.json" in said
    assert "sign in" in said


def test_a_refusal_repeats_what_google_said():
    said = readable(error(403, "Insufficient permission for this file"))

    # Not always about permission: Drive answers 403 for a query it will not
    # run, so what Google said matters more than a guess made here about why.
    assert "Insufficient permission for this file" in said


def test_something_missing_says_it_may_be_a_matter_of_access():
    said = readable(error(404))

    assert "does not exist" in said
    assert "cannot access it" in said


def test_a_malformed_request_repeats_the_complaint():
    said = readable(error(400, "Invalid requests[0].deleteDimension"))

    assert "malformed" in said
    assert "Invalid requests[0].deleteDimension" in said


@pytest.mark.parametrize("status", (500, 502, 503, 504, 429))
def test_anything_else_is_still_turned_into_a_sentence(status):
    said = readable(error(status, "Backend error"))

    assert said.startswith("Google returned an error")
    assert "Backend error" in said


def test_an_error_with_no_message_still_reads_as_a_sentence():
    said = readable(error(500))

    assert said.endswith(".")
    assert "Google" in said


def test_nothing_a_tool_shows_the_model_is_a_traceback():
    """Every branch returns prose. A tool hands this straight to the model."""
    for status in (400, 401, 403, 404, 429, 500, 418):
        said = readable(error(status, "detail here"))

        assert isinstance(said, str)
        assert "Traceback" not in said
        assert said.strip() == said
        assert len(said.split()) > 3
