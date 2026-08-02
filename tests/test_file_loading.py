"""Unit-level file loading: DataLoader and workbook_probe against the fixture corpus.

Every case comes from tests/fixtures/manifest.json and is executed by
loading_checks.run_case, which is also what scripts/run_test_plan.py calls. That
shared path is deliberate: the professor demo and `pytest` must be able to
disagree about presentation but never about what a case asserts.

What this file adds on top of the sweep is the coverage guards at the bottom --
the manifest is data, and data can quietly lose a case. A test plan that promises
the four required Excel topologies should fail if one of them goes missing,
rather than reporting a cheerful green on 49 cases.
"""

import pytest

from tests import loading_checks

# Ordered here so failures read in the same order as the manifest.
CASES = loading_checks.load_cases()


def _params():
    """One pytest param per case, xfail-marked from the manifest.

    strict=True on purpose: if someone fixes the cp1252 ladder or the corrupt
    workbook handling, the case starts passing and pytest reports XPASS as a
    failure. That is the only reliable prompt to delete the xfail flag -- a
    non-strict xfail would go green and the stale flag would live forever.
    """
    for case in CASES:
        marks = []
        if loading_checks.is_xfail(case):
            marks.append(pytest.mark.xfail(
                reason=case.get("known_issue", "known defect"), strict=True,
            ))
        yield pytest.param(case, id=case["id"], marks=marks)


@pytest.mark.parametrize("case", list(_params()))
def test_case(case):
    result = loading_checks.run_case(case)

    if result["status"] == "skipped":
        pytest.skip(result["reason"])

    assert not result["problems"], "\n".join(
        [f"{case['id']}: {case['description']}", ""]
        + [f"  - {p}" for p in result["problems"]]
    )


# ---------------------------------------------------------------- coverage guards

def test_all_four_required_matrix_cells_are_covered():
    """The professor's 2x2, in both Excel formats: 4 cells x 2 = 8 cases."""
    by_cell = {}
    for case in CASES:
        if case.get("group") != "required-matrix":
            continue
        exts = {f.rsplit(".", 1)[-1] for f in case["files"]}
        by_cell.setdefault(case["matrix_cell"], set()).update(exts)

    assert set(by_cell) == {1, 2, 3, 4}, (
        f"required matrix cells present: {sorted(by_cell)}, expected 1-4"
    )
    for cell, exts in sorted(by_cell.items()):
        assert exts == {"xlsx", "xls"}, (
            f"matrix cell {cell} covers {sorted(exts)}, expected both xlsx and xls"
        )


def test_every_extension_the_app_accepts_has_a_case():
    """The UI accepts .csv/.xls/.xlsx, so each must appear as the sole input somewhere.

    Guards against a corpus that only ever tests an extension inside a mixed
    batch, where another file could be carrying the assertion.
    """
    solo = {
        case["files"][0].rsplit(".", 1)[-1]
        for case in CASES
        if len(case["files"]) == 1
    }
    assert {"csv", "xls", "xlsx"} <= solo, f"single-file cases cover only {sorted(solo)}"


def test_both_table_naming_branches_are_asserted():
    """Bare filename vs '<sheet> (<stem>)<ext>' -- both must be pinned by some case."""
    bare = parenthesised = False
    for case in CASES:
        for name in (case["expect"].get("tables") or {}):
            if " (" in name and ")" in name:
                parenthesised = True
            else:
                bare = True
    assert bare, "no case asserts the bare-filename table name"
    assert parenthesised, "no case asserts the parenthesised table name"


def test_known_issues_are_documented_and_flagged_together():
    """An xfail without a known_issue is an unexplained failure; the reverse is a lie."""
    for case in CASES:
        if loading_checks.is_xfail(case):
            assert case.get("known_issue"), (
                f"{case['id']} is xfail but carries no known_issue explaining why"
            )
        elif case.get("known_issue"):
            assert False, (
                f"{case['id']} documents a known_issue but is not marked xfail, "
                "so nothing enforces the defect being real"
            )
