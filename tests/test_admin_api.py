"""Tests for the developer-only saved-report endpoints.

Two properties matter more than the happy path.

The gate must be closed by default. With ADMIN_TOKEN unset every route answers
404 -- not 401 -- so an unconfigured deployment does not confirm the routes exist.

Persistence must be off by default. Report bundles hold the user's actual rows,
so nothing is written unless SAVE_REPORT_HISTORY says so, and the flag is honoured
inside telemetry.save_report rather than at the call site where it could be
forgotten.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.data import telemetry
from tests.test_generate_report_api import make_session

TOKEN = "test-admin-token-abc123"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_DB", str(tmp_path / "usage.db"))
    monkeypatch.delenv("SAVE_REPORT_HISTORY", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    telemetry._initialised.clear()
    return str(tmp_path / "usage.db")


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    return {"X-Admin-Token": TOKEN}


@pytest.fixture
def history_on(monkeypatch):
    monkeypatch.setenv("SAVE_REPORT_HISTORY", "true")


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


ADMIN_ROUTES = [
    "/api/admin/reports",
    "/api/admin/reports/1",
    "/api/admin/events",
]


# ------------------------------------------------------------------ the gate

@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_routes_are_404_when_no_token_is_configured(client, route):
    """Unconfigured means invisible. A 401 here would confirm the route is real."""
    assert client.get(route).status_code == 404


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_a_supplied_token_cannot_conjure_access(client, route):
    """With ADMIN_TOKEN unset, no header value works -- including a guessed one."""
    response = client.get(route, headers={"X-Admin-Token": "anything"})
    assert response.status_code == 404


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_wrong_token_is_401_when_configured(client, admin, route):
    """Here the route's existence is not the secret, so 401 is the honest answer."""
    response = client.get(route, headers={"X-Admin-Token": "wrong"})
    assert response.status_code == 401


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_missing_header_is_401_when_configured(client, admin, route):
    assert client.get(route).status_code == 401


def test_correct_token_is_admitted(client, admin):
    response = client.get("/api/admin/reports", headers=admin)
    assert response.status_code == 200
    assert response.json()["reports"] == []


# ------------------------------------------------------- persistence flag

def test_nothing_is_saved_when_history_is_off(client, temp_db, monkeypatch):
    make_session(monkeypatch, session_id="s1")

    assert client.post("/api/generate-report",
                       json={"session_id": "s1", "report_type": "A"}).status_code == 200

    conn = sqlite3.connect(temp_db)
    try:
        saved = conn.execute("SELECT COUNT(*) FROM saved_reports").fetchone()[0]
    finally:
        conn.close()
    assert saved == 0


def test_a_report_is_saved_when_history_is_on(client, temp_db, history_on, monkeypatch):
    make_session(monkeypatch, session_id="s1")

    assert client.post("/api/generate-report",
                       json={"session_id": "s1", "report_type": "A"},
                       headers={"X-Client-Id": "c1"}).status_code == 200

    conn = sqlite3.connect(temp_db)
    try:
        rows = conn.execute(
            "SELECT session_id, letter, client_id, bundle_version FROM saved_reports"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("s1", "A", "c1", telemetry.BUNDLE_VERSION)]


# ------------------------------------------------------------------ reading

def test_listing_omits_the_payload_but_reports_its_size(client, admin, history_on,
                                                        monkeypatch):
    """The bundle column can be megabytes; a listing must not load it."""
    make_session(monkeypatch, session_id="s1")
    client.post("/api/generate-report", json={"session_id": "s1", "report_type": "A"})

    body = client.get("/api/admin/reports", headers=admin).json()

    assert body["history_enabled"] is True
    assert len(body["reports"]) == 1
    entry = body["reports"][0]
    assert entry["session_id"] == "s1"
    assert entry["letter"] == "A"
    assert entry["bytes"] > 0
    assert "bundle" not in entry


def test_history_enabled_distinguishes_empty_from_switched_off(client, admin):
    """An empty list means two different things; the caller should not have to guess."""
    body = client.get("/api/admin/reports", headers=admin).json()

    assert body["reports"] == []
    assert body["history_enabled"] is False


def test_a_bundle_carries_everything_needed_to_re_render(client, admin, history_on,
                                                         monkeypatch):
    """The whole point: reopen a report with no LLM call and no original file."""
    make_session(monkeypatch, session_id="s1")
    client.post("/api/generate-report", json={"session_id": "s1", "report_type": "A"})
    report_id = client.get("/api/admin/reports", headers=admin).json()["reports"][0]["id"]

    bundle = client.get(f"/api/admin/reports/{report_id}", headers=admin).json()

    assert bundle["bundle_version"] == telemetry.BUNDLE_VERSION
    assert bundle["session_id"] == "s1"
    assert bundle["report_letter"] == "A"
    # A chart to draw, statistics to show, rows for the table, and the
    # recommendation behind it.
    assert bundle["report"]["chart"]
    assert bundle["report"]["stats"]
    assert bundle["report"]["rows"]
    assert bundle["recommendations"]["recommendations"]


def test_the_saved_copy_keeps_more_rows_than_the_client_received(
        client, admin, history_on, monkeypatch):
    """Why this is saved server-side rather than posted from the browser.

    SESSIONS keeps up to MAX_STORED_ROWS; the response carries at most
    MAX_ROWS_RETURNED. Saving from the client would silently keep the smaller set.
    """
    make_session(monkeypatch, session_id="s1")
    client.post("/api/generate-report", json={"session_id": "s1", "report_type": "A"})
    report_id = client.get("/api/admin/reports", headers=admin).json()["reports"][0]["id"]

    bundle = client.get(f"/api/admin/reports/{report_id}", headers=admin).json()

    assert len(bundle["rows_stored"]) >= len(bundle["report"]["rows"])


def test_an_unknown_report_id_is_404(client, admin):
    assert client.get("/api/admin/reports/9999", headers=admin).status_code == 404


def test_events_endpoint_exposes_props_that_stats_aggregates_away(client, admin):
    telemetry.log_event("analysis_completed", client_id="c1", session_id="s1",
                        props={"llm_provider": "gemini", "duration_ms": 1234})

    body = client.get("/api/admin/events", headers=admin).json()

    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["event"] == "analysis_completed"
    assert event["props"]["llm_provider"] == "gemini"


# ------------------------------------------------------------------ non-fatal

def test_a_failing_save_does_not_break_report_generation(client, history_on,
                                                         monkeypatch):
    """Same rule as the rest of telemetry: never load-bearing."""
    def explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(telemetry, "save_report", explode)
    make_session(monkeypatch, session_id="s1")

    response = client.post("/api/generate-report",
                           json={"session_id": "s1", "report_type": "A"})

    assert response.status_code == 200
    assert response.json()["rows"]
