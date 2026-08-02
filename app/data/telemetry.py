"""Usage tracking: how many people use the app, what they upload, what it produces.

Backed by SQLite through the standard library, so this adds no dependency. The
database is created on first write and lives at `usage.db` in the repo root unless
`TELEMETRY_DB` says otherwise (the tests point it at a temp file).

Two rules govern everything here.

**Telemetry must never break a request.** Every write is wrapped so that a locked
database, a full disk or a schema mistake produces a log line and nothing more.
A user losing their analysis because a counter could not be incremented would be
a far worse bug than the missing counter.

**Never store cell values.** Filenames and column names are hashed by default;
shapes, counts and durations answer every question the dashboard asks without
holding anyone's data. Set `TELEMETRY_STORE_NAMES=1` in development when you need
to read the names back.

Schema shape is a hybrid on purpose: three narrow typed tables carry the headline
counters so they stay cheap to aggregate and index, while a general `events` log
with a JSON `props` column absorbs anything new without a migration.
"""

import contextlib
import hashlib
import json
import os
import sqlite3
import time

# Bumped when the meaning of an existing event's props changes, so old and new
# rows stay distinguishable instead of being silently averaged together. Adding a
# brand new event name is not a version change.
SCHEMA_VERSION = 1

# Event names follow object_verb, snake_case. Kept as a whitelist because
# POST /api/events accepts names from the browser, and an open-ended name column
# turns into an unqueryable mess the first time a typo ships.
KNOWN_EVENTS = frozenset({
    "files_inspected",
    "analysis_started",
    "analysis_completed",
    "analysis_failed",
    "report_generated",
    "report_failed",
    "report_viewed",
    "report_exported",
    "report_emailed",
    "report_retry_clicked",
    "compare_all_opened",
    "data_table_toggled",
    "tab_changed",
})

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    client_id      TEXT,
    session_id     TEXT,
    event          TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    props          TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_event ON events(event);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_client ON events(client_id);

CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    session_id      TEXT,
    client_id       TEXT,
    ext             TEXT,
    size_bytes      INTEGER,
    kind            TEXT,
    sheet_count     INTEGER,
    sheets_selected INTEGER,
    rows            INTEGER,
    columns         INTEGER,
    load_ok         INTEGER,
    error_type      TEXT,
    name_hash       TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_session ON files(session_id);
CREATE INDEX IF NOT EXISTS idx_files_ts      ON files(ts);

CREATE TABLE IF NOT EXISTS reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT    NOT NULL,
    session_id          TEXT,
    client_id           TEXT,
    letter              TEXT,
    pattern             TEXT,
    chart_type          TEXT,
    rows_returned       INTEGER,
    is_truncated        INTEGER,
    build_ms            INTEGER,
    ok                  INTEGER,
    error_type          TEXT,
    has_schema_warning  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
CREATE INDEX IF NOT EXISTS idx_reports_ts      ON reports(ts);

-- Saved report bundles. Unlike every other table here this one DOES hold the
-- user's data (a report's rows), which is why it is gated on SAVE_REPORT_HISTORY
-- and readable only through the token-gated admin endpoints. It is a debugging
-- trail, not a user-facing library.
CREATE TABLE IF NOT EXISTS saved_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    client_id      TEXT,
    session_id     TEXT,
    letter         TEXT,
    name           TEXT,
    bundle_version INTEGER NOT NULL,
    bundle         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_reports_ts      ON saved_reports(ts);
CREATE INDEX IF NOT EXISTS idx_saved_reports_session ON saved_reports(session_id);
"""

# Bumped when the bundle's shape changes incompatibly. The dev viewer refuses a
# version it does not know rather than rendering half a report.
BUNDLE_VERSION = 1

# Paths already initialised this process. Keyed by path rather than a single bool
# so pointing TELEMETRY_DB somewhere else (as every test does) re-runs the schema.
_initialised = set()


def db_path():
    """Where the database lives. Read per call, not captured at import.

    Same reasoning as report_builder._debug_files_enabled: this module can be
    imported before AI_Engine calls load_dotenv(), so a value captured at import
    time would miss anything set in .env.
    """
    configured = os.getenv("TELEMETRY_DB", "").strip()
    return configured or os.path.join(_REPO_ROOT, "usage.db")


def store_names_enabled():
    """Whether to store filenames and column names in the clear."""
    return os.getenv("TELEMETRY_STORE_NAMES", "").strip().lower() in ("1", "true", "yes")


def hash_name(name):
    """A stable short digest of a name, or the name itself in dev.

    Hashed rather than dropped so questions like "how often is the same workbook
    re-uploaded?" stay answerable without keeping what it was called.
    """
    if name is None:
        return None
    if store_names_enabled():
        return str(name)
    return hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:12]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@contextlib.contextmanager
def _connect(path=None):
    """A short-lived connection, committed on clean exit.

    Deliberately not a long-lived shared connection. The API's sync handlers run
    in Starlette's threadpool, so a shared connection would need
    check_same_thread=False plus locking of its own; opening per call sidesteps
    that entirely and costs microseconds at this scale.
    """
    path = path or db_path()
    conn = sqlite3.connect(path, timeout=5.0)
    try:
        # Lets readers (the stats endpoint) proceed while a write is in flight.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path=None):
    """Create the schema if it is not there. Safe to call repeatedly."""
    path = path or db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)
    _initialised.add(path)
    return path


def _ensure_init(path):
    """Initialise on first use rather than from a startup hook.

    api.py has no lifespan handler, and a lazy check cannot be bypassed by a code
    path that forgets to call it -- including tests, which repoint TELEMETRY_DB
    freely.
    """
    if path not in _initialised:
        init_db(path)


def _swallow(operation, exc):
    """Report a telemetry failure without propagating it.

    The whole point: an analytics problem must never become the user's problem.
    """
    print(f"[telemetry] {operation} failed, continuing: {type(exc).__name__}: {exc}")


def log_event(event, client_id=None, session_id=None, props=None):
    """Append one event. Returns True if it was written.

    Unknown event names are recorded with an `unknown_event:` prefix rather than
    dropped: losing the signal entirely would hide a client-side typo, while
    letting arbitrary names in unprefixed would pollute the name space.
    """
    name = event if event in KNOWN_EVENTS else f"unknown_event:{event}"
    try:
        path = db_path()
        _ensure_init(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO events (ts, client_id, session_id, event, "
                "schema_version, props) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), client_id, session_id, name, SCHEMA_VERSION,
                 json.dumps(props or {}, default=str)),
            )
        return True
    except Exception as exc:
        _swallow(f"log_event({event})", exc)
        return False


def record_file(session_id=None, client_id=None, name=None, ext=None,
                size_bytes=None, kind=None, sheet_count=None,
                sheets_selected=None, rows=None, columns=None,
                load_ok=True, error_type=None):
    """One row per uploaded file. Returns True if it was written."""
    try:
        path = db_path()
        _ensure_init(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO files (ts, session_id, client_id, ext, size_bytes, "
                "kind, sheet_count, sheets_selected, rows, columns, load_ok, "
                "error_type, name_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), session_id, client_id, ext, size_bytes, kind, sheet_count,
                 sheets_selected, rows, columns, 1 if load_ok else 0, error_type,
                 hash_name(name)),
            )
        return True
    except Exception as exc:
        _swallow("record_file", exc)
        return False


def record_report(session_id=None, client_id=None, letter=None, pattern=None,
                  chart_type=None, rows_returned=None, is_truncated=False,
                  build_ms=None, ok=True, error_type=None,
                  has_schema_warning=False):
    """One row per report build attempt. Returns True if it was written."""
    try:
        path = db_path()
        _ensure_init(path)
        with _connect(path) as conn:
            conn.execute(
                "INSERT INTO reports (ts, session_id, client_id, letter, pattern, "
                "chart_type, rows_returned, is_truncated, build_ms, ok, error_type, "
                "has_schema_warning) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), session_id, client_id, letter, pattern, chart_type,
                 rows_returned, 1 if is_truncated else 0, build_ms,
                 1 if ok else 0, error_type, 1 if has_schema_warning else 0),
            )
        return True
    except Exception as exc:
        _swallow("record_report", exc)
        return False


# --------------------------------------------------------------------------
# Saved report bundles (developer tooling)
# --------------------------------------------------------------------------

def save_report_history_enabled():
    """Whether to keep a copy of every generated report.

    Off unless asked for, because these bundles contain the user's actual rows --
    retaining them has to be a deliberate choice, not a default.

    Read per call rather than captured at import, matching
    report_builder._debug_files_enabled: this module can be imported before
    load_dotenv() has run, and a value snapshotted then would ignore .env.
    """
    return os.getenv("SAVE_REPORT_HISTORY", "").strip().lower() in ("1", "true", "yes")


def save_report(session_id=None, client_id=None, letter=None, name=None,
                bundle=None):
    """Store one report bundle. Returns its row id, or None if nothing was saved.

    A no-op when SAVE_REPORT_HISTORY is off, checked here rather than at the call
    site so the flag cannot be honoured in one place and forgotten in another.
    """
    if not save_report_history_enabled():
        return None
    try:
        path = db_path()
        _ensure_init(path)
        payload = dict(bundle or {})
        payload.setdefault("bundle_version", BUNDLE_VERSION)
        with _connect(path) as conn:
            cursor = conn.execute(
                "INSERT INTO saved_reports (ts, client_id, session_id, letter, "
                "name, bundle_version, bundle) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), client_id, session_id, letter, name,
                 payload["bundle_version"],
                 json.dumps(payload, default=str)),
            )
            return cursor.lastrowid
    except Exception as exc:
        _swallow("save_report", exc)
        return None


def list_saved_reports(limit=100):
    """Recent bundles, newest first, without their payloads.

    The bundle column can be megabytes, so the list view never selects it -- a
    listing that loads every report's rows to show a table of names would fall
    over on exactly the sessions a developer most wants to inspect.
    """
    path = db_path()
    if not os.path.exists(path):
        return []
    try:
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT id, ts, session_id, letter, name, bundle_version, "
                "LENGTH(bundle) AS bytes FROM saved_reports "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "session_id": r[2], "letter": r[3],
             "name": r[4], "bundle_version": r[5], "bytes": r[6]}
            for r in rows
        ]
    except Exception as exc:
        _swallow("list_saved_reports", exc)
        return []


def get_saved_report(report_id):
    """One bundle by row id, parsed, or None if it is not there."""
    path = db_path()
    if not os.path.exists(path):
        return None
    try:
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT bundle FROM saved_reports WHERE id = ?", (report_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None
    except Exception as exc:
        _swallow("get_saved_report", exc)
        return None


def recent_events(limit=200):
    """The raw event log, newest first, for the admin view."""
    path = db_path()
    if not os.path.exists(path):
        return []
    try:
        with _connect(path) as conn:
            rows = conn.execute(
                "SELECT id, ts, client_id, session_id, event, schema_version, props "
                "FROM events ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        out = []
        for r in rows:
            try:
                props = json.loads(r[6]) if r[6] else {}
            except (TypeError, ValueError):
                props = {"_unparseable": r[6]}
            out.append({"id": r[0], "ts": r[1], "client_id": r[2],
                        "session_id": r[3], "event": r[4],
                        "schema_version": r[5], "props": props})
        return out
    except Exception as exc:
        _swallow("recent_events", exc)
        return []


def _rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def stats():
    """Aggregate counters for the home page.

    Returns zeros rather than raising when the database is missing or unreadable,
    so the home page degrades to an empty state instead of erroring. At capstone
    scale plain COUNTs are instant; there is no caching layer and no counters
    table to keep in sync.
    """
    empty = {
        "users": 0, "sessions": 0, "files_processed": 0, "reports_built": 0,
        "ext_breakdown": {}, "pattern_breakdown": {}, "daily": [],
        "available": False,
    }
    path = db_path()
    if not os.path.exists(path):
        return empty

    try:
        with _connect(path) as conn:
            # Sessions are counted across both tables: an analysis that produced no
            # report still happened, and a session is not required to reach the
            # report stage to count as usage.
            users = _rows(conn, """
                SELECT COUNT(DISTINCT client_id) FROM (
                    SELECT client_id FROM events  WHERE client_id IS NOT NULL
                    UNION SELECT client_id FROM files   WHERE client_id IS NOT NULL
                    UNION SELECT client_id FROM reports WHERE client_id IS NOT NULL
                )
            """)[0][0]
            sessions = _rows(conn, """
                SELECT COUNT(DISTINCT session_id) FROM (
                    SELECT session_id FROM events  WHERE session_id IS NOT NULL
                    UNION SELECT session_id FROM files   WHERE session_id IS NOT NULL
                    UNION SELECT session_id FROM reports WHERE session_id IS NOT NULL
                )
            """)[0][0]
            files_processed = _rows(conn, "SELECT COUNT(*) FROM files")[0][0]
            # Only successful builds count as a report the user actually got.
            reports_built = _rows(
                conn, "SELECT COUNT(*) FROM reports WHERE ok = 1")[0][0]

            ext_breakdown = dict(_rows(conn, (
                "SELECT COALESCE(ext, 'unknown'), COUNT(*) FROM files "
                "GROUP BY 1 ORDER BY 2 DESC")))
            pattern_breakdown = dict(_rows(conn, (
                "SELECT COALESCE(pattern, 'unknown'), COUNT(*) FROM reports "
                "WHERE ok = 1 GROUP BY 1 ORDER BY 2 DESC")))
            daily = [
                {"date": d, "files": f, "reports": r}
                for d, f, r in _rows(conn, """
                    SELECT day,
                           SUM(is_file)   AS files,
                           SUM(is_report) AS reports
                    FROM (
                        SELECT substr(ts, 1, 10) AS day, 1 AS is_file, 0 AS is_report
                          FROM files
                        UNION ALL
                        SELECT substr(ts, 1, 10) AS day, 0, 1
                          FROM reports WHERE ok = 1
                    )
                    GROUP BY day ORDER BY day
                """)
            ]

        return {
            "users": users,
            "sessions": sessions,
            "files_processed": files_processed,
            "reports_built": reports_built,
            "ext_breakdown": ext_breakdown,
            "pattern_breakdown": pattern_breakdown,
            "daily": daily,
            "available": True,
        }
    except Exception as exc:
        _swallow("stats", exc)
        return empty
