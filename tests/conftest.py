"""Shared fixtures for the test suite.

`tests/` had no conftest before this. test_export_api.py does
`from tests.test_generate_report_api import ...` and works only because pytest
inserts the rootdir on sys.path when it finds no package marker - a conftest here
makes that explicit rather than incidental.

The corpus fixtures are session-scoped: discovery walks tests/data/ once per run
rather than once per parametrised case.
"""

import os

import pytest
from fastapi.testclient import TestClient

import app.api as api
from tests import loading_checks


@pytest.fixture(scope="session")
def data_dir():
    return loading_checks.DATA_ROOT


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture(scope="session")
def baseline():
    return loading_checks.load_baseline()


@pytest.fixture(scope="session")
def inbox_baseline():
    return loading_checks.load_baseline(loading_checks.INBOX_BASELINE_PATH)


def require_corpus(cases, folder):
    """Skip rather than fail when a corpus folder is missing or empty.

    A partial checkout should report zero cases, not a wall of red. The demo path
    is the other way round: scripts/run_test_plan.py --strict turns a missing
    *required* corpus into an error, so an empty folder can't pass for a green run
    on the day. tests/data/inbox/ is exempt from that - being empty is its normal
    state on every machine but the one that filled it.
    """
    if not cases:
        pytest.skip(
            f"no test cases discovered in {folder} - "
            f"corpus missing or empty (this is expected on a partial checkout)"
        )


def case_ids(cases):
    return [c["id"] for c in cases]


def _discover(fn, root):
    return fn(root) if os.path.isdir(root) else []


EXTENSION_CASES = _discover(
    loading_checks.discover_extension_cases, loading_checks.EXTENSIONS_ROOT
)
WORKBOOK_CASES = _discover(
    loading_checks.discover_workbook_cases, loading_checks.WORKBOOKS_ROOT
)
INBOX_CASES = _discover(
    loading_checks.discover_inbox_cases, loading_checks.INBOX_ROOT
)
