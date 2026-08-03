"""Persist a session's *source* files so a report can be rebuilt later.

This is deliberately not a report store. Nothing here writes report rows, cell
values or a database: it copies the workbooks the user uploaded into
``session_data/<session_id>/source/`` and records, in ``manifest.json``, the few
non-derivable facts needed to reconstruct the run - the LLM's recommendations,
which worksheets were selected, and the file profiles.

Everything else about a report is *derived*, so it is regenerated on demand
rather than stored. `app.api.rehydrate_session` reads a manifest back into the
shape `/api/generate-report` expects, and the report is then built by the same
code path a live request uses. No LLM call is involved in a replay.

Two constraints make that work, and both are load-bearing:

1. **The original filename is preserved exactly.** `DataLoader.tables()` keys on
   `os.path.basename(name)`, and the recommendations reference those keys. The
   directory a file sits in is irrelevant; its name is not.
2. **`sheet_selections` round-trips.** `DataLoader.add_files(paths, selections)`
   decides which worksheets exist and therefore what the table keys are.
   Replaying without it silently loads sheets the original run excluded.

The directory is the source of truth. A manifest whose ``source/`` has been
deleted describes an *expired* session: `list_sessions` reports it as
unreplayable rather than raising, so pruning `session_data/` by hand can never
500 the admin listing.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

# Where snapshots live. A module-level default rather than a constant read per
# call, because the tests point it somewhere else with monkeypatch.setattr and
# the value never comes from the environment.
SESSION_ROOT = Path("session_data")

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
SOURCE_DIRNAME = "source"


def history_enabled() -> bool:
    """Whether to persist session snapshots at all.

    Read on each call, never captured at import - the same reasoning as
    `report_builder._debug_files_enabled()`: this module is imported before
    `load_dotenv()` has necessarily run, so a module-level read would miss
    anything set in `.env` and quietly disable the feature. Reading per call is
    also what makes `monkeypatch.setenv` work in tests.
    """
    return os.getenv("SAVE_REPORT_HISTORY", "false").strip().lower() in ("1", "true", "yes")


def session_dir(session_id: str) -> Path:
    """The directory holding one session's snapshot."""
    return SESSION_ROOT / session_id


def _app_version() -> str:
    """The API's declared version, or "unknown" if the import fails.

    Imported lazily and defensively: app.api imports *this* module, so a
    top-level import would close a cycle, and a snapshot must never fail over a
    version string.
    """
    try:
        from app.api import app as _fastapi_app
        return _fastapi_app.version
    except Exception:
        return "unknown"


def _pandas_version() -> str:
    try:
        import pandas
        return pandas.__version__
    except Exception:
        return "unknown"


def save_session_snapshot(
    session_id,
    client_id,
    file_paths,
    sheet_selections,
    recommendations,
    file_profiles,
    file_metadata,
):
    """Copy the uploads and write the manifest. Returns the session directory.

    Call this from inside `/api/analyze-full`'s `try`, *before* the `finally`
    removes the temp directory - once `shutil.rmtree` has run the source is gone
    and there is nothing left to copy.

    `file_profiles` are `FileProfile` objects; they are serialised through
    `_profile_to_dict` because the manifest has to be plain JSON.
    """
    target = session_dir(session_id)
    source_dir = target / SOURCE_DIRNAME
    source_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for path in file_paths:
        # basename, not the temp path: the name is what the table keys are built
        # from, and the temp directory it lived in is about to be deleted.
        name = os.path.basename(path)
        shutil.copy2(path, source_dir / name)
        saved.append(name)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "saved_at": datetime.now().isoformat(),
        "app_version": _app_version(),
        # Cheap insurance: a pandas upgrade can shift dtypes or group ordering,
        # and a replay that diverges is much easier to diagnose when the version
        # that produced the original is written down.
        "pandas_version": _pandas_version(),
        "session_id": session_id,
        "client_id": client_id,
        "recommendations": recommendations,
        # Mandatory, not decoration - see the module docstring.
        "sheet_selections": sheet_selections,
        "file_profiles": [_profile_to_dict(p) for p in (file_profiles or [])],
        "files": file_metadata,
        "source_files": saved,
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    return target


def _profile_to_dict(profile):
    """One FileProfile as plain JSON.

    Handles dataclasses, plain objects with a `__dict__`, and things that are
    already dicts, because the only consumer that matters (`_axis_granularity`)
    reads a handful of attributes and several call sites construct profiles
    differently in tests.
    """
    if isinstance(profile, dict):
        return profile
    try:
        import dataclasses
        if dataclasses.is_dataclass(profile):
            return json.loads(json.dumps(dataclasses.asdict(profile), default=str))
    except Exception:
        pass
    data = getattr(profile, "__dict__", None)
    if data is None:
        return {"repr": repr(profile)}
    return json.loads(json.dumps(dict(data), default=str))


def load_manifest(session_id):
    """One session's manifest as a dict, or None if it isn't there / is corrupt.

    Returning None rather than raising is what lets the admin listing render a
    half-deleted `session_data/` instead of failing whole.
    """
    path = session_dir(session_id) / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[session_store] Could not read manifest for {session_id}: {type(e).__name__}: {e}")
        return None


def source_paths(session_id):
    """The persisted upload paths for one session, sorted, as strings.

    Sorted so `DataLoader.add_files` sees a stable order: table *keys* don't
    depend on it, but the order profiles and relationships come out in does.
    """
    directory = session_dir(session_id) / SOURCE_DIRNAME
    if not directory.is_dir():
        return []
    return sorted(str(p) for p in directory.iterdir() if p.is_file())


def is_replayable(session_id) -> bool:
    """Whether this session still has both a manifest and its source files."""
    return bool(source_paths(session_id)) and (
        session_dir(session_id) / MANIFEST_NAME
    ).is_file()


def list_sessions(limit=200):
    """Every saved session, newest first, as summary dicts.

    Newest-first works by sorting on the directory name because a session id is
    `%Y%m%d_%H%M%S_<hex>` - lexical order is chronological order. Nothing parses
    it back into a datetime; `saved_at` in the manifest is the real timestamp.

    A directory with no readable manifest is still listed, marked unreplayable,
    so a half-written snapshot is visible rather than invisible.
    """
    if not SESSION_ROOT.is_dir():
        return []

    out = []
    for entry in sorted(SESSION_ROOT.iterdir(), key=lambda p: p.name, reverse=True):
        if not entry.is_dir():
            continue
        manifest = load_manifest(entry.name)
        if manifest is None:
            # Directories created by SAVE_DEBUG_FILES alone have no manifest and
            # are not snapshots. Skipping them keeps the listing meaningful.
            continue
        recs = (manifest.get("recommendations") or {}).get("recommendations") or []
        out.append({
            "session_id": entry.name,
            "saved_at": manifest.get("saved_at"),
            "client_id": manifest.get("client_id"),
            "app_version": manifest.get("app_version"),
            "pandas_version": manifest.get("pandas_version"),
            "files": [f.get("name") for f in (manifest.get("files") or [])],
            "file_count": len(manifest.get("files") or []),
            # Letter + name per recommendation, so the listing can offer the
            # replay links without a second request per session.
            "reports": [
                {"letter": chr(ord("A") + i), "report_name": r.get("report_name")}
                for i, r in enumerate(recs)
            ],
            "replayable": is_replayable(entry.name),
        })
        if len(out) >= limit:
            break
    return out


def delete_session(session_id) -> bool:
    """Remove one snapshot directory. True if something was deleted."""
    target = session_dir(session_id)
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True
