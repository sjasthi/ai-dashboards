"""API-level usage tracking - the endpoints in api.py writing to telemetry.

tests/test_telemetry.py covers the storage module on its own. This one covers the
wiring: that hitting an endpoint produces the event it should, that the event
carries the props the home page and the debugging story depend on, and - the check
that matters most - that a broken telemetry layer cannot fail a user request.

Every test runs against a tmp_path database via the autouse fixture in conftest.
"""

import io
import json
import os

import pytest

import app.api as api
from app.data import telemetry
from tests.conftest import EXTENSION_CASES
from tests.test_upload_api import stub_llm  # noqa: F401  (used as a fixture)


CLIENT_HEADER = {"X-Client-Id": "test-client-1"}


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


def _events(name=None):
    return telemetry.recent_events(limit=100, event=name)


def _one_of(name):
    found = _events(name)
    assert found, f"no {name!r} event was recorded"
    return found[0]


def _small_csv(name="orders.csv"):
    return [("files", (name, io.BytesIO(b"a,b\n1,2\n3,4\n"), "text/csv"))]


def _two_sheet_workbook(name="sales.xlsx"):
    """A real .xlsx with two populated sheets, built in memory. The sheet columns
    can only be exercised by a genuine workbook - a CSV has no sheets to count."""
    import pandas as pd

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(
            writer, sheet_name="Orders", index=False)
        pd.DataFrame({"c": [5, 6], "d": [7, 8]}).to_excel(
            writer, sheet_name="Items", index=False)
    buffer.seek(0)
    return [("files", (name, buffer, "application/octet-stream"))]


def _corpus_csv():
    """A real corpus file, so probe results are genuine rather than synthetic."""
    for case in EXTENSION_CASES:
        if case["files"] and case["files"][0].lower().endswith(".csv"):
            return case["files"][0]
    return None


# ============================================================================
# Telemetry must never fail a user request
# ============================================================================

@pytest.fixture
def exploding_telemetry(monkeypatch):
    """Make every telemetry write raise, the way a corrupt database would."""
    def boom(*args, **kwargs):
        raise RuntimeError("telemetry is broken")

    monkeypatch.setattr(api.telemetry, "log_event", boom)
    monkeypatch.setattr(api.telemetry, "record_file", boom)
    monkeypatch.setattr(api.telemetry, "record_report", boom)


def test_inspect_still_succeeds_when_telemetry_raises(client, exploding_telemetry):
    # The whole design rests on this. telemetry.log_event swallows internally, so
    # this test bypasses that guard entirely and asserts the endpoint survives a
    # telemetry layer that raises where nothing catches it.
    response = client.post("/api/inspect", files=_small_csv(), headers=CLIENT_HEADER)
    assert response.status_code == 200
    assert response.json()["files"][0]["rows"] == 2


def test_analyze_still_succeeds_when_telemetry_raises(client, stub_llm, exploding_telemetry):
    response = client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)
    assert response.status_code == 200
    assert response.json()["status"] == "complete"


def test_events_endpoint_still_succeeds_when_telemetry_raises(client, exploding_telemetry):
    response = client.post(
        "/api/events",
        json={"events": [{"event": "report_viewed", "props": {"letter": "A"}}]},
        headers=CLIENT_HEADER,
    )
    assert response.status_code == 200


def test_stats_still_answers_when_telemetry_raises(client, monkeypatch):
    def boom():
        raise RuntimeError("telemetry is broken")

    monkeypatch.setattr(api.telemetry, "stats", boom)

    # The home page's only backend dependency. It degrades to zeros rather than
    # 500ing, and says so with `available: false` so the UI can tell the difference
    # between "no usage yet" and "the counters are down".
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.json()["users"] == 0
    assert response.json()["available"] is False


# ============================================================================
# /api/inspect
# ============================================================================

def test_inspect_records_the_batch_shape(client):
    path = _corpus_csv()
    files = _small_csv() if not path else [
        ("files", (os.path.basename(path), open(path, "rb"), "text/csv"))
    ]
    assert client.post("/api/inspect", files=files, headers=CLIENT_HEADER).status_code == 200

    event = _one_of("files_inspected")
    assert event["client_id"] == "test-client-1"
    assert event["props"]["submitted"] == 1
    assert event["props"]["inspected"] == 1
    assert event["props"]["ext_mix"] == {".csv": 1}
    assert event["props"]["failed"] == 0
    assert event["props"]["duration_ms"] >= 0


def test_inspect_counts_an_unreadable_file_without_failing_the_batch(client):
    files = _small_csv() + [("files", ("empty.csv", io.BytesIO(b""), "text/csv"))]
    assert client.post("/api/inspect", files=files, headers=CLIENT_HEADER).status_code == 200

    props = _one_of("files_inspected")["props"]
    assert props["submitted"] == 2
    assert props["failed"] == 1  # the empty one, reported per-file rather than as a 400


def test_inspect_works_without_a_client_id_header(client):
    # curl, an older cached bundle, or a browser with localStorage blocked. The
    # event is still recorded; it just doesn't count toward distinct browsers.
    assert client.post("/api/inspect", files=_small_csv()).status_code == 200
    assert _one_of("files_inspected")["client_id"] is None
    assert telemetry.stats()["clients"] == 0


# ============================================================================
# /api/analyze-full
# ============================================================================

def test_analyze_records_started_and_completed(client, stub_llm):
    response = client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    started = _one_of("analysis_started")
    completed = _one_of("analysis_completed")
    assert started["session_id"] == session_id
    assert completed["session_id"] == session_id
    assert completed["props"]["table_count"] == 1
    assert completed["props"]["total_rows"] == 2
    assert completed["props"]["recommendation_count"] == 1
    assert completed["props"]["patterns"] == {"RANKING": 1}
    assert completed["props"]["duration_ms"] >= 0


def test_analyze_writes_one_file_row_per_upload(client, stub_llm):
    files = _small_csv("a.csv") + _small_csv("b.csv")
    assert client.post("/api/analyze-full", files=files, headers=CLIENT_HEADER).status_code == 200

    rows = telemetry.recent_files()
    assert len(rows) == 2
    assert {r["ext"] for r in rows} == {".csv"}
    assert all(r["load_ok"] == 1 for r in rows)
    assert all(r["rows"] == 2 for r in rows)


def test_analyze_never_stores_a_filename_in_plaintext(client, stub_llm):
    assert client.post(
        "/api/analyze-full", files=_small_csv("payroll_confidential.csv"),
        headers=CLIENT_HEADER,
    ).status_code == 200

    row = telemetry.recent_files()[0]
    assert row["name_hash"] != "payroll_confidential.csv"
    assert row["name_hash"] == telemetry.hash_name("payroll_confidential.csv")


def test_analyze_records_whether_sheets_were_deselected(client, stub_llm):
    # Whether the sheet picker earns its complexity is one of the questions this
    # phase exists to answer, so "no selections sent" and "selections sent" have to
    # be distinguishable rather than both looking like a default.
    assert client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER).status_code == 200
    assert _one_of("analysis_started")["props"]["has_selections"] is False


def test_analyze_records_the_workbooks_own_sheet_count(client, stub_llm):
    # The two columns answer different questions and must not collapse into one:
    # sheet_count is how big the workbook is, sheets_selected is how much of it the
    # picker asked for. Only one of two sheets is selected here, so a row reporting
    # the same number twice means the count is being taken after the filter.
    response = client.post(
        "/api/analyze-full",
        files=_two_sheet_workbook(),
        data={"selections": json.dumps({"sales.xlsx": ["Orders"]})},
        headers=CLIENT_HEADER,
    )
    assert response.status_code == 200, response.text

    row = telemetry.recent_files()[0]
    assert row["kind"] == "excel"
    assert row["sheet_count"] == 2
    assert row["sheets_selected"] == 1


def test_analyze_records_a_sheet_count_when_no_selection_was_sent(client, stub_llm):
    # No `selections` field at all - every sheet is analysed. The count is still
    # recorded, but sheets_selected stays null rather than being backfilled from it:
    # "the client never offered a choice" is not the same fact as "the user chose
    # both", and only the null keeps them apart.
    assert client.post(
        "/api/analyze-full", files=_two_sheet_workbook(), headers=CLIENT_HEADER,
    ).status_code == 200

    row = telemetry.recent_files()[0]
    assert row["sheet_count"] == 2
    assert row["sheets_selected"] is None


def test_analyze_records_a_csv_as_having_no_sheets(client, stub_llm):
    # Null, not 1. A CSV has no worksheets at all, which is a different shape from
    # a single-sheet workbook, and the ext column already says which is which.
    assert client.post(
        "/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER,
    ).status_code == 200

    row = telemetry.recent_files()[0]
    assert row["kind"] == "csv"
    assert row["sheet_count"] is None


def test_analyze_failure_records_the_error_class_only(client, monkeypatch):
    def explode(*args, **kwargs):
        raise ValueError("column 'salary' in payroll_confidential.csv is bad")

    monkeypatch.setattr(api.ai_engine, "get_validated_recommendations", explode)
    response = client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)
    assert response.status_code == 502

    props = _one_of("analysis_failed")["props"]
    assert props["error_type"] == "ValueError"
    # The message could name a column or a file; only the class is kept.
    assert "salary" not in str(props)
    assert "payroll_confidential" not in str(props)


def test_stubbed_llm_leaves_provider_fields_empty_without_breaking_the_event(client, stub_llm):
    # The stub takes **kwargs and never fills `meta`. api.py reads it with .get(),
    # so the analysis still completes and the event simply carries nulls.
    assert client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER).status_code == 200
    props = _one_of("analysis_completed")["props"]
    assert "llm_provider" in props
    assert props["llm_provider"] is None


# ============================================================================
# POST /api/events
# ============================================================================

def test_events_accepts_whitelisted_names(client):
    response = client.post("/api/events", json={"events": [
        {"event": "report_viewed", "session_id": "s1", "props": {"letter": "A"}},
        {"event": "tab_changed", "props": {"tab": "reports"}},
    ]}, headers=CLIENT_HEADER)

    assert response.json() == {"accepted": 2, "rejected": []}
    assert _one_of("report_viewed")["props"]["letter"] == "A"


def test_events_drops_unknown_names_without_failing_the_batch(client):
    response = client.post("/api/events", json={"events": [
        {"event": "report_viewed"},
        {"event": "not_a_real_event"},
    ]}, headers=CLIENT_HEADER)

    # A stale bundle loses the one renamed event, not its whole batch.
    assert response.json() == {"accepted": 1, "rejected": ["not_a_real_event"]}
    assert len(_events("not_a_real_event")) == 0


def test_events_rejects_an_oversized_batch(client):
    response = client.post("/api/events", json={
        "events": [{"event": "report_viewed"}] * 51
    }, headers=CLIENT_HEADER)
    assert response.status_code == 422


def test_events_rejects_an_empty_batch(client):
    assert client.post("/api/events", json={"events": []}).status_code == 422


def test_events_truncates_long_prop_values(client):
    client.post("/api/events", json={"events": [
        {"event": "report_viewed", "props": {"letter": "A" * 5000}}
    ]}, headers=CLIENT_HEADER)
    assert len(_one_of("report_viewed")["props"]["letter"]) == api.MAX_PROP_CHARS


def test_events_caps_the_number_of_props(client):
    client.post("/api/events", json={"events": [
        {"event": "report_viewed", "props": {f"k{i}": i for i in range(100)}}
    ]}, headers=CLIENT_HEADER)
    assert len(_one_of("report_viewed")["props"]) == api.MAX_EVENT_PROPS


def test_a_prop_named_like_a_parameter_does_not_break_the_endpoint(client):
    # props come from the browser. Routing them through track()'s **kwargs would
    # make a prop called "client" or "session" collide with the parameter of the
    # same name and raise TypeError before telemetry's guard could swallow it.
    response = client.post("/api/events", json={"events": [
        {"event": "report_viewed", "props": {"client": "x", "session": "y", "event": "z"}}
    ]}, headers=CLIENT_HEADER)

    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert _one_of("report_viewed")["props"]["client"] == "x"


# ============================================================================
# GET /api/stats
# ============================================================================

def test_visit_started_is_an_accepted_event(client):
    response = client.post("/api/events", json={"events": [{"event": "visit_started"}]},
                           headers=CLIENT_HEADER)
    assert response.json() == {"accepted": 1, "rejected": []}


def test_user_activated_is_an_accepted_event(client):
    response = client.post("/api/events", json={"events": [{"event": "user_activated"}]},
                           headers=CLIENT_HEADER)
    assert response.json() == {"accepted": 1, "rejected": []}


def test_a_second_visit_from_the_same_browser_counts_again(client):
    for _ in range(2):
        client.post("/api/events", json={"events": [{"event": "visit_started"}]},
                    headers=CLIENT_HEADER)

    body = client.get("/api/stats").json()
    assert body["visits"] == 2   # two sittings
    assert body["clients"] == 1  # one browser


def test_browsing_without_analysing_is_a_visit_but_not_a_user(client):
    # Someone opens the landing page, reads it, clicks around, and leaves. The
    # headline number must not move.
    client.post("/api/events", json={"events": [
        {"event": "visit_started"}, {"event": "tab_changed", "props": {"tab": "reports"}},
    ]}, headers=CLIENT_HEADER)

    body = client.get("/api/stats").json()
    assert body["visits"] == 1
    assert body["users"] == 0


def test_analysing_alone_does_not_count_a_user(client, stub_llm):
    # The server sees the analysis, but it cannot know whether this browser had
    # already run one earlier in the same sitting - so the count comes from the
    # browser's user_activated event, never inferred from the upload itself.
    client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)

    body = client.get("/api/stats").json()
    assert body["files_processed"] == 1
    assert body["users"] == 0


def test_a_full_visit_then_analysis_counts_one_user(client, stub_llm):
    client.post("/api/events", json={"events": [{"event": "visit_started"}]},
                headers=CLIENT_HEADER)
    client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)
    client.post("/api/events", json={"events": [{"event": "user_activated"}]},
                headers=CLIENT_HEADER)
    # A second analysis in the same sitting fires no further activation.
    client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)

    body = client.get("/api/stats").json()
    assert body["visits"] == 1
    assert body["users"] == 1
    assert body["files_processed"] == 2


def test_stats_is_zeroed_before_anything_happens(client):
    body = client.get("/api/stats").json()
    assert body["visits"] == 0
    assert body["users"] == 0
    assert body["clients"] == 0
    assert body["files_processed"] == 0
    assert body["reports_built"] == 0
    assert body["ext_breakdown"] == {}
    assert body["daily"] == []


def test_stats_reflects_a_real_run(client, stub_llm):
    client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)

    body = client.get("/api/stats").json()
    assert body["sessions"] == 1
    assert body["files_processed"] == 1
    assert body["ext_breakdown"] == {".csv": 1}


def test_stats_counts_two_browsers_as_two_clients(client, stub_llm):
    client.post("/api/analyze-full", files=_small_csv(), headers={"X-Client-Id": "browser-a"})
    client.post("/api/analyze-full", files=_small_csv(), headers={"X-Client-Id": "browser-b"})
    client.post("/api/analyze-full", files=_small_csv(), headers={"X-Client-Id": "browser-a"})

    body = client.get("/api/stats").json()
    assert body["clients"] == 2    # the repeat run is not a third browser
    assert body["sessions"] == 3   # but it is a third session


def test_stats_matches_what_the_module_reports(client, stub_llm):
    client.post("/api/analyze-full", files=_small_csv(), headers=CLIENT_HEADER)
    assert client.get("/api/stats").json() == telemetry.stats()
