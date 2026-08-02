"""Unit tests for app/data/telemetry.py.

Two properties matter more than the rest and are tested first: telemetry never
raises at its callers, and it never writes a filename in plaintext by default. The
rest of the module is a thin wrapper over sqlite and is tested by round-trip.

Every test here redirects TELEMETRY_DB at a tmp_path, so nothing touches the
developer's real usage.db. conftest.py does the same for the whole suite; the
explicit fixture below is what gives each test its own empty database.
"""

import json
import os
import sqlite3

import pytest

from app.data import telemetry


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh, empty database for one test."""
    path = tmp_path / "usage.db"
    monkeypatch.setenv("TELEMETRY_DB", str(path))
    monkeypatch.delenv("TELEMETRY_STORE_NAMES", raising=False)
    return path


# ============================================================================
# Non-fatal by construction
# ============================================================================

@pytest.fixture
def broken_db(tmp_path, monkeypatch):
    """Point TELEMETRY_DB at a directory. sqlite cannot open it, so every write
    fails at connect time - the closest thing to a genuinely broken database that
    a test can arrange portably."""
    unopenable = tmp_path / "not-a-file"
    unopenable.mkdir()
    monkeypatch.setenv("TELEMETRY_DB", str(unopenable))
    return unopenable


def test_log_event_swallows_a_broken_database(broken_db, capsys):
    # The whole point of the module: a caller in api.py must not have to guard
    # this call, so a failure returns False instead of propagating.
    assert telemetry.log_event("analysis_started", client_id="c1") is False
    assert "[telemetry]" in capsys.readouterr().out


def test_record_file_and_record_report_swallow_a_broken_database(broken_db):
    assert telemetry.record_file(name="sales.xlsx", ext=".xlsx") is False
    assert telemetry.record_report(letter="A", pattern="RANKING") is False


def test_stats_returns_zeros_rather_than_raising_when_broken(broken_db):
    assert telemetry.stats() == telemetry.empty_stats()


def test_recent_events_returns_empty_rather_than_raising_when_broken(broken_db):
    assert telemetry.recent_events() == []


def test_unserialisable_props_do_not_lose_the_event(db):
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    assert telemetry.log_event("analysis_started", props={"thing": Opaque()}) is True
    event = telemetry.recent_events()[0]
    # Degraded to a repr rather than dropping the row - a bad prop value costs one
    # field, not the whole event.
    assert event["props"]["thing"] == "<opaque>"


# ============================================================================
# Privacy
# ============================================================================

def test_names_are_hashed_by_default(db):
    telemetry.record_file(name="quarterly_revenue.xlsx", ext=".xlsx", rows=10)

    with sqlite3.connect(db) as conn:
        stored = conn.execute("SELECT name_hash FROM files").fetchone()[0]

    assert stored != "quarterly_revenue.xlsx"
    assert len(stored) == 12
    assert stored == telemetry.hash_name("quarterly_revenue.xlsx")


def test_hashing_is_stable_so_repeat_uploads_stay_countable(db):
    assert telemetry.hash_name("orders.csv") == telemetry.hash_name("orders.csv")
    assert telemetry.hash_name("orders.csv") != telemetry.hash_name("orders.xlsx")


def test_plaintext_names_only_when_explicitly_enabled(db, monkeypatch):
    monkeypatch.setenv("TELEMETRY_STORE_NAMES", "1")
    assert telemetry.hash_name("quarterly_revenue.xlsx") == "quarterly_revenue.xlsx"


def test_store_names_is_read_per_call_not_frozen_at_import(db, monkeypatch):
    # AI_Engine.py reads its flags at module level and cannot be changed after
    # import. This module deliberately does not, so a flag flip takes effect.
    assert telemetry.hash_name("a.csv") != "a.csv"
    monkeypatch.setenv("TELEMETRY_STORE_NAMES", "true")
    assert telemetry.hash_name("a.csv") == "a.csv"


def test_hash_name_passes_through_none(db):
    assert telemetry.hash_name(None) is None


# ============================================================================
# Schema and round-trip
# ============================================================================

def test_init_db_is_idempotent(db):
    telemetry.init_db()
    telemetry.init_db()

    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"events", "files", "reports"} <= tables


def test_writes_create_the_schema_without_an_explicit_init(db):
    # There is no startup hook in api.py to call init_db() from, so the first
    # write has to be able to stand the database up on its own.
    assert not os.path.exists(db)
    assert telemetry.log_event("files_inspected") is True
    assert os.path.exists(db)


def test_log_event_round_trip(db):
    telemetry.log_event(
        "analysis_completed",
        client_id="client-1",
        session_id="20260802_120000_abc123",
        props={"provider": "gemini", "duration_ms": 4210, "table_count": 3},
    )

    events = telemetry.recent_events()
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "analysis_completed"
    assert event["client_id"] == "client-1"
    assert event["session_id"] == "20260802_120000_abc123"
    assert event["schema_version"] == telemetry.SCHEMA_VERSION
    assert event["props"]["provider"] == "gemini"
    assert event["props"]["duration_ms"] == 4210


def test_recent_events_is_newest_first_and_respects_its_limit(db):
    for i in range(5):
        telemetry.log_event(f"event_{i}")

    events = telemetry.recent_events(limit=3)
    assert [e["event"] for e in events] == ["event_4", "event_3", "event_2"]


def test_recent_events_filters_by_event_name_in_sql_not_after_fetching(db):
    telemetry.log_event("analysis_completed")
    for _ in range(10):
        telemetry.log_event("report_generated")

    # The ten newest rows are all report_generated. Filtering a fetched page in
    # Python would return zero here; filtering in SQL returns the one match.
    events = telemetry.recent_events(limit=5, event="analysis_completed")
    assert len(events) == 1
    assert events[0]["event"] == "analysis_completed"


def test_recent_files_and_recent_reports_round_trip(db):
    telemetry.record_file(session_id="s1", name="a.csv", ext=".csv", rows=10)
    telemetry.record_file(session_id="s2", name="b.xlsx", ext=".xlsx", rows=20)
    telemetry.record_report(session_id="s1", letter="A", pattern="RANKING")

    files = telemetry.recent_files()
    assert [f["session_id"] for f in files] == ["s2", "s1"]  # newest first
    assert telemetry.recent_files(limit=1)[0]["ext"] == ".xlsx"

    reports = telemetry.recent_reports()
    assert len(reports) == 1
    assert reports[0]["letter"] == "A"


def test_recent_files_and_reports_swallow_a_broken_database(broken_db):
    assert telemetry.recent_files() == []
    assert telemetry.recent_reports() == []


def test_db_path_honours_the_env_var_and_is_read_per_call(db, monkeypatch, tmp_path):
    assert telemetry.db_path() == str(db)
    other = tmp_path / "elsewhere.db"
    monkeypatch.setenv("TELEMETRY_DB", str(other))
    assert telemetry.db_path() == str(other)


def test_db_path_defaults_to_the_repo_root(monkeypatch):
    monkeypatch.delenv("TELEMETRY_DB", raising=False)
    assert os.path.basename(telemetry.db_path()) == "usage.db"


def test_props_are_stored_as_json_text(db):
    telemetry.log_event("report_generated", props={"letter": "A"})

    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT props FROM events").fetchone()[0]

    assert json.loads(raw) == {"letter": "A"}


def test_record_file_round_trip(db):
    telemetry.record_file(
        session_id="s1", client_id="c1", name="sales.XLSX", ext=".XLSX",
        size_bytes=2411008, kind="excel", sheet_count=4, sheets_selected=2,
        rows=8200, columns=12,
    )

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM files").fetchone())

    assert row["ext"] == ".xlsx"  # normalised, so the breakdown doesn't split by case
    assert row["kind"] == "excel"
    assert row["sheet_count"] == 4
    assert row["sheets_selected"] == 2
    assert row["rows"] == 8200
    assert row["load_ok"] == 1


def test_record_file_captures_a_failure(db):
    telemetry.record_file(name="broken.xlsx", ext=".xlsx", kind="unknown",
                          load_ok=False, error_type="BadZipFile")

    with sqlite3.connect(db) as conn:
        load_ok, error_type = conn.execute("SELECT load_ok, error_type FROM files").fetchone()

    assert load_ok == 0
    assert error_type == "BadZipFile"


def test_record_report_round_trip(db):
    telemetry.record_report(
        session_id="s1", client_id="c1", letter="A", pattern="RANKING",
        chart_type="bar", rows_returned=15, is_truncated=True, build_ms=320,
        has_schema_warning=True,
    )

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM reports").fetchone())

    assert row["letter"] == "A"
    assert row["pattern"] == "RANKING"
    assert row["chart_type"] == "bar"
    assert row["rows_returned"] == 15
    assert row["is_truncated"] == 1
    assert row["build_ms"] == 320
    assert row["ok"] == 1
    assert row["has_schema_warning"] == 1


# ============================================================================
# stats()
# ============================================================================

def test_stats_on_an_empty_database_returns_zeros(db):
    # A fresh clone, or the first boot after a deploy. The home page must render
    # four zeros rather than take a 500.
    assert telemetry.stats() == telemetry.empty_stats()


def test_visits_counts_arrivals_not_browsers(db):
    # One row per browser session, so the same client arriving twice is two visits
    # even though it is one browser - that is the difference from `clients`.
    telemetry.log_event("visit_started", client_id="c1")
    telemetry.log_event("visit_started", client_id="c1")
    telemetry.log_event("visit_started", client_id="c2")

    result = telemetry.stats()
    assert result["visits"] == 3
    assert result["clients"] == 2


def test_a_visitor_who_never_analyses_is_not_a_user(db):
    # The distinction the home page's headline number rests on. Landing on the
    # page, reading it, and leaving must not count as a user.
    telemetry.log_event("visit_started", client_id="c1")
    telemetry.log_event("tab_changed", client_id="c1")

    result = telemetry.stats()
    assert result["visits"] == 1
    assert result["users"] == 0


def test_a_visitor_becomes_a_user_on_activation(db):
    telemetry.log_event("visit_started", client_id="c1")
    telemetry.log_event("user_activated", client_id="c1")

    result = telemetry.stats()
    assert result["visits"] == 1
    assert result["users"] == 1


def test_users_never_exceeds_visits_for_one_session(db):
    # The frontend fires user_activated at most once per browser session, so a
    # sitting with several analyses is still one user.
    telemetry.log_event("visit_started", client_id="c1")
    telemetry.log_event("user_activated", client_id="c1")
    telemetry.log_event("analysis_completed", client_id="c1")
    telemetry.log_event("analysis_completed", client_id="c1")

    assert telemetry.stats()["users"] == 1


def test_visits_ignores_other_events(db):
    telemetry.log_event("files_inspected", client_id="c1")
    telemetry.log_event("report_generated", client_id="c1")
    result = telemetry.stats()
    assert result["visits"] == 0
    assert result["users"] == 0


def test_counters_are_zero_on_an_empty_database(db):
    for field in ("visits", "users", "clients"):
        assert telemetry.stats()[field] == 0
        assert telemetry.empty_stats()[field] == 0


def test_stats_counts_distinct_clients_and_sessions(db):
    telemetry.log_event("analysis_started", client_id="c1", session_id="s1")
    telemetry.log_event("analysis_completed", client_id="c1", session_id="s1")
    telemetry.log_event("analysis_started", client_id="c2", session_id="s2")

    result = telemetry.stats()
    assert result["clients"] == 2    # not 3 - the same client twice is one browser
    assert result["sessions"] == 2


def test_stats_breakdowns_and_counters(db):
    telemetry.record_file(session_id="s1", name="a.csv", ext=".csv")
    telemetry.record_file(session_id="s1", name="b.xlsx", ext=".xlsx")
    telemetry.record_file(session_id="s1", name="c.xlsx", ext=".xlsx")
    telemetry.record_report(session_id="s1", letter="A", pattern="RANKING")
    telemetry.record_report(session_id="s1", letter="B", pattern="RANKING")
    telemetry.record_report(session_id="s1", letter="C", pattern="TREND")

    result = telemetry.stats()
    assert result["files_processed"] == 3
    assert result["reports_built"] == 3
    assert result["ext_breakdown"] == {".xlsx": 2, ".csv": 1}
    assert result["pattern_breakdown"] == {"RANKING": 2, "TREND": 1}


def test_stats_excludes_failed_reports_from_reports_built(db):
    telemetry.record_report(letter="A", pattern="RANKING")
    telemetry.record_report(letter="B", pattern="TREND", ok=False, error_type="KeyError")

    result = telemetry.stats()
    assert result["reports_built"] == 1
    assert "TREND" not in result["pattern_breakdown"]


def test_stats_daily_buckets_by_date(db):
    telemetry.log_event("files_inspected")
    telemetry.log_event("analysis_started")

    daily = telemetry.stats()["daily"]
    assert len(daily) == 1
    assert daily[0]["events"] == 2
    # Bucketed by calendar day, so the sparkline has one point per day.
    assert len(daily[0]["date"]) == len("2026-08-02")
