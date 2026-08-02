"""API-level upload tests: /api/inspect and /api/analyze-full over the fixture corpus.

The LLM is stubbed for every test in this module. That is not only about speed and
quota -- a live model returns different recommendations each run, so any assertion
about the response would be either vacuous or flaky. What is worth asserting is
everything around the model: which tables the pipeline built, what it told the
model they were called, the row and column counts echoed back to the UI, and the
status codes for malformed input.

The stub is installed autouse and asserts nothing was skipped: a test in this file
that reaches the network is a bug in the test, not a slow test.
"""

import json
import os

import pytest

import app.api as api
from tests import loading_checks

CASES = loading_checks.load_cases()

# The subset worth driving over HTTP. Every case is already asserted at unit level
# by test_file_loading; repeating all 50 through multipart upload would triple the
# suite's runtime to re-test the same DataLoader call. These groups are the ones
# where the HTTP layer itself does something: status codes, form parsing, the
# per-file error list, and the row counts echoed into file_profiles.
API_GROUPS = {"required-matrix", "extensions", "sheet-selection", "failure-modes"}


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    """Replace the model call with a deterministic recommendation set.

    Mirrors the monkeypatch style in test_generate_report_api.py. The stub records
    what it was called with so tests can assert on the *inputs* the pipeline
    prepared -- the filenames handed to the model are the real subject here, since
    a recommendation naming a table that does not exist is the failure mode this
    whole layer exists to prevent.
    """
    calls = []

    def fake(prompt, valid_filenames, session_id=None, tables=None, **kwargs):
        calls.append({
            "prompt": prompt,
            "valid_filenames": set(valid_filenames),
            "session_id": session_id,
            "tables": dict(tables or {}),
        })
        first = sorted(valid_filenames)[0] if valid_filenames else "unknown.csv"
        return {
            "recommendations": [
                {
                    "rank": i,
                    "report_name": f"Stubbed Report {letter}",
                    # pattern_used, matching models.Recommendation. A stub using the
                    # wrong key would hide exactly the bug this name once caused.
                    "pattern_used": "RANKING",
                    "source_files": [first],
                    "insight": "Stubbed insight.",
                    "plotly_config": {
                        "chart_type": "bar",
                        "x_axis": "product",
                        "y_axis": "units",
                        "title": f"Stubbed {letter}",
                    },
                }
                for i, letter in enumerate("ABC", start=1)
            ]
        }

    monkeypatch.setattr(api.ai_engine, "get_validated_recommendations", fake)
    return calls


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


def upload_files(case):
    """Multipart payload for a case, in the order the manifest lists its files."""
    return [
        ("files", (os.path.basename(path), open(path, "rb"), "application/octet-stream"))
        for path in loading_checks.case_paths(case)
    ]


def form_data(case):
    """The `selections` form field, if the case has one.

    selections_raw wins: it exists precisely for payloads that are not valid JSON
    and therefore cannot survive a json.dumps round trip.
    """
    if case.get("selections_raw") is not None:
        return {"selections": case["selections_raw"]}
    if case.get("selections") is not None:
        return {"selections": json.dumps(case["selections"])}
    return {}


def api_cases():
    for case in CASES:
        if case.get("group") not in API_GROUPS:
            continue
        if "api" not in loading_checks.case_levels(case):
            continue
        marks = []
        if loading_checks.is_xfail(case):
            marks.append(pytest.mark.xfail(
                reason=case.get("known_issue", "known defect"), strict=True,
            ))
        yield pytest.param(case, id=case["id"], marks=marks)


API_CASES = list(api_cases())


@pytest.mark.parametrize("case", API_CASES)
def test_analyze_full(case, client, stub_llm):
    expect = case["expect"]
    absent = loading_checks.missing_files(case)
    if absent:
        pytest.skip(f"fixture not built: {', '.join(absent)}")

    response = client.post("/api/analyze-full", files=upload_files(case),
                           data=form_data(case))

    assert response.status_code == expect["status"], (
        f"{case['id']}: expected {expect['status']}, got {response.status_code} "
        f"- {response.text[:300]}"
    )

    if expect["status"] != 200:
        wanted = expect.get("detail_contains")
        if wanted:
            assert wanted in str(response.json().get("detail", "")), (
                f"{case['id']}: detail {response.json().get('detail')!r} "
                f"does not mention {wanted!r}"
            )
        return

    body = response.json()
    assert body["status"] == "complete"

    # The tables the pipeline built are what the model was told about, and they
    # must match the manifest exactly - including the naming rules, since a
    # recommendation referring to a table name that does not exist is unusable.
    assert stub_llm, f"{case['id']}: the LLM stub was never called"
    told = stub_llm[-1]["valid_filenames"]
    assert told == set(expect["tables"]), (
        f"{case['id']}: model was told {sorted(told)}, "
        f"manifest expects {sorted(expect['tables'])}"
    )

    built = {name: df for name, df in stub_llm[-1]["tables"].items()}
    actual_shapes = {
        name: {"rows": len(df), "columns": len(df.columns)} for name, df in built.items()
    }
    assert actual_shapes == expect["tables"], (
        f"{case['id']}: shapes {actual_shapes} != {expect['tables']}"
    )

    # The session must survive the request: /api/generate-report reads its tables
    # back out, and worksheets never existed as files on disk.
    assert body["session_id"] in api.SESSIONS
    assert set(api.SESSIONS[body["session_id"]]["tables"]) == set(expect["tables"])


@pytest.mark.parametrize("case", API_CASES)
def test_inspect(case, client):
    expect = case["expect"]
    absent = loading_checks.missing_files(case)
    if absent:
        pytest.skip(f"fixture not built: {', '.join(absent)}")
    if expect.get("inspect") is None:
        pytest.skip("case does not pin inspect output")

    response = client.post("/api/inspect", files=upload_files(case))

    # /api/inspect is 200 even for files it cannot read: the upload screen shows
    # the problem next to the file rather than rejecting the whole batch.
    assert response.status_code == 200, response.text
    entries = response.json()["files"]
    assert len(entries) == len(expect["inspect"])

    problems = loading_checks._compare_inspect(entries, expect["inspect"])
    assert not problems, "\n".join([case["id"], ""] + [f"  - {p}" for p in problems])


# ------------------------------------------------------------------ HTTP-only paths

def test_analyze_full_rejects_empty_upload(client):
    response = client.post("/api/analyze-full", files=[])
    assert response.status_code == 422  # FastAPI rejects the missing required field


def test_inspect_reports_bad_file_without_failing_the_batch(client):
    """A broken file must not cost the user their good ones.

    This is the behavioural difference between the two endpoints, and it is worth
    a dedicated test: /api/inspect degrades per file, /api/analyze-full refuses
    the request.
    """
    good = loading_checks.find_fixture("m1_one_book_one_sheet.xlsx")
    bad = loading_checks.find_fixture("zero_byte.xlsx")
    files = [
        ("files", ("m1_one_book_one_sheet.xlsx", open(good, "rb"), "application/octet-stream")),
        ("files", ("zero_byte.xlsx", open(bad, "rb"), "application/octet-stream")),
    ]
    response = client.post("/api/inspect", files=files)

    assert response.status_code == 200
    entries = {e["name"]: e for e in response.json()["files"]}
    assert entries["m1_one_book_one_sheet.xlsx"]["rows"] == 4
    assert entries["zero_byte.xlsx"]["kind"] == "unknown"
    assert entries["zero_byte.xlsx"]["error"]


def test_analyze_full_refuses_the_batch_when_one_file_is_empty(client):
    good = loading_checks.find_fixture("m1_one_book_one_sheet.xlsx")
    bad = loading_checks.find_fixture("zero_byte.xlsx")
    files = [
        ("files", ("m1_one_book_one_sheet.xlsx", open(good, "rb"), "application/octet-stream")),
        ("files", ("zero_byte.xlsx", open(bad, "rb"), "application/octet-stream")),
    ]
    response = client.post("/api/analyze-full", files=files)

    assert response.status_code == 400
    assert "empty" in str(response.json()["detail"]).lower()


def test_selection_naming_reaches_the_model(client, stub_llm):
    """One sheet of many collapses the table name, and the model must see that name.

    The unit test pins DataLoader's output; this pins that nothing between the
    loader and the prompt renames it back.
    """
    path = loading_checks.find_fixture("m3_one_book_many_sheets.xlsx")
    files = [("files", ("m3_one_book_many_sheets.xlsx", open(path, "rb"),
                        "application/octet-stream"))]
    data = {"selections": json.dumps({"m3_one_book_many_sheets.xlsx": ["Regions"]})}

    response = client.post("/api/analyze-full", files=files, data=data)

    assert response.status_code == 200
    assert stub_llm[-1]["valid_filenames"] == {"m3_one_book_many_sheets.xlsx"}


def test_row_counts_echoed_to_the_frontend_are_real(client):
    """file_profiles carries the counts the UI renders, per workbook, not per sheet.

    A multi-sheet workbook yields profiles named "Orders (sales).xlsx", which never
    equals "sales.xlsx"; the counts are grouped back by origin. Before that fix the
    UI reported a hardcoded 500 rows for every file.
    """
    path = loading_checks.find_fixture("m3_one_book_many_sheets.xlsx")
    files = [("files", ("m3_one_book_many_sheets.xlsx", open(path, "rb"),
                        "application/octet-stream"))]

    response = client.post("/api/analyze-full", files=files)

    assert response.status_code == 200
    profiles = {p["name"]: p for p in response.json()["file_profiles"]}
    # 4 + 3 + 2 across the three sheets.
    assert profiles["m3_one_book_many_sheets.xlsx"]["rows"] == 9
    assert profiles["m3_one_book_many_sheets.xlsx"]["columns"] == 4
