"""The token-gated developer routes — GET /api/admin/*.

Three things are being verified, in order of how badly they'd hurt if wrong:

1. **The gate.** Unset ADMIN_TOKEN must 404 (an unconfigured deployment shouldn't
   advertise these routes), a wrong token must 401, and neither may be reachable
   by accident from the user-facing app.
2. **Replay equivalence.** A report rebuilt from a saved session must match the
   one the live endpoint produced from the same input — everything but
   `generated_at`, which is read from the wall clock at build time by design.
3. **Degradation.** A session whose source files were deleted lists as
   unreplayable and answers 410, rather than 500ing or returning a blank report.

The replay path is exercised end to end through /api/analyze-full with the LLM
stubbed, because the point of the whole feature is that the *second* build costs
no model call — a hand-built SESSIONS entry would skip the part that matters.
"""

import json
import os
from datetime import datetime

import pandas as pd
import pytest

import app.api as api
from app.data import session_store

from tests.test_upload_api import stub_llm  # noqa: F401  (fixture)

TOKEN = "test-admin-token"
AUTH = {"X-Admin-Token": TOKEN}


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


@pytest.fixture
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)


@pytest.fixture
def history_on(monkeypatch):
    monkeypatch.setenv("SAVE_REPORT_HISTORY", "true")


@pytest.fixture
def csv_file(tmp_path):
    """A small CSV whose grouped shape the stub recommendation can actually build."""
    path = tmp_path / "orders.csv"
    pd.DataFrame({
        "region": ["North", "South", "North", "East"],
        "amount": [10, 20, 30, 40],
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def workbook(tmp_path):
    """Two groupable sheets, so table keys become "<sheet> (<stem>).xlsx".

    Both carry the same columns on purpose: whichever sheet the stub picks, the
    recommendation resolves, so the test is about the *keys* rather than about
    which sheet won.
    """
    path = tmp_path / "sales.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"region": ["N", "S", "N"], "amount": [1, 2, 3]}).to_excel(
            writer, sheet_name="Orders", index=False
        )
        pd.DataFrame({"region": ["E", "W"], "amount": [50, 60]}).to_excel(
            writer, sheet_name="Items", index=False
        )
    return path


@pytest.fixture
def grouping_llm(monkeypatch):
    """A stub recommendation that produces a real grouped report and a real chart.

    Distinct from tests/test_upload_api.py's stub_llm, whose empty groupby is enough
    to prove files reached the pipeline but produces nothing worth comparing.
    """
    def fake(prompt, valid_filenames, **kwargs):
        target = sorted(valid_filenames)[0] if valid_filenames else "unknown.csv"
        rec = {
            "rank": 1,
            "report_name": "Amount by region",
            "question_answered": "Which region sells most?",
            "pattern_used": "RANKING",
            "rationale_bullets": ["Stubbed."],
            "required_operations": [{
                "operation_type": "groupby",
                "files_involved": [target],
                "groupby_columns": ["region"],
                "aggregations": [{"column": "amount", "func": "sum"}],
            }],
            "plotly_config": {
                "chart_type": "bar", "x_axis": "region", "y_axis": "amount_sum",
                "title": "Amount by region",
            },
        }
        return {"recommendations": [rec, {**rec, "rank": 2, "report_name": "Second"}]}

    monkeypatch.setattr(api.ai_engine, "get_validated_recommendations", fake)
    return fake


def _analyze(client, paths, selections=None):
    files = [("files", (os.path.basename(p), open(p, "rb"), "application/octet-stream"))
             for p in paths]
    data = {"selections": json.dumps(selections)} if selections is not None else None
    try:
        return client.post("/api/analyze-full", files=files, data=data)
    finally:
        for _, (_, handle, _) in files:
            handle.close()


def _saved_session(client, paths, selections=None):
    """Run a stubbed analysis, then forget it the way a server restart would."""
    response = _analyze(client, paths, selections)
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    return session_id


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

ADMIN_ROUTES = [
    "/api/admin/sessions",
    "/api/admin/sessions/anything",
    "/api/admin/sessions/anything/reports/A",
    "/api/admin/stats",
]


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_unset_admin_token_is_404_not_401(client, monkeypatch, route):
    """An unconfigured deployment must not advertise that these routes exist."""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get(route).status_code == 404
    # Not even with a token: absent configuration means absent routes.
    assert client.get(route, headers=AUTH).status_code == 404


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_wrong_token_is_401(client, admin_on, route):
    assert client.get(route, headers={"X-Admin-Token": "nope"}).status_code == 401


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_missing_token_with_admin_configured_is_401(client, admin_on, route):
    assert client.get(route).status_code == 401


def test_blank_admin_token_env_counts_as_unset(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "   ")
    assert client.get("/api/admin/sessions", headers=AUTH).status_code == 404


def test_admin_token_is_read_per_call(client, monkeypatch):
    """Same reasoning as SAVE_REPORT_HISTORY: nothing is captured at import."""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get("/api/admin/sessions", headers=AUTH).status_code == 404
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    assert client.get("/api/admin/sessions", headers=AUTH).status_code == 200


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_is_empty_when_nothing_was_saved(client, admin_on):
    body = client.get("/api/admin/sessions", headers=AUTH).json()
    assert body["sessions"] == []
    assert body["history_enabled"] is False


def test_listing_shows_a_saved_session(
    client, admin_on, history_on, grouping_llm, csv_file
):
    session_id = _saved_session(client, [csv_file])

    body = client.get("/api/admin/sessions", headers=AUTH).json()
    assert body["history_enabled"] is True
    entry = next(s for s in body["sessions"] if s["session_id"] == session_id)
    assert entry["replayable"] is True
    assert entry["files"] == ["orders.csv"]
    assert [r["letter"] for r in entry["reports"]] == ["A", "B"]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def test_detail_returns_the_full_recommendations(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """The listing carries a letter and a name; the Reports page needs the objects."""
    session_id = _saved_session(client, [csv_file])

    body = client.get(f"/api/admin/sessions/{session_id}", headers=AUTH).json()

    assert body["session_id"] == session_id
    assert body["replayable"] is True
    assert body["files"] == ["orders.csv"]

    recs = body["recommendations"]["recommendations"]
    assert len(recs) == 2
    # Verbatim, not a summary: the page reads these keys directly, and a listing
    # entry has none of them.
    assert recs[0]["report_name"]
    assert recs[0]["pattern_used"]


def test_detail_costs_no_workbook_read(
    client, admin_on, history_on, grouping_llm, csv_file, monkeypatch
):
    """Drawing a report switcher must not re-read and re-profile every saved file."""
    session_id = _saved_session(client, [csv_file])

    def explode(*args, **kwargs):
        raise AssertionError("detail rehydrated the session")

    monkeypatch.setattr(api, "rehydrate_session", explode)
    assert client.get(f"/api/admin/sessions/{session_id}", headers=AUTH).status_code == 200


def test_detail_of_a_never_saved_session_is_404(client, admin_on):
    response = client.get("/api/admin/sessions/nope_20200101", headers=AUTH)
    assert response.status_code == 404
    assert "never saved" in response.json()["detail"]


def test_detail_of_an_expired_session_is_still_served(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """A session whose workbooks are gone can't be replayed, but it can be described.

    404-ing here would make an unreplayable row unclickable *and* unexplainable;
    `replayable: False` is the honest answer, and the report route still 410s.
    """
    session_id = _saved_session(client, [csv_file])
    for f in (session_store.session_dir(session_id) / "source").iterdir():
        f.unlink()

    body = client.get(f"/api/admin/sessions/{session_id}", headers=AUTH).json()
    assert body["replayable"] is False
    assert body["recommendations"]["recommendations"]


def test_detail_is_503_without_the_session_store(client, admin_on, monkeypatch):
    monkeypatch.setattr(api, "SESSION_STORE_AVAILABLE", False)
    assert client.get("/api/admin/sessions/anything", headers=AUTH).status_code == 503


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def test_replay_matches_the_live_report(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """The equivalence claim the whole design rests on.

    Everything but generated_at must match: the pipeline reads no clock, so the same
    bytes in produce the same DataFrame out. SESSIONS is cleared in between to stand
    in for a server restart - the replay may not lean on anything in memory.
    """
    session_id = _saved_session(client, [csv_file])

    live = client.post(
        "/api/generate-report", json={"session_id": session_id, "report_type": "A"}
    )
    assert live.status_code == 200, live.text
    live_body = live.json()

    api.SESSIONS.clear()  # the restart

    replayed = client.get(
        f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH
    )
    assert replayed.status_code == 200, replayed.text
    replayed_body = replayed.json()

    assert replayed_body["rows"] == live_body["rows"]
    assert replayed_body["stats"] == live_body["stats"]
    assert replayed_body["chart"] == live_body["chart"]
    assert replayed_body["columns"] == live_body["columns"]
    assert replayed_body["report_rows"] == live_body["report_rows"]
    assert replayed_body["report_name"] == live_body["report_name"]
    # The one field that legitimately differs.
    assert set(live_body) - set(replayed_body) == set()


def test_replay_costs_no_llm_call(
    client, admin_on, history_on, grouping_llm, csv_file, monkeypatch
):
    """The feature's reason for existing. If a replay could reach the model, a
    developer browsing old sessions would silently burn the daily quota."""
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()

    def explode(*args, **kwargs):
        raise AssertionError("replay must never call the LLM")

    monkeypatch.setattr(api.ai_engine, "get_validated_recommendations", explode)
    response = client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)
    assert response.status_code == 200, response.text


def test_replay_rehydrates_into_sessions_so_export_works(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """Rehydrating *into* SESSIONS is what makes the session genuinely exist again:
    _resolve_export finds it by the same key, so the export panel isn't a special
    case that has to be hidden."""
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()

    client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)

    assert session_id in api.SESSIONS
    assert api.SESSIONS[session_id]["rehydrated"] is True
    status = client.get(f"/api/export/{session_id}/status")
    assert status.status_code == 200, status.text


def test_replay_is_json_serialisable_with_missing_cells(
    client, admin_on, history_on, grouping_llm, tmp_path
):
    """The trap the superseded design fell into: the raw `data` key keeps NaN, and
    Starlette renders with allow_nan=False, so returning it 500s on any report with
    one missing cell. _response_of must drop it on this path too."""
    path = tmp_path / "orders.csv"
    pd.DataFrame({
        "region": ["North", "South", None],
        "amount": [10, None, 30],
    }).to_csv(path, index=False)

    session_id = _saved_session(client, [path])
    api.SESSIONS.clear()

    response = client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)
    assert response.status_code == 200, response.text
    assert "data" not in response.json()


def test_multi_sheet_workbook_replays(
    client, admin_on, history_on, grouping_llm, workbook
):
    """Worksheets never existed as files, so their table keys are synthesised from
    the workbook's own name — "<sheet> (<stem>).xlsx". The saved copy has to keep
    that name exactly, or the recommendation resolves against nothing."""
    session_id = _saved_session(client, [workbook])
    live = client.post(
        "/api/generate-report", json={"session_id": session_id, "report_type": "A"}
    ).json()
    api.SESSIONS.clear()

    response = client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_files"] == ["Items (sales).xlsx"]
    assert body["source_files"] == live["source_files"]
    assert body["rows"] == live["rows"]


def test_sheet_selection_round_trips_through_replay(
    client, admin_on, history_on, grouping_llm, workbook
):
    """With one sheet selected, DataLoader collapses the key back to the workbook's
    own name — the parenthesised form is only warranted when a sheet has siblings.

    That is precisely why sheet_selections is mandatory in the manifest rather than
    optional metadata: replay it without them and both sheets load, the key becomes
    "Orders (sales).xlsx", "sales.xlsx" no longer exists, and the report 422s.
    """
    session_id = _saved_session(client, [workbook], {"sales.xlsx": ["Orders"]})
    assert session_store.load_manifest(session_id)["sheet_selections"] == {
        "sales.xlsx": ["Orders"]
    }
    api.SESSIONS.clear()

    response = client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["source_files"] == ["sales.xlsx"]
    # Only the selected sheet's three rows were grouped, not Items' two.
    assert response.json()["report_rows"] == 2


def test_replay_of_a_never_saved_session_is_404(client, admin_on):
    response = client.get("/api/admin/sessions/nosuch/reports/A", headers=AUTH)
    assert response.status_code == 404


def test_replay_of_an_expired_session_is_410_not_500(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """Deleting session_data/<id>/source/ by hand is the expected way to forget one
    run. It must degrade to a clear error the viewer can render."""
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()
    for f in (session_store.session_dir(session_id) / "source").iterdir():
        f.unlink()

    response = client.get(f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH)
    assert response.status_code == 410
    assert "no longer replayable" in response.json()["detail"]

    listed = client.get("/api/admin/sessions", headers=AUTH).json()["sessions"]
    entry = next(s for s in listed if s["session_id"] == session_id)
    assert entry["replayable"] is False


def test_replaying_a_letter_with_no_recommendation_is_422(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """D6: a replay that fails where the live run succeeded is a finding, and the
    error has to reach the viewer rather than becoming a blank panel."""
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()

    response = client.get(f"/api/admin/sessions/{session_id}/reports/Z", headers=AUTH)
    assert response.status_code == 422
    assert response.json()["detail"]


def test_replay_reuses_a_session_already_in_memory(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """Replaying B after A must not re-read the workbook, and must not lose A."""
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()

    assert client.get(
        f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH
    ).status_code == 200
    assert client.get(
        f"/api/admin/sessions/{session_id}/reports/B", headers=AUTH
    ).status_code == 200

    assert sorted(api.SESSIONS[session_id]["reports"]) == ["A", "B"]


def test_letters_are_case_insensitive(
    client, admin_on, history_on, grouping_llm, csv_file
):
    session_id = _saved_session(client, [csv_file])
    api.SESSIONS.clear()
    assert client.get(
        f"/api/admin/sessions/{session_id}/reports/a", headers=AUTH
    ).status_code == 200


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_removes_the_snapshot(
    client, admin_on, history_on, grouping_llm, csv_file
):
    session_id = _saved_session(client, [csv_file])

    response = client.delete(f"/api/admin/sessions/{session_id}", headers=AUTH)
    assert response.status_code == 200
    assert client.get("/api/admin/sessions", headers=AUTH).json()["sessions"] == []
    assert not session_store.session_dir(session_id).exists()


def test_delete_of_an_unknown_session_is_404(client, admin_on):
    assert client.delete("/api/admin/sessions/nosuch", headers=AUTH).status_code == 404


def test_delete_is_gated_too(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.delete("/api/admin/sessions/x", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def _prune(client, **body):
    return client.post("/api/admin/sessions/prune", json=body, headers=AUTH)


def _save_snapshots(*session_ids, ages=None):
    """Write bare snapshots directly, without paying for an analysis each time."""
    from datetime import timedelta
    for i, sid in enumerate(session_ids):
        session_store.save_session_snapshot(
            session_id=sid, client_id="c", file_paths=[], sheet_selections=None,
            recommendations={"recommendations": [{"report_name": "R"}]},
            file_profiles=[], file_metadata=[{"name": "f.csv", "size": 1}],
        )
        if ages:
            path = session_store.session_dir(sid) / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["saved_at"] = (datetime.now() - timedelta(days=ages[i])).isoformat()
            path.write_text(json.dumps(manifest), encoding="utf-8")


def test_prune_is_gated(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.post("/api/admin/sessions/prune", json={"keep_newest": 0}).status_code == 404


def test_prune_with_no_criterion_is_400_not_a_massacre(client, admin_on):
    """An empty body must never mean "delete everything"."""
    _save_snapshots("20260101_000000_aaaaaa")

    response = _prune(client)
    assert response.status_code == 400
    assert "exactly one" in response.json()["detail"]
    assert len(client.get("/api/admin/sessions", headers=AUTH).json()["sessions"]) == 1


def test_prune_with_two_criteria_is_400(client, admin_on):
    response = _prune(client, older_than_days=30, keep_newest=1)
    assert response.status_code == 400


def test_prune_rejects_negative_values_at_the_schema(client, admin_on):
    """Caught by pydantic's ge=0 before it reaches the store — a 422, not a 400."""
    assert _prune(client, older_than_days=-5).status_code == 422
    assert _prune(client, keep_newest=-1).status_code == 422


def test_prune_defaults_to_a_dry_run(client, admin_on):
    """Omitting dry_run must preview, not delete. The dangerous direction has to be
    the one you type out."""
    _save_snapshots("20260101_000000_aaaaaa", "20260803_000000_bbbbbb")

    body = _prune(client, keep_newest=1).json()

    assert body["dry_run"] is True
    assert [m["session_id"] for m in body["matched"]] == ["20260101_000000_aaaaaa"]
    assert body["deleted"] == []
    assert len(client.get("/api/admin/sessions", headers=AUTH).json()["sessions"]) == 2


def test_prune_commits_when_told_to(client, admin_on):
    _save_snapshots("20260101_000000_aaaaaa", "20260803_000000_bbbbbb")

    body = _prune(client, keep_newest=1, dry_run=False).json()

    assert body["deleted"] == ["20260101_000000_aaaaaa"]
    assert body["remaining"] == 1
    listed = client.get("/api/admin/sessions", headers=AUTH).json()["sessions"]
    assert [s["session_id"] for s in listed] == ["20260803_000000_bbbbbb"]


def test_prune_by_age(client, admin_on):
    _save_snapshots(
        "20260101_000000_aaaaaa", "20260803_000000_bbbbbb", ages=[40, 1]
    )

    body = _prune(client, older_than_days=30, dry_run=False).json()
    assert body["deleted"] == ["20260101_000000_aaaaaa"]


def test_prune_by_explicit_ids(client, admin_on):
    _save_snapshots("20260101_000000_aaaaaa", "20260803_000000_bbbbbb")

    body = _prune(
        client, session_ids=["20260803_000000_bbbbbb"], dry_run=False
    ).json()
    assert body["deleted"] == ["20260803_000000_bbbbbb"]


def test_prune_drops_the_session_from_memory_too(
    client, admin_on, history_on, grouping_llm, csv_file
):
    """A pruned session left in SESSIONS would keep answering replays from a
    snapshot that no longer exists, then 404 confusingly after the next restart."""
    session_id = _saved_session(client, [csv_file])
    assert session_id in api.SESSIONS

    _prune(client, session_ids=[session_id], dry_run=False)

    assert session_id not in api.SESSIONS
    assert client.get(
        f"/api/admin/sessions/{session_id}/reports/A", headers=AUTH
    ).status_code == 404


def test_prune_unreplayable_only(client, admin_on, history_on, grouping_llm, csv_file):
    keep = _saved_session(client, [csv_file])
    _save_snapshots("20260101_000000_dead00")  # written with no source files at all

    body = _prune(client, keep_newest=0, unreplayable_only=True, dry_run=False).json()

    assert body["deleted"] == ["20260101_000000_dead00"]
    listed = client.get("/api/admin/sessions", headers=AUTH).json()["sessions"]
    assert [s["session_id"] for s in listed] == [keep]


def test_listing_reports_size_and_age(
    client, admin_on, history_on, grouping_llm, csv_file
):
    _saved_session(client, [csv_file])

    body = client.get("/api/admin/sessions", headers=AUTH).json()
    entry = body["sessions"][0]
    assert entry["bytes"] >= csv_file.stat().st_size
    assert entry["age_days"] is not None and entry["age_days"] < 1
    assert body["total_bytes"] == entry["bytes"]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def test_admin_stats_returns_the_parsed_event_log(
    client, admin_on, grouping_llm, csv_file
):
    """The point of routing through telemetry.recent_events rather than raw SQL:
    props comes back as a dict, not the stored JSON text."""
    _analyze(client, [csv_file])

    body = client.get("/api/admin/stats", headers=AUTH).json()
    assert body["summary"]["sessions"] >= 1
    assert body["events"], "no events recorded"
    assert all(isinstance(e["props"], dict) for e in body["events"])
    started = next(e for e in body["events"] if e["event"] == "analysis_started")
    assert started["props"]["file_count"] == 1
    assert body["files"], "no file rows recorded"


def test_admin_stats_respects_the_limit(client, admin_on, grouping_llm, csv_file):
    _analyze(client, [csv_file])
    body = client.get("/api/admin/stats?limit=1", headers=AUTH).json()
    assert len(body["events"]) == 1


def test_public_stats_stays_unauthenticated(client, admin_on):
    """The aggregate endpoint the home page reads must not be caught by the gate."""
    assert client.get("/api/stats").status_code == 200
