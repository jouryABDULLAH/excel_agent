"""Shared fixtures.

Two jobs: point the tools at a workbook the test owns, and prove the real one
was never touched.
"""

import hashlib

import pytest

from excel_agent import workbook
from excel_agent.config import WORKBOOK_PATH

# The functions that reach the workbook through a default argument. Each one
# takes its path last, so replacing the last default is enough to redirect it.
PATH_DEFAULTED = ("load_values", "load_book", "backup_once", "save")


@pytest.fixture
def use_workbook(monkeypatch):
    """Return a function that points the tools at a workbook of your choosing.

    config.WORKBOOK_PATH is read once, when workbook.py is imported, and baked
    into the default arguments of the functions that open the file. Setting the
    constant afterwards therefore changes nothing, so the defaults themselves
    are what gets replaced here.
    """

    def point_at(path):
        for name in PATH_DEFAULTED:
            function = getattr(workbook, name)
            defaults = function.__defaults__ or ()
            monkeypatch.setattr(
                function, "__defaults__", defaults[:-1] + (path,)
            )
        return path

    return point_at


@pytest.fixture(autouse=True)
def fresh_backup_state():
    """Give each test the backup state of a process that has not written yet.

    The flag that makes a backup happen once lives for the life of the process,
    so without this the first test to write would be the only one that ever
    takes a backup, and the test that checks backups would pass or fail on the
    order the tests happened to run in.
    """
    workbook._backup_state["taken"] = False
    yield
    workbook._backup_state["taken"] = False


@pytest.fixture(autouse=True)
def sample_untouched():
    """Fail any test that changes the real workbook.

    The tools reach data/sample.xlsx by default, so a test that forgets to call
    use_workbook writes to the file the user works in. Comparing the contents
    either side of every test turns that from something noticed later into a
    failure at the point it happens.
    """
    if not WORKBOOK_PATH.exists():
        yield
        return

    before = hashlib.sha256(WORKBOOK_PATH.read_bytes()).hexdigest()
    yield
    after = hashlib.sha256(WORKBOOK_PATH.read_bytes()).hexdigest()

    assert before == after, (
        f"{WORKBOOK_PATH} was modified by this test. Tests must work on a copy "
        "in tmp_path, by way of the use_workbook fixture."
    )
