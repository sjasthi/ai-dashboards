"""API-level file loading - /api/inspect and /api/analyze-full.

The unit suites (test_file_loading, test_workbook_matrix) drive DataLoader and
workbook_probe directly. This one pushes the same corpus files through the actual
HTTP endpoints the upload screen calls, because several things only exist at that
layer: multipart handling, the temp-directory round trip, and the per-file error
envelope /api/inspect returns instead of failing a whole batch.

The question here is whether the API *agrees* with the unit level, so it runs a
representative subset rather than the whole corpus - one batch per matrix cell
plus a mixed-extension batch. Re-running 105 files over HTTP would add minutes and
find nothing the unit suites don't.

The LLM is stubbed. Real recommendations are non-deterministic and cost money;
what is being tested is that files reach the pipeline intact, not what the model
says about them.
"""

import os

import pytest

from tests import loading_checks
from tests.conftest import EXTENSION_CASES, WORKBOOK_CASES, case_ids, require_corpus


def _one_per_cell():
    """The smallest batch from each matrix cell, so all eight cells are exercised
    over HTTP without uploading the whole corpus."""
    by_cell = {}
    for case in WORKBOOK_CASES:
        cell = case["expectations"].get("cell")
        fmt = case["expectations"].get("fmt")
        if not cell or not case["files"]:
            continue
        size = sum(os.path.getsize(p) for p in case["files"])
        key = (fmt, cell)
        if key not in by_cell or size < by_cell[key][0]:
            by_cell[key] = (size, case)
    return [case for _, case in sorted(by_cell.values(), key=lambda kv: kv[1]["id"])]


def _one_mixed():
    mixed = [c for c in EXTENSION_CASES
             if c["group"] == "extensions-mixed" and c["id"].endswith("csv+xls+xlsx")]
    return mixed[:1]


API_CASES = _one_per_cell() + _one_mixed()


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the AI call with a fixed, schema-shaped response.

    Patched on the module object api imported at startup, mirroring how
    test_generate_report_api patches api.generate_report. The recommendation names
    a file that always exists in the loaded set, so downstream validation has
    something real to resolve against.
    """
    import app.api as api

    def fake(prompt, valid_filenames, **kwargs):
        target = sorted(valid_filenames)[0] if valid_filenames else "unknown.csv"
        return {
            "recommendations": [{
                "rank": 1,
                "report_name": "Stubbed Report",
                "question_answered": "Stubbed for testing.",
                "pattern_used": "RANKING",
                "rationale_bullets": ["Stubbed."],
                "required_operations": [{
                    "operation_type": "groupby",
                    "files_involved": [target],
                    "groupby_columns": [],
                    "aggregations": [],
                }],
                "plotly_config": {
                    "chart_type": "bar", "x_axis": "x", "y_axis": "y",
                    "title": "Stubbed Report",
                },
            }]
        }

    monkeypatch.setattr(api.ai_engine, "get_validated_recommendations", fake)
    return fake


def _upload(paths):
    return [
        ("files", (os.path.basename(p), open(p, "rb"),
                   "application/octet-stream"))
        for p in paths
    ]


def _close(files):
    for _, (_, handle, _) in files:
        handle.close()


def test_corpus_is_present():
    require_corpus(API_CASES, loading_checks.DATA_ROOT)


@pytest.mark.parametrize("case", API_CASES, ids=case_ids(API_CASES))
def test_inspect_agrees_with_the_probe(client, case):
    """/api/inspect must report exactly what workbook_probe reports directly.

    This is the number the upload screen puts in front of the user before they
    commit to an analysis, so a discrepancy here is a lie told in the UI.
    """
    files = _upload(case["files"])
    try:
        response = client.post("/api/inspect", files=files)
    finally:
        _close(files)

    assert response.status_code == 200, response.text
    payload = response.json()["files"]
    assert len(payload) == len(case["files"])

    direct = loading_checks.run_batch(case)
    assert direct["passed"], loading_checks.describe_failures(direct)
    expected = {f["name"]: f for f in direct["observed"]["files"]}

    for reported in payload:
        assert "error" not in reported, f"{reported['name']}: {reported.get('error')}"
        want = expected[reported["name"]]
        assert reported["kind"] == want["kind"]
        assert reported["rows"] == want["probe_rows"], (
            f"{reported['name']}: /api/inspect says {reported['rows']} rows, "
            f"the probe says {want['probe_rows']}"
        )
        if reported["kind"] == "excel":
            assert [s["name"] for s in reported["sheets"]] == \
                   [s["name"] for s in want["sheets"]]


@pytest.mark.parametrize("case", API_CASES, ids=case_ids(API_CASES))
def test_analyze_full_loads_the_same_tables(client, case, stub_llm):
    """/api/analyze-full must build the same table set the unit level does.

    Checked against the session's `tables`, which is the authoritative per-sheet
    map the report endpoint later reads from. If a sheet went missing between
    upload and profiling, this is where it shows.
    """
    import app.api as api

    files = _upload(case["files"])
    try:
        response = client.post("/api/analyze-full", files=files)
    finally:
        _close(files)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"

    direct = loading_checks.run_batch(case)
    expected_tables = set(direct["observed"]["tables"])
    session_tables = set(api.SESSIONS[body["session_id"]]["tables"])
    assert session_tables == expected_tables, (
        "tables built by the API differ from the unit level:\n"
        f"  only via API:  {sorted(session_tables - expected_tables)}\n"
        f"  only via unit: {sorted(expected_tables - session_tables)}"
    )


@pytest.mark.parametrize("case", API_CASES, ids=case_ids(API_CASES))
def test_analyze_full_reports_real_row_counts_per_upload(client, case, stub_llm):
    """The response's `file_profiles` is per *uploaded file*, not per table (it is
    really `file_metadata` - see the note at api.py:433). Its rows/columns are
    backfilled by grouping profiles through `loader.origins`, because a multi-sheet
    workbook yields tables called "Orders (sales).xlsx" that never equal
    "sales.xlsx" by name.

    That grouping is the thing worth testing: get it wrong and every multi-sheet
    upload renders a null row count in the UI.
    """
    files = _upload(case["files"])
    try:
        response = client.post("/api/analyze-full", files=files)
    finally:
        _close(files)

    assert response.status_code == 200, response.text
    reported = {f["name"]: f for f in response.json()["file_profiles"]}

    direct = loading_checks.run_batch(case)
    for observed in direct["observed"]["files"]:
        entry = reported[observed["name"]]
        populated = [s for s in observed["sheets"] if not s["empty"]]
        assert entry["rows"] == observed["probe_rows"], (
            f"{observed['name']}: API reports {entry['rows']} rows, "
            f"the probe says {observed['probe_rows']}"
        )
        assert entry["columns"] == max(s["columns"] for s in populated)


def test_inspect_reports_an_empty_file_without_failing_the_batch(client):
    """A zero-byte file gets a per-file `error` and the rest of the batch still
    comes back - the upload screen shows the problem next to the file rather than
    rejecting everything the user selected."""
    sample = next((c["files"][0] for c in API_CASES if c["files"]), None)
    if sample is None:
        pytest.skip("no corpus files available")

    files = [
        ("files", ("empty.csv", b"", "text/csv")),
        ("files", (os.path.basename(sample), open(sample, "rb"),
                   "application/octet-stream")),
    ]
    try:
        response = client.post("/api/inspect", files=files)
    finally:
        files[1][1][1].close()

    assert response.status_code == 200, response.text
    reported = {f["name"]: f for f in response.json()["files"]}
    assert "error" in reported["empty.csv"]
    assert "error" not in reported[os.path.basename(sample)]
