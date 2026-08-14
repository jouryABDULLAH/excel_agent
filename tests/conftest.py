"""Shared fixtures.

Four jobs: keep test runs out of LangSmith, point the tools at a workbook the
test owns, keep backups out of the project, and prove the real workbook was
never touched.
"""

import hashlib
import os

# Before excel_agent is imported, and so before LangChain is, because the
# suite would otherwise be sent to LangSmith like any other run.
os.environ["LANGSMITH_TRACING"] = "false"

import pytest  # noqa: E402

from excel_agent import config, workbook  # noqa: E402

# Captured before any test can redirect it, so the guard below watches the
# real file however a test moves the data folder around.
REAL_WORKBOOK = config.WORKBOOK_PATH


@pytest.fixture
def use_workbook(monkeypatch):
    """Return a function making a workbook in tmp the one the tools reach for.

    The tools hand a name to config and get a path back, and config reads the
    data folder at the moment it is asked rather than when it was imported.
    Moving the folder and the default workbook is therefore enough to keep a
    test off the real one, whether it names a workbook or leaves it out.
    """

    def point_at(path):
        monkeypatch.setattr(config, "DATA_DIR", path.parent)
        monkeypatch.setattr(config, "WORKBOOK_PATH", path)
        return path

    return point_at


@pytest.fixture(autouse=True)
def backups_in_tmp(tmp_path, monkeypatch):
    """Send backups to tmp, so no test can write into the project's own folder.

    workbook.py reads BACKUP_DIR from config once, when it is imported, so it
    is workbook's own name for the folder that has to be moved.
    """
    monkeypatch.setattr(workbook, "BACKUP_DIR", tmp_path / "backups")


@pytest.fixture(autouse=True)
def fresh_backup_state():
    """Give each test the backup state of a session that has written nothing.

    Which workbooks have been backed up lives for the life of the process, so
    without this the first test to write would be the only one that ever took
    a backup, and the tests about backups would pass or fail on the order they
    happened to run in.
    """
    workbook._backed_up.clear()
    yield
    workbook._backed_up.clear()


@pytest.fixture(autouse=True)
def sample_untouched():
    """Fail any test that changes the real workbook.

    A test that forgets use_workbook writes to the file the user works in.
    Comparing the contents either side of every test turns that from something
    noticed later into a failure at the point it happens.
    """
    if not REAL_WORKBOOK.exists():
        yield
        return

    before = hashlib.sha256(REAL_WORKBOOK.read_bytes()).hexdigest()
    yield
    after = hashlib.sha256(REAL_WORKBOOK.read_bytes()).hexdigest()

    assert before == after, (
        f"{REAL_WORKBOOK} was modified by this test. Tests must work on a copy "
        "in tmp_path, by way of the use_workbook fixture."
    )