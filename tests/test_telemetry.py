"""Unit tests for app/data/telemetry.py.

The assertions worth caring about here are the negative ones. Counters that go up
are easy; what matters is that telemetry cannot take a request down with it, and
that it does not quietly retain the user's data. Those two properties are the
whole justification for the module's shape, so they get explicit tests rather
than being assumed from a code read.

Every test points TELEMETRY_DB at a temp file, so nothing touches the real
usage.db and tests cannot see each other's rows.
"""

import json
import os
import sqlite3

import pytest

from app.data import telemetry


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Isolate each test in its own database."""
    path = str(tmp_path / "usage.db")
    monkeypatch.setenv("TELEMETRY_DB", path)
    monkeypatch.delenv("TELEMETRY_STORE_NAMES", raising=False)
    # The module remembers which paths it has initialised; a fresh tmp_path is a
    # new key, but clear anyway so ordering can never matter.
    telemetry._initialised.clear()
    return path


def rows(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------ schema setup

def test_init_db_is_idempotent(temp_db):
    telemetry.init_db()
    telemetry.init_db()  # must not raise on the second call

    names = {r[0] for r in rows(temp_db,
             "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "files", "reports"} <= names


def test_first_write_creates_the_database(temp_db):
    """No startup hook exists, so the first write has to be enough."""
    assert not os.path.exists(temp_db)

    assert telemetry.log_event("analysis_started", client_id="c1") is True

    assert os.path.exists(temp_db)
    assert rows(temp_db, "SELECT COUNT(*) FROM events")[0][0] == 1


# ------------------------------------------------------------------ writes

def test_log_event_records_props_as_json(temp_db):
    telemetry.log_event(
        "analysis_completed", client_id="c1", session_id="s1",
        props={"duration_ms": 1200, "provider": "gemini", "table_count": 3},
    )

    ts, client, session, event, version, props = rows(temp_db,
        "SELECT ts, client_id, session_id, event, schema_version, props FROM events")[0]
    assert (client, session, event) == ("c1", "s1", "analysis_completed")
    assert version == telemetry.SCHEMA_VERSION
    assert json.loads(props)["provider"] == "gemini"
    assert ts  # a timestamp was stamped


def test_unknown_event_names_are_prefixed_not_dropped(temp_db):
    """A client-side typo must stay visible without polluting the name space."""
    telemetry.log_event("totally_made_up", client_id="c1")

    assert rows(temp_db, "SELECT event FROM events")[0][0] == \
        "unknown_event:totally_made_up"


def test_record_file_and_record_report(temp_db):
    telemetry.record_file(
        session_id="s1", client_id="c1", name="sales.xlsx", ext=".xlsx",
        size_bytes=2048, kind="excel", sheet_count=3, sheets_selected=2,
        rows=120, columns=8,
    )
    telemetry.record_report(
        session_id="s1", client_id="c1", letter="A", pattern="RANKING",
        chart_type="bar", rows_returned=500, is_truncated=True, build_ms=340,
    )

    ext, sheets, selected, load_ok = rows(temp_db,
        "SELECT ext, sheet_count, sheets_selected, load_ok FROM files")[0]
    assert (ext, sheets, selected, load_ok) == (".xlsx", 3, 2, 1)

    letter, pattern, truncated, ok = rows(temp_db,
        "SELECT letter, pattern, is_truncated, ok FROM reports")[0]
    assert (letter, pattern, truncated, ok) == ("A", "RANKING", 1, 1)


def test_failures_are_recorded_with_their_error_class(temp_db):
    telemetry.record_file(session_id="s1", name="broken.xlsx", ext=".xlsx",
                          load_ok=False, error_type="BadZipFile")
    telemetry.record_report(session_id="s1", letter="B", ok=False,
                            error_type="KeyError")

    assert rows(temp_db, "SELECT load_ok, error_type FROM files")[0] == (0, "BadZipFile")
    assert rows(temp_db, "SELECT ok, error_type FROM reports")[0] == (0, "KeyError")


# ------------------------------------------------------------------ privacy

def test_filenames_are_hashed_by_default(temp_db):
    telemetry.record_file(session_id="s1", name="Q3 confidential salaries.xlsx",
                          ext=".xlsx")

    stored = rows(temp_db, "SELECT name_hash FROM files")[0][0]
    assert "confidential" not in stored
    assert "salaries" not in stored
    assert len(stored) == 12


def test_hashing_is_stable_so_repeat_uploads_are_still_countable(temp_db):
    assert telemetry.hash_name("sales.xlsx") == telemetry.hash_name("sales.xlsx")
    assert telemetry.hash_name("sales.xlsx") != telemetry.hash_name("other.xlsx")


def test_names_can_be_stored_in_the_clear_for_development(temp_db, monkeypatch):
    monkeypatch.setenv("TELEMETRY_STORE_NAMES", "1")

    telemetry.record_file(session_id="s1", name="sales.xlsx", ext=".xlsx")

    assert rows(temp_db, "SELECT name_hash FROM files")[0][0] == "sales.xlsx"


def test_no_cell_values_are_stored_anywhere(temp_db):
    """The schema must have nowhere to put a cell value even by accident."""
    telemetry.init_db()
    for table in ("events", "files", "reports"):
        columns = {r[1] for r in rows(temp_db, f"PRAGMA table_info({table})")}
        assert not {"values", "data", "rows_data", "cells", "sample"} & columns


# ------------------------------------------------------------------ non-fatal

def test_a_broken_database_does_not_raise(tmp_path, monkeypatch):
    """The property the whole module is shaped around.

    Points TELEMETRY_DB at a file that is not a database. Every write must return
    False and keep going, because the alternative is a user losing their analysis
    to a bookkeeping problem.
    """
    bad = tmp_path / "not-a-database.db"
    bad.write_bytes(b"this is definitely not sqlite" * 20)
    monkeypatch.setenv("TELEMETRY_DB", str(bad))
    telemetry._initialised.clear()

    assert telemetry.log_event("analysis_started", client_id="c1") is False
    assert telemetry.record_file(session_id="s1", ext=".csv") is False
    assert telemetry.record_report(session_id="s1", letter="A") is False
    assert telemetry.stats()["available"] is False


def test_an_undirectable_path_does_not_raise(monkeypatch):
    """A path that cannot be created must also degrade quietly."""
    monkeypatch.setenv("TELEMETRY_DB", os.path.join(os.devnull, "nested", "usage.db"))
    telemetry._initialised.clear()

    assert telemetry.log_event("analysis_started") is False


# ------------------------------------------------------------------ stats

def test_stats_on_a_missing_database_returns_an_empty_shape(temp_db):
    """The home page must render before anyone has used the app."""
    result = telemetry.stats()

    assert result["available"] is False
    assert result["users"] == 0
    assert result["files_processed"] == 0
    assert result["reports_built"] == 0
    assert result["daily"] == []


def test_stats_counts_distinct_users_and_sessions(temp_db):
    telemetry.log_event("analysis_started", client_id="c1", session_id="s1")
    telemetry.record_file(session_id="s1", client_id="c1", ext=".csv")
    telemetry.record_file(session_id="s1", client_id="c1", ext=".xlsx")
    telemetry.record_report(session_id="s1", client_id="c1", letter="A",
                            pattern="RANKING")
    # A second person, one session, one file, one report.
    telemetry.record_file(session_id="s2", client_id="c2", ext=".csv")
    telemetry.record_report(session_id="s2", client_id="c2", letter="A",
                            pattern="TREND")

    result = telemetry.stats()

    assert result["available"] is True
    assert result["users"] == 2
    assert result["sessions"] == 2
    assert result["files_processed"] == 3
    assert result["reports_built"] == 2
    assert result["ext_breakdown"] == {".csv": 2, ".xlsx": 1}
    assert result["pattern_breakdown"] == {"RANKING": 1, "TREND": 1}


def test_failed_reports_do_not_count_as_reports_built(temp_db):
    telemetry.record_report(session_id="s1", client_id="c1", letter="A", ok=True,
                            pattern="RANKING")
    telemetry.record_report(session_id="s1", client_id="c1", letter="B", ok=False,
                            error_type="KeyError")

    result = telemetry.stats()

    assert result["reports_built"] == 1
    assert result["pattern_breakdown"] == {"RANKING": 1}


def test_stats_counts_a_session_that_never_reached_a_report(temp_db):
    """Uploading and abandoning is still usage, and the funnel needs to see it."""
    telemetry.record_file(session_id="s1", client_id="c1", ext=".csv")

    result = telemetry.stats()

    assert result["sessions"] == 1
    assert result["users"] == 1
    assert result["reports_built"] == 0


def test_daily_series_groups_by_date(temp_db):
    telemetry.record_file(session_id="s1", client_id="c1", ext=".csv")
    telemetry.record_report(session_id="s1", client_id="c1", letter="A")

    daily = telemetry.stats()["daily"]

    assert len(daily) == 1
    assert daily[0]["files"] == 1
    assert daily[0]["reports"] == 1
    assert len(daily[0]["date"]) == 10  # YYYY-MM-DD
