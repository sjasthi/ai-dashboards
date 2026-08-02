"""Test 2 - the workbook/sheet matrix.

The professor's emphasis: do .xlsx and .xls workbooks load correctly across
1-vs-2+ workbooks x 1-vs-many sheets? Eight cells, four per format.

Layout is tests/data/workbooks/<fmt>/<cell>/<example>/, where <cell> names the
shape (1wb-1sheet, 2+wb-multisheet, ...) and each <example> folder is one batch
uploaded together. The cell name is the assertion, so a batch that doesn't match
its folder is a failure - either the code changed or the file was filed wrong, and
both are worth knowing.

Running the same matrix through both formats is the point: .xlsx goes through
openpyxl and .xls through xlrd, two separate probe implementations sharing only
_extent, which carries a format-dependent assumption about what an empty cell
looks like (None vs '').
"""

import pytest

from tests import loading_checks
from tests.conftest import WORKBOOK_CASES, case_ids, require_corpus

REQUIRED_CELLS = {"1wb-1sheet", "1wb-multisheet"}
REQUIRED_FORMATS = {"xls", "xlsx"}


def test_corpus_is_present():
    require_corpus(WORKBOOK_CASES, loading_checks.WORKBOOKS_ROOT)


@pytest.mark.parametrize("case", WORKBOOK_CASES, ids=case_ids(WORKBOOK_CASES))
def test_batch_loads(case):
    """One batch - every workbook in an <example> folder uploaded together.

    Beyond the shared invariants this checks the folder's own claim: the workbook
    count, whether each workbook is single- or multi-sheet, and that the table
    names took the naming branch that shape implies.
    """
    result = loading_checks.run_batch(case)
    assert result["passed"], loading_checks.describe_failures(result)


def test_all_matrix_cells_are_covered():
    """The four cells must exist in both formats.

    Without this the suite could go green having tested only .xlsx, or only the
    single-sheet half - which is exactly the coverage claim being made to the
    professor, so it gets asserted rather than assumed.
    """
    require_corpus(WORKBOOK_CASES, loading_checks.WORKBOOKS_ROOT)
    found = {
        (c["expectations"]["fmt"], c["expectations"]["cell"])
        for c in WORKBOOK_CASES if "cell" in c["expectations"]
    }
    formats = {f for f, _ in found}
    assert formats == REQUIRED_FORMATS, f"missing format tree: {REQUIRED_FORMATS - formats}"

    for fmt in sorted(REQUIRED_FORMATS):
        cells = {c for f, c in found if f == fmt}
        single = {c for c in cells if c.endswith("-1sheet")}
        multi = {c for c in cells if c.endswith("-multisheet")}
        one_wb = {c for c in cells if c.startswith("1wb")}
        many_wb = {c for c in cells if not c.startswith("1wb")}
        assert single and multi, f"{fmt}: needs both 1sheet and multisheet cells, has {sorted(cells)}"
        assert one_wb and many_wb, f"{fmt}: needs both 1wb and 2+wb cells, has {sorted(cells)}"


def test_no_unrecognised_cell_folders():
    """A folder whose name doesn't parse is reported, not skipped in silence -
    otherwise a typo like `2wb-multisheets` would quietly remove coverage."""
    require_corpus(WORKBOOK_CASES, loading_checks.WORKBOOKS_ROOT)
    bad = [c["id"] for c in WORKBOOK_CASES if c["expectations"].get("unrecognised")]
    assert not bad, (
        "cell folders that don't match <n>[+]wb-<1|multi>sheet: " + ", ".join(bad)
    )


def test_no_off_format_files_in_batches():
    """Each batch is filtered to its tree's own format so a stray file can't make a
    1wb folder look like a 2-workbook batch. The filter keeps the matrix honest;
    this reports what it had to ignore, so the corpus gets cleaned rather than
    silently worked around."""
    require_corpus(WORKBOOK_CASES, loading_checks.WORKBOOKS_ROOT)
    strays = {
        c["id"]: c["expectations"]["ignored_files"]
        for c in WORKBOOK_CASES if c["expectations"].get("ignored_files")
    }
    assert not strays, (
        "off-format files found inside workbook batches (they were ignored, but "
        "they contradict the folder they are in):\n"
        + "\n".join(f"  {k}: {v}" for k, v in strays.items())
    )


@pytest.mark.parametrize(
    "case", [c for c in WORKBOOK_CASES if len(c["files"]) > 1],
    ids=case_ids([c for c in WORKBOOK_CASES if len(c["files"]) > 1]),
)
def test_multi_workbook_names_stay_unique(case):
    """The reason the multi-workbook cells exist.

    Several batches in the corpus hold workbooks with identical sheet names -
    budget_2024/budget_2025 both have Q1..Q4, electronics/furniture/office_supplies
    all have Products/Inventory/Sales. Only the (stem) suffix in the generated
    table name keeps those apart; drop it and these collapse onto each other,
    silently losing tables.
    """
    result = loading_checks.run_batch(case)
    unique = [c for c in result["checks"] if c["name"] == "no tables lost to a name collision"]
    assert unique, "collision check did not run"
    assert unique[0]["passed"], (
        f"{case['id']}: expected {unique[0]['expected']}, got {unique[0]['actual']} "
        "- two worksheets landed on the same key and one overwrote the other"
    )
