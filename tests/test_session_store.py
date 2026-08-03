"""Session snapshots — app/data/session_store.py and the /api/analyze-full hook.

Two layers are covered, because the interesting failures live at different ones:

* the store itself (does the manifest round-trip, is the filename preserved byte
  for byte, does a half-deleted directory degrade instead of raising), and
* the call site (does an upload actually persist, does it stay off by default,
  and does a broken store leave the analysis alone).

The filename assertions are not incidental detail. `DataLoader.tables()` keys on
`os.path.basename`, and the LLM's recommendations reference those keys, so a
snapshot that renames "sales.xlsx" to "0.xlsx" is unreplayable in a way nothing
else in the suite would notice.
"""

import json
import os

import pandas as pd
import pytest

import app.api as api
from app.data import session_store

from tests.test_upload_api import stub_llm  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


@pytest.fixture
def history_on(monkeypatch):
    """Turn persistence on. SESSION_ROOT is already inside tmp_path via the
    autouse isolated_session_store fixture in conftest."""
    monkeypatch.setenv("SAVE_REPORT_HISTORY", "true")


@pytest.fixture
def workbook(tmp_path):
    """A two-sheet .xlsx written to a temp path, standing in for an upload."""
    path = tmp_path / "sales.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"region": ["N", "S"], "amount": [10, 20]}).to_excel(
            writer, sheet_name="Orders", index=False
        )
        pd.DataFrame({"sku": ["a", "b"], "qty": [1, 2]}).to_excel(
            writer, sheet_name="Items", index=False
        )
    return path


def save(session_id="20260803_120000_abcdef", **overrides):
    kwargs = {
        "session_id": session_id,
        "client_id": "client-1",
        "file_paths": [],
        "sheet_selections": None,
        "recommendations": {"recommendations": [{"report_name": "R1"}]},
        "file_profiles": [],
        "file_metadata": [],
    }
    kwargs.update(overrides)
    return session_store.save_session_snapshot(**kwargs)


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------

def test_history_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SAVE_REPORT_HISTORY", raising=False)
    assert session_store.history_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", " true "])
def test_history_flag_accepts_the_usual_spellings(monkeypatch, value):
    monkeypatch.setenv("SAVE_REPORT_HISTORY", value)
    assert session_store.history_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", ""])
def test_history_flag_rejects_everything_else(monkeypatch, value):
    monkeypatch.setenv("SAVE_REPORT_HISTORY", value)
    assert session_store.history_enabled() is False


def test_flag_is_read_per_call_not_at_import(monkeypatch):
    """The whole point of _debug_files_enabled's idiom: setenv after import works."""
    monkeypatch.delenv("SAVE_REPORT_HISTORY", raising=False)
    assert session_store.history_enabled() is False
    monkeypatch.setenv("SAVE_REPORT_HISTORY", "true")
    assert session_store.history_enabled() is True


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def test_snapshot_preserves_the_filename_byte_for_byte(workbook):
    """The table keys are basenames, so the name is load-bearing, not cosmetic."""
    target = save(file_paths=[str(workbook)])

    copied = target / "source" / "sales.xlsx"
    assert copied.is_file()
    assert copied.read_bytes() == workbook.read_bytes()


def test_manifest_records_what_a_replay_needs(workbook):
    selections = {"sales.xlsx": ["Orders"]}
    recs = {"recommendations": [{"report_name": "Revenue by region"}]}
    target = save(
        file_paths=[str(workbook)],
        sheet_selections=selections,
        recommendations=recs,
        file_metadata=[{"name": "sales.xlsx", "size": 99, "rows": 2, "columns": 2}],
    )

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == session_store.MANIFEST_VERSION
    # sheet_selections is mandatory: add_files() decides which worksheets exist and
    # therefore what the table keys are. Dropping it replays the wrong tables.
    assert manifest["sheet_selections"] == selections
    assert manifest["recommendations"] == recs
    assert manifest["client_id"] == "client-1"
    assert manifest["files"][0]["name"] == "sales.xlsx"
    assert manifest["saved_at"] and manifest["pandas_version"]


def test_manifest_is_json_serialisable_with_awkward_profiles(workbook):
    """file_profiles arrive as objects, and pandas/numpy scalars ride along inside
    them. The manifest must still be plain JSON on the other side."""
    class Profile:
        def __init__(self):
            self.filename = "sales.xlsx"
            self.row_count = 2
            self.columns = ["region", "amount"]
            self.first_seen = pd.Timestamp("2023-01-01")

    target = save(file_paths=[str(workbook)], file_profiles=[Profile()])
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_profiles"][0]["filename"] == "sales.xlsx"
    assert manifest["file_profiles"][0]["row_count"] == 2


def test_saving_twice_overwrites_rather_than_accumulating(workbook):
    save(file_paths=[str(workbook)])
    target = save(file_paths=[str(workbook)])
    assert [p.name for p in (target / "source").iterdir()] == ["sales.xlsx"]


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------

def test_source_paths_are_sorted_and_absolute(tmp_path, workbook):
    second = tmp_path / "b.csv"
    second.write_text("x\n1\n", encoding="utf-8")
    save(file_paths=[str(workbook), str(second)])

    names = [os.path.basename(p) for p in session_store.source_paths("20260803_120000_abcdef")]
    assert names == ["b.csv", "sales.xlsx"]


def test_listing_reports_letters_and_names(workbook):
    save(
        file_paths=[str(workbook)],
        recommendations={"recommendations": [
            {"report_name": "First"}, {"report_name": "Second"}, {"report_name": "Third"},
        ]},
        file_metadata=[{"name": "sales.xlsx", "size": 1, "rows": 2, "columns": 2}],
    )

    listed = session_store.list_sessions()
    assert len(listed) == 1
    assert listed[0]["session_id"] == "20260803_120000_abcdef"
    assert listed[0]["replayable"] is True
    assert listed[0]["files"] == ["sales.xlsx"]
    assert [r["letter"] for r in listed[0]["reports"]] == ["A", "B", "C"]
    assert listed[0]["reports"][1]["report_name"] == "Second"


def test_listing_is_newest_first():
    for sid in ("20260101_000000_aaaaaa", "20260803_120000_bbbbbb", "20260501_000000_cccccc"):
        save(session_id=sid)
    assert [s["session_id"] for s in session_store.list_sessions()] == [
        "20260803_120000_bbbbbb", "20260501_000000_cccccc", "20260101_000000_aaaaaa",
    ]


def test_a_session_whose_source_was_deleted_lists_as_unreplayable(workbook):
    """The directory is the source of truth. Pruning session_data/ by hand must
    degrade the listing, never raise - this is D6's failure mode."""
    target = save(file_paths=[str(workbook)])
    for f in (target / "source").iterdir():
        f.unlink()

    listed = session_store.list_sessions()
    assert len(listed) == 1
    assert listed[0]["replayable"] is False
    assert session_store.is_replayable("20260803_120000_abcdef") is False


def test_directories_without_a_manifest_are_ignored(workbook):
    """SAVE_DEBUG_FILES creates session_data/<id>/ too. Those aren't snapshots."""
    save(file_paths=[str(workbook)])
    (session_store.SESSION_ROOT / "debug_only").mkdir(parents=True)
    (session_store.SESSION_ROOT / "debug_only" / "raw_response.txt").write_text("x")

    assert [s["session_id"] for s in session_store.list_sessions()] == [
        "20260803_120000_abcdef"
    ]


def test_a_corrupt_manifest_returns_none_rather_than_raising():
    target = session_store.session_dir("20260803_120000_abcdef")
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("{not json", encoding="utf-8")

    assert session_store.load_manifest("20260803_120000_abcdef") is None
    assert session_store.list_sessions() == []


def test_listing_an_absent_root_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(session_store, "SESSION_ROOT", tmp_path / "nope")
    assert session_store.list_sessions() == []


def test_delete_session_removes_the_directory(workbook):
    save(file_paths=[str(workbook)])
    assert session_store.delete_session("20260803_120000_abcdef") is True
    assert session_store.list_sessions() == []
    # Deleting something already gone is not an error.
    assert session_store.delete_session("20260803_120000_abcdef") is False


# ---------------------------------------------------------------------------
# The /api/analyze-full hook
# ---------------------------------------------------------------------------

def _upload(paths):
    return [("files", (os.path.basename(p), open(p, "rb"), "application/octet-stream"))
            for p in paths]


def _analyze(client, paths, selections=None):
    files = _upload(paths)
    data = {"selections": json.dumps(selections)} if selections is not None else None
    try:
        return client.post("/api/analyze-full", files=files, data=data)
    finally:
        for _, (_, handle, _) in files:
            handle.close()


def test_upload_persists_the_source_when_the_flag_is_on(
    client, stub_llm, history_on, workbook  # noqa: F811
):
    response = _analyze(client, [workbook])
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    saved = session_store.session_dir(session_id) / "source" / "sales.xlsx"
    assert saved.is_file()
    # Byte-identical to what was uploaded: the replay reads this file, not a
    # re-serialised DataFrame.
    assert saved.read_bytes() == workbook.read_bytes()

    manifest = session_store.load_manifest(session_id)
    assert manifest["session_id"] == session_id
    assert manifest["recommendations"] == response.json()["recommendations"]
    assert session_store.is_replayable(session_id)


def test_upload_persists_nothing_when_the_flag_is_off(
    client, stub_llm, workbook  # noqa: F811
):
    response = _analyze(client, [workbook])
    assert response.status_code == 200, response.text

    assert session_store.list_sessions() == []
    assert not session_store.session_dir(response.json()["session_id"]).exists()


def test_sheet_selections_round_trip_through_the_upload(
    client, stub_llm, history_on, workbook  # noqa: F811
):
    """What the user unchecked on the upload screen has to survive, or the replay
    loads sheets the original run never saw."""
    selections = {"sales.xlsx": ["Orders"]}
    response = _analyze(client, [workbook], selections)
    assert response.status_code == 200, response.text

    manifest = session_store.load_manifest(response.json()["session_id"])
    assert manifest["sheet_selections"] == selections


def test_a_failing_snapshot_never_fails_the_analysis(
    client, stub_llm, history_on, workbook, monkeypatch  # noqa: F811
):
    """Two independent guards exist for this: history is a developer convenience
    bolted onto a request the user actually made."""
    def boom(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(session_store, "save_session_snapshot", boom)
    response = _analyze(client, [workbook])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "complete"


def test_snapshot_is_written_before_the_temp_dir_is_removed(
    client, stub_llm, history_on, workbook  # noqa: F811
):
    """The ordering bug this guards against is unrecoverable: after shutil.rmtree
    the uploads are gone, so a snapshot taken later can only ever be empty."""
    response = _analyze(client, [workbook])
    session_id = response.json()["session_id"]
    assert session_store.source_paths(session_id), "source/ is empty — saved too late"
