"""Usage tracking — a stdlib-sqlite3 event log that survives a server restart.

Everything the app knows about a run currently dies with the process: SESSIONS in
api.py is a module-level dict. This module is the persistence side of that gap. It
answers "how many people used it, what did they upload, did the pipeline work" from
counts and shapes alone.

Three rules shape the design:

1.  **Telemetry must never fail a user request.** Every public write swallows its
    own exceptions and prints. A broken DB, a locked file, or a bad prop value
    degrades to "no row was written", never to a 500 on an upload.
2.  **No cell values, ever.** Filenames and column names are hashed by default
    (TELEMETRY_STORE_NAMES=1 stores plaintext while developing). Shapes and counts
    answer every question the home page asks without holding anyone's data.
3.  **Short-lived connection per call.** api.py mixes an `async def` endpoint with
    sync ones that Starlette runs in its threadpool, so a shared connection would
    need check_same_thread handling and a lock. Opening per write costs
    microseconds locally and sidesteps the whole category.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# Stamped onto every event row. When the meaning of a prop changes, bump this so
# old and new rows are distinguishable rather than silently averaged together.
SCHEMA_VERSION = 1

# sqlite's date()/strftime() understand this directly. Deliberately not
# datetime.now().isoformat() (what the rest of the app uses for display
# timestamps): a "+00:00" offset makes the daily GROUP BY in stats() fiddlier for
# no benefit, and these timestamps are only ever read by SQL.
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    client_id      TEXT,
    session_id     TEXT,
    event          TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    props          TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
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
CREATE TABLE IF NOT EXISTS reports (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    session_id         TEXT,
    client_id          TEXT,
    letter             TEXT,
    pattern            TEXT,
    chart_type         TEXT,
    rows_returned      INTEGER,
    is_truncated       INTEGER,
    build_ms           INTEGER,
    ok                 INTEGER,
    error_type         TEXT,
    has_schema_warning INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_event    ON events(event);
CREATE INDEX IF NOT EXISTS idx_events_ts       ON events(ts);
CREATE INDEX IF NOT EXISTS idx_files_session   ON files(session_id);
CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
"""


# ============================================================================
# CONFIGURATION
# ============================================================================
# Read per call, not at module level. app/data/emailer.py:55 documents why: a
# module-level os.getenv races load_dotenv() during import, and it also freezes
# the value so monkeypatch.setenv in a test has no effect. AI_Engine.py reads its
# flags at import and has exactly that limitation - don't copy it here, because
# the test suite needs to redirect TELEMETRY_DB at a tmp_path per run.

def db_path():
    """Where usage.db lives. Defaults to the repo root, beside the app package.

    Public because a reader needs to be able to say *which* database it just
    reported on - scripts/show_usage.py prints it, and "the counters are all zero"
    is otherwise indistinguishable from "you are pointed at the wrong file".
    """
    configured = os.getenv("TELEMETRY_DB", "").strip()
    if configured:
        return configured
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "usage.db")


def _store_names_enabled():
    return os.getenv("TELEMETRY_STORE_NAMES", "").strip().lower() in ("1", "true", "yes")


def hash_name(value):
    """Hash a filename or column name unless plaintext is explicitly enabled.

    Twelve hex chars of sha256 - enough that two distinct names practically never
    collide at capstone volume, short enough to read in a query result. The point
    is that "the same file was uploaded twice" stays answerable while the file's
    actual name does not leave the machine it was uploaded on.
    """
    if value is None:
        return None
    text = str(value)
    if _store_names_enabled():
        return text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _now():
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


# ============================================================================
# CONNECTION
# ============================================================================

@contextmanager
def _connect():
    """Short-lived connection with WAL and a busy timeout.

    WAL lets the reader behind /api/stats run while a write is in flight instead
    of taking a "database is locked" error, and busy_timeout covers the case of
    two threadpool workers writing at the same moment.
    """
    conn = sqlite3.connect(db_path(), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the schema if it isn't there. Idempotent - safe to call per write.

    There is no startup hook in api.py to hang this off, and adding one would mean
    every TestClient(api.app) in the existing suite touches the developer's real
    usage.db. Initialising lazily on first write keeps that from happening and
    removes the ordering question entirely.
    """
    with _connect() as conn:
        conn.executescript(_DDL)


def _insert(table, values):
    """Shared write path: ensure schema, then insert one row."""
    columns = list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with _connect() as conn:
        conn.executescript(_DDL)
        conn.execute(sql, [values[c] for c in columns])


# ============================================================================
# WRITES — all non-fatal by construction
# ============================================================================

def log_event(event, client_id=None, session_id=None, props=None):
    """Append one row to the event log.

    Returns True if the row landed, False if anything at all went wrong. The
    return value exists for tests; callers in api.py ignore it, because there is
    no useful action to take when telemetry fails and a user's upload must not
    care either way.
    """
    try:
        _insert("events", {
            "ts": _now(),
            "client_id": client_id,
            "session_id": session_id,
            "event": event,
            "schema_version": SCHEMA_VERSION,
            # default=str so a stray non-serialisable value (a numpy int, a
            # Timestamp) degrades to its repr instead of losing the whole event.
            "props": json.dumps(props or {}, default=str),
        })
        return True
    except Exception as e:
        print(f"[telemetry] log_event({event!r}) failed: {type(e).__name__}: {e}")
        return False


def record_file(session_id=None, client_id=None, name=None, ext=None, size_bytes=None,
                kind=None, sheet_count=None, sheets_selected=None, rows=None,
                columns=None, load_ok=True, error_type=None):
    """One row per uploaded file. `name` is hashed on the way in - never stored raw
    unless TELEMETRY_STORE_NAMES is set."""
    try:
        _insert("files", {
            "ts": _now(),
            "session_id": session_id,
            "client_id": client_id,
            "ext": (ext or "").lower() or None,
            "size_bytes": size_bytes,
            "kind": kind,
            "sheet_count": sheet_count,
            "sheets_selected": sheets_selected,
            "rows": rows,
            "columns": columns,
            "load_ok": 1 if load_ok else 0,
            "error_type": error_type,
            "name_hash": hash_name(name),
        })
        return True
    except Exception as e:
        print(f"[telemetry] record_file failed: {type(e).__name__}: {e}")
        return False


def record_report(session_id=None, client_id=None, letter=None, pattern=None,
                  chart_type=None, rows_returned=None, is_truncated=False,
                  build_ms=None, ok=True, error_type=None, has_schema_warning=False):
    """One row per report generation attempt (cache hits are events, not rows here -
    a cached read didn't build anything, and counting it would inflate every
    per-report duration and pattern statistic)."""
    try:
        _insert("reports", {
            "ts": _now(),
            "session_id": session_id,
            "client_id": client_id,
            "letter": letter,
            "pattern": pattern,
            "chart_type": chart_type,
            "rows_returned": rows_returned,
            "is_truncated": 1 if is_truncated else 0,
            "build_ms": build_ms,
            "ok": 1 if ok else 0,
            "error_type": error_type,
            "has_schema_warning": 1 if has_schema_warning else 0,
        })
        return True
    except Exception as e:
        print(f"[telemetry] record_report failed: {type(e).__name__}: {e}")
        return False


# ============================================================================
# READS
# ============================================================================

def _scalar(conn, sql, default=0):
    row = conn.execute(sql).fetchone()
    value = row[0] if row else None
    return default if value is None else value


def stats():
    """Aggregate counters for GET /api/stats and the home page.

    Returns zeros rather than raising on an empty or missing database - that is
    the state on a fresh clone and on the first boot after a deploy, and a home
    page that 500s because nobody has used the app yet is worse than one showing
    four zeros. At capstone volume these are plain COUNTs over a few thousand
    rows, so there is no caching layer and no counters table to drift.
    """
    try:
        with _connect() as conn:
            conn.executescript(_DDL)
            return {
                # Three different questions, deliberately all answered rather than
                # collapsed into one number that would have to mean whichever the
                # caller assumed.
                #
                # `visits`  - arrivals. One row per browser session. Includes people
                #             who read the landing page and left.
                # `users`   - arrivals that actually put data through the pipeline.
                #             One row per browser session, on its first successful
                #             analysis. This is the headline figure: someone who
                #             looked around without uploading is a visitor, not a
                #             user, and counting them as one would make the number
                #             mostly measure curiosity.
                # `clients` - distinct browsers ever seen, across all time. Unlike
                #             the two above it does not reset when a browser closes,
                #             so it answers "how many different people" rather than
                #             "how many times".
                "visits": _scalar(
                    conn, "SELECT COUNT(*) FROM events WHERE event = 'visit_started'"
                ),
                "users": _scalar(
                    conn, "SELECT COUNT(*) FROM events WHERE event = 'user_activated'"
                ),
                "clients": _scalar(
                    conn,
                    "SELECT COUNT(DISTINCT client_id) FROM events WHERE client_id IS NOT NULL",
                ),
                "sessions": _scalar(
                    conn,
                    "SELECT COUNT(DISTINCT session_id) FROM events WHERE session_id IS NOT NULL",
                ),
                "files_processed": _scalar(conn, "SELECT COUNT(*) FROM files"),
                "reports_built": _scalar(conn, "SELECT COUNT(*) FROM reports WHERE ok = 1"),
                "ext_breakdown": {
                    r["ext"]: r["n"] for r in conn.execute(
                        "SELECT ext, COUNT(*) AS n FROM files "
                        "WHERE ext IS NOT NULL GROUP BY ext ORDER BY n DESC"
                    )
                },
                "pattern_breakdown": {
                    r["pattern"]: r["n"] for r in conn.execute(
                        "SELECT pattern, COUNT(*) AS n FROM reports "
                        "WHERE pattern IS NOT NULL AND ok = 1 GROUP BY pattern ORDER BY n DESC"
                    )
                },
                "daily": [
                    {"date": r["d"], "events": r["n"]} for r in conn.execute(
                        "SELECT date(ts) AS d, COUNT(*) AS n FROM events "
                        "GROUP BY d ORDER BY d"
                    )
                ],
            }
    except Exception as e:
        print(f"[telemetry] stats() failed: {type(e).__name__}: {e}")
        return empty_stats()


def empty_stats():
    """The shape stats() returns when there is nothing to report. Named so the
    frontend contract is defined in one place rather than implied by a failure
    path."""
    return {
        "visits": 0, "users": 0, "clients": 0, "sessions": 0, "files_processed": 0,
        "reports_built": 0, "ext_breakdown": {}, "pattern_breakdown": {}, "daily": [],
    }


def _recent(table, limit, where=None, params=()):
    """Newest-first rows from one table. Shared by the three readers below, which
    differ only in their table and their filter."""
    try:
        clause = f" WHERE {where}" if where else ""
        with _connect() as conn:
            conn.executescript(_DDL)
            rows = conn.execute(
                f"SELECT * FROM {table}{clause} ORDER BY id DESC LIMIT ?",
                (*params, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[telemetry] read from {table} failed: {type(e).__name__}: {e}")
        return []


def recent_events(limit=100, event=None):
    """Most recent events, newest first, optionally filtered to one event name.

    Used by the tests, by scripts/show_usage.py, and the token-gated admin
    endpoint. `props` comes back as a parsed dict rather than the JSON
    text actually stored - a caller reading raw SQL gets an opaque blob, which is
    exactly the thing that makes the sqlite CLI unpleasant for this table.

    The filter is applied in SQL, not after: filtering a fetched page in Python
    would silently return fewer rows than asked for whenever the last N events
    happen to be some other event name.
    """
    where, params = ("event = ?", (event,)) if event else (None, ())
    rows = _recent("events", limit, where, params)
    for item in rows:
        try:
            item["props"] = json.loads(item["props"]) if item["props"] else {}
        except (TypeError, ValueError):
            item["props"] = {}
    return rows


def recent_files(limit=50):
    """Most recent uploaded-file rows, newest first."""
    return _recent("files", limit)


def recent_reports(limit=50):
    """Most recent report-generation rows, newest first."""
    return _recent("reports", limit)
