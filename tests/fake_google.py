"""A Google API client built by hand, so the service layer can be tested.

The real client is a stack of dynamically built objects ending in a request
that knows how to execute itself. Only that shape matters to the code under
test: something with .files().list(**kwargs) or .spreadsheets().get(**kwargs)
that hands back an object with .execute(). This is that shape, and it records
what it was asked for so a test can look.
"""

from googleapiclient.errors import HttpError


class Response:
    """The bit of an httplib2 response that HttpError reads."""

    def __init__(self, status: int, reason: str = "Something went wrong"):
        self.status = status
        self.reason = reason


def error(status: int, message: str | None = None) -> HttpError:
    """An HttpError the way googleapiclient raises one.

    A message goes in as the JSON body Google really sends, because that is
    where HttpError.reason reads it from, and readable() shows it to the model.
    """
    content = (
        f'{{"error": {{"code": {status}, "message": "{message}"}}}}'.encode()
        if message
        else b""
    )
    return HttpError(Response(status), content)


class Request:
    """One prepared call, which fails a given number of times before working."""

    def __init__(self, answer=None, failures=()):
        self.answer = answer if answer is not None else {}
        self.failures = list(failures)
        self.attempts = 0

    def execute(self):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.answer


class Endpoint:
    """Something with methods that return requests, recording every call.

    Given a dict of answers it hands back the one matching the method asked
    for, so files().list() and spreadsheets().get() can be set up separately.
    """

    def __init__(self, answers=None, failures=None, inside=None):
        self.answers = answers or {}
        self.failures = failures or {}
        # The real client nests: spreadsheets().values().batchUpdate(). A name
        # given here is another endpoint rather than a call, so a test can say
        # what values() answers separately from what spreadsheets() does.
        self.inside = inside if inside is not None else {"values": None}
        self.calls: list[tuple[str, dict]] = []
        self.requests: list[Request] = []

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)

        if method in self.inside:
            if self.inside[method] is None:
                self.inside[method] = Endpoint(self.answers, self.failures, inside={})
            return lambda: self.inside[method]

        def call(**arguments):
            self.calls.append((method, arguments))
            request = Request(
                self.answers.get(method, {}),
                self.failures.get(method, ()),
            )
            self.requests.append(request)
            return request

        return call

    @property
    def asked(self) -> dict:
        """The arguments of the last call made."""
        return self.calls[-1][1]


class FakeGoogle:
    """A stand in for GoogleAPI that executes without a network.

    Holds one Endpoint for Drive's files() and one for Sheets' spreadsheets(),
    so a test can say what Google answers and then read back what was asked.
    """

    def __init__(self, files=None, spreadsheets=None):
        self.files_endpoint = files or Endpoint()
        self.spreadsheets_endpoint = spreadsheets or Endpoint()
        self.executed: list[Request] = []

    @property
    def drive(self):
        outer = self

        class Drive:
            def files(self):
                return outer.files_endpoint

        return Drive()

    @property
    def sheets(self):
        outer = self

        class Sheets:
            def spreadsheets(self):
                return outer.spreadsheets_endpoint

        return Sheets()

    def execute(self, request):
        self.executed.append(request)
        return request.execute()
