"""Test 3 - the inbox.

tests/data/inbox/ is the drop-anything folder: a developer puts a spreadsheet in
and it gets checked, with no naming convention, no manifest and no code change.
See tests/data/inbox/README.md.

Nothing has been declared about these files, so only the folder-independent
invariants apply - it reads, it loads, and the probe's row count matches what
pandas produced. Workbook counts and sheet classes are not asserted because
nobody said what they should be.

The folder's contents are gitignored, so on every machine but the one that filled
it this suite skips. That is normal, not a gap: the two required corpora are what
--strict enforces.
"""

import pytest

from tests import loading_checks
from tests.conftest import INBOX_CASES, case_ids


pytestmark = pytest.mark.skipif(
    not INBOX_CASES,
    reason=(
        "tests/data/inbox/ is empty - drop a .csv/.xls/.xlsx in it to have it "
        "tested (see tests/data/inbox/README.md). Its contents are gitignored, "
        "so empty is the expected state on a fresh clone."
    ),
)


@pytest.mark.parametrize("case", INBOX_CASES, ids=case_ids(INBOX_CASES))
def test_inbox_file_loads(case):
    result = loading_checks.run_batch(case)
    assert result["passed"], loading_checks.describe_failures(result)


def test_unsupported_files_are_reported():
    """A file the harness can't read is listed rather than ignored.

    Dropping `report.xlsx.txt` or a stray .pdf in a batch folder should be visible
    - the failure mode this guards against is a developer believing a file was
    tested when discovery never picked it up.
    """
    ignored = {
        c["id"]: c["expectations"]["ignored_files"]
        for c in INBOX_CASES if c["expectations"].get("ignored_files")
    }
    if not ignored:
        pytest.skip("no unsupported files in the inbox")
    pytest.fail(
        "unsupported files in the inbox were not tested (only .csv/.xls/.xlsx "
        "are read):\n"
        + "\n".join(f"  {k}: {v}" for k, v in ignored.items())
    )


@pytest.mark.parametrize("case", INBOX_CASES, ids=case_ids(INBOX_CASES))
def test_matches_recorded_baseline(case, inbox_baseline):
    """Inbox recordings live in inbox/baseline.json, not the committed one.

    Letting them into tests/data/baseline.json would pin expectations against
    files nobody else has, so every other clone would fail on entries it cannot
    satisfy.
    """
    result = loading_checks.run_batch(case)
    if not result["passed"]:
        pytest.skip("invariants failed; see test_inbox_file_loads for the detail")

    status, drift = loading_checks.baseline_status(result, inbox_baseline)
    if status in ("unrecorded", "stale", "n/a"):
        pytest.skip(f"baseline status: {status}")
    assert not drift, (
        f"{case['id']} drifted from its recorded baseline:\n"
        + "\n".join(f"  {d['field']}: recorded {d['expected']!r}, now {d['actual']!r}"
                    for d in drift)
    )
