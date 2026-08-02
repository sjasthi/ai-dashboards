"""API-level tests for usage tracking: /api/stats, /api/events, and instrumentation.

The point of these tests is not that counters increment -- test_telemetry covers
the counting. It is that instrumenting the request path did not change the request
path: same status codes, same bodies, and no new way for a request to fail.

The last test is the one that matters most. It makes every telemetry call raise and
asserts the app still serves reports, because a user losing their analysis to a
bookkeeping error would be a far worse bug than a missing counter.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.data import telemetry
from tests.test_generate_report_api import make_session


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_DB", str(tmp_path / "usage.db"))
    monkeypatch.delenv("TELEMETRY_STORE_NAMES", raising=False)
    telemetry._initialised.clear()
    return str(tmp_path / "usage.db")


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


def db_rows(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ /api/stats

def test_stats_is_available_before_anything_is_recorded(client):
    """The home page must render on a fresh install."""
    body = client.get("/api/stats").json()

    assert body["available"] is False
    assert body["users"] == 0
    assert body["files_processed"] == 0
    assert body["reports_built"] == 0
    assert body["daily"] == []


def test_stats_exposes_no_identifying_information(client, temp_db):
    """Public endpoint, so it must be counts only.

    A filename leaking here would be a privacy bug, not a cosmetic one.
    """
    telemetry.record_file(session_id="s1", client_id="c1",
                          name="Q3 confidential salaries.xlsx", ext=".xlsx")

    body = client.get("/api/stats").json()
    serialised = str(body)

    assert "confidential" not in serialised
    assert "salaries" not in serialised
    assert "c1" not in serialised
    assert "s1" not in serialised
    assert body["files_processed"] == 1


def test_stats_reflects_recorded_activity(client, temp_db):
    telemetry.record_file(session_id="s1", client_id="c1", ext=".xlsx")
    telemetry.record_report(session_id="s1", client_id="c1", letter="A",
                            pattern="RANKING")

    body = client.get("/api/stats").json()

    assert body["available"] is True
    assert body["users"] == 1
    assert body["files_processed"] == 1
    assert body["reports_built"] == 1
    assert body["ext_breakdown"] == {".xlsx": 1}


def test_stats_survives_a_restart(client, temp_db):
    """The whole reason for using SQLite instead of a module-level counter.

    A fresh TestClient is a fresh app instance over the same database file, which
    is what a uvicorn restart amounts to for this data.
    """
    telemetry.record_file(session_id="s1", client_id="c1", ext=".csv")
    telemetry.record_report(session_id="s1", client_id="c1", letter="A")
    api.SESSIONS.clear()  # in-memory state is gone, as it would be after a restart

    body = TestClient(api.app).get("/api/stats").json()

    assert body["files_processed"] == 1
    assert body["reports_built"] == 1


# ------------------------------------------------------------------ /api/events

def test_events_records_a_known_event(client, temp_db):
    response = client.post("/api/events", json={
        "event": "compare_all_opened",
        "props": {"letters": ["A", "B"]},
        "session_id": "s1",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    assert db_rows(temp_db, "SELECT event FROM events")[0][0] == "compare_all_opened"


def test_events_attributes_to_the_client_id_header(client, temp_db):
    client.post("/api/events", json={"event": "tab_changed"},
                headers={"X-Client-Id": "browser-abc"})

    assert db_rows(temp_db, "SELECT client_id FROM events")[0][0] == "browser-abc"


def test_events_accepts_an_unknown_name_without_erroring(client, temp_db):
    """A frontend typo should be visible in the data, not a 4xx the client ignores."""
    response = client.post("/api/events", json={"event": "made_up_name"})

    assert response.status_code == 200
    assert db_rows(temp_db, "SELECT event FROM events")[0][0] == \
        "unknown_event:made_up_name"


def test_events_rejects_an_oversized_event_name(client):
    """The only client-writable path into the database has to be bounded."""
    response = client.post("/api/events", json={"event": "x" * 500})

    assert response.status_code == 422


def test_client_id_is_truncated_not_trusted(client, temp_db):
    client.post("/api/events", json={"event": "tab_changed"},
                headers={"X-Client-Id": "z" * 500})

    stored = db_rows(temp_db, "SELECT client_id FROM events")[0][0]
    assert len(stored) == api.MAX_CLIENT_ID_LEN


def test_a_request_without_the_header_is_still_recorded(client, temp_db):
    """curl, older clients and the tests send no header; that is not an error."""
    response = client.post("/api/events", json={"event": "tab_changed"})

    assert response.status_code == 200
    assert db_rows(temp_db, "SELECT client_id FROM events")[0][0] is None


# ------------------------------------------------------- instrumented endpoints

def test_generate_report_records_a_report_row(client, temp_db, monkeypatch):
    make_session(monkeypatch, session_id="s1")

    response = client.post("/api/generate-report",
                           json={"session_id": "s1", "report_type": "A"},
                           headers={"X-Client-Id": "c1"})

    assert response.status_code == 200
    letter, pattern, ok, client_id = db_rows(
        temp_db, "SELECT letter, pattern, ok, client_id FROM reports")[0]
    assert (letter, ok, client_id) == ("A", 1, "c1")
    assert pattern  # carried through from the recommendation


def test_a_cached_report_is_not_counted_twice(client, temp_db, monkeypatch):
    """The browser prefetches every report, so duplicate requests are routine.

    Counting the cache hit would inflate "reports built" every time the prefetcher
    raced a user's click.
    """
    make_session(monkeypatch, session_id="s1")

    first = client.post("/api/generate-report",
                        json={"session_id": "s1", "report_type": "A"})
    second = client.post("/api/generate-report",
                         json={"session_id": "s1", "report_type": "A"})

    assert first.status_code == second.status_code == 200
    assert db_rows(temp_db, "SELECT COUNT(*) FROM reports")[0][0] == 1
    assert telemetry.stats()["reports_built"] == 1

    cache_hits = db_rows(
        temp_db, "SELECT COUNT(*) FROM events WHERE event = 'report_generated'")
    assert cache_hits[0][0] == 2  # both requests logged as events


def test_a_failed_report_is_recorded_but_not_counted_as_built(client, temp_db):
    api.SESSIONS["s1"] = {"recommendations": {"recommendations": []}, "tables": {}}

    response = client.post("/api/generate-report",
                           json={"session_id": "s1", "report_type": "A"})

    assert response.status_code >= 400
    ok, error_type = db_rows(temp_db, "SELECT ok, error_type FROM reports")[0]
    assert ok == 0
    assert error_type
    assert telemetry.stats()["reports_built"] == 0


# ------------------------------------------------------------------ non-fatal

def test_telemetry_failure_does_not_break_report_generation(client, monkeypatch):
    """The guarantee the whole design rests on.

    Every telemetry entry point raises. The request must still succeed with an
    unchanged body -- if this test ever fails, telemetry has become load-bearing
    and that is a bug regardless of what else works.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("telemetry is down")

    monkeypatch.setattr(telemetry, "log_event", explode)
    monkeypatch.setattr(telemetry, "record_report", explode)
    monkeypatch.setattr(telemetry, "record_file", explode)
    make_session(monkeypatch, session_id="s1")

    response = client.post("/api/generate-report",
                           json={"session_id": "s1", "report_type": "A"})

    assert response.status_code == 200
    assert response.json()["report_type"] == "A"
    assert response.json()["rows"]


def test_stats_still_answers_when_telemetry_raises(client, monkeypatch):
    """Empty tiles beat a home page that will not load."""
    def explode(*args, **kwargs):
        raise RuntimeError("telemetry is down")

    monkeypatch.setattr(telemetry, "stats", explode)

    response = client.get("/api/stats")

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["users"] == 0
