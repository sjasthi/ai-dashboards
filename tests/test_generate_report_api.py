"""
Integration tests for POST /api/generate-report.

These drive the real endpoint against a hand-built session, so the response contract
the dashboard depends on is verified rather than assumed - including that the whole
payload actually survives JSON serialization (report DataFrames carry Timestamps,
numpy scalars, NaN and ordered Categoricals, none of which encode by default).
"""

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.data.report_builder import METADATA_COLUMNS


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture(autouse=True)
def clean_sessions():
    api.SESSIONS.clear()
    yield
    api.SESSIONS.clear()


def orders_frame(n=90, seed=42):
    """The screenshot's report shape: one row per day, a count per day."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "order_date": pd.date_range("2023-01-01", periods=n, freq="D"),
        "order_id_count": np.clip(rng.normal(135, 20, n), 60, None).round(),
    })


def recommendation(**overrides):
    rec = {
        "rank": 1,
        "report_name": "Order Frequency Trend",
        "question_answered": "How has the volume of orders changed over the first quarter?",
        "pattern_used": "TREND",
        "data_quality_warning": "A small portion of order records are missing item information.",
        "rationale_bullets": ["Visualizes the daily fluctuations in order volume."],
        "required_operations": [{
            "operation_type": "groupby",
            "files_involved": ["orders.csv"],
            "groupby_columns": ["order_date"],
            "aggregations": [{"column": "order_id", "func": "count"}],
        }],
        "plotly_config": {
            "chart_type": "line",
            "x_axis": "order_date",
            "y_axis": "order_id_count",
            "title": "Daily Order Volume Trend",
        },
    }
    rec.update(overrides)
    return rec


def make_session(monkeypatch, df=None, rec=None, session_id="testsession"):
    """Register a session and stub generate_report to return `df` for it."""
    df = orders_frame() if df is None else df
    rec = recommendation() if rec is None else rec

    staged = df.copy()
    for col in reversed(METADATA_COLUMNS):
        staged.insert(0, col, rec.get("report_name") if "name" in col else rec.get("rank"))

    api.SESSIONS[session_id] = {
        "files": [{"name": "orders.csv", "size": 1024, "rows": len(df)}],
        "status": "complete",
        "created_at": "2026-07-27T00:00:00",
        "file_profiles": [],
        "prompt": "",
        "recommendations": {"recommendations": [rec, rec, rec]},
        "analysis": {},
        "tables": {"orders.csv": df},
    }

    monkeypatch.setattr(api, "generate_report", lambda *a, **k: staged)
    return session_id, staged, rec


def post(client, session_id, report_type="A"):
    return client.post(
        "/api/generate-report",
        json={"session_id": session_id, "report_type": report_type},
    )


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

def test_response_is_json_serializable(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    res = post(client, sid)
    assert res.status_code == 200
    json.dumps(res.json())  # would raise on a stray Timestamp / numpy scalar


def test_stats_are_present_and_computed(client, monkeypatch):
    sid, staged, _ = make_session(monkeypatch)
    stats = post(client, sid).json()["stats"]

    assert stats["available"] is True
    assert stats["count"] == 90
    assert stats["mean"] == pytest.approx(staged["order_id_count"].mean(), abs=0.01)
    assert stats["median"] == pytest.approx(staged["order_id_count"].median())
    assert stats["peak_value"] == staged["order_id_count"].max()


def test_insight_text_is_a_finding_not_the_llm_question(client, monkeypatch):
    """The regression the whole rework targets: the model's question used to be
    displayed as the top insight."""
    sid, _, rec = make_session(monkeypatch)
    body = post(client, sid).json()

    assert body["stats"]["top_insight_text"] != rec["question_answered"]
    assert "?" not in body["stats"]["top_insight_text"]
    # The question is still carried, but as the report's subtitle rather than a finding.
    assert body["question_answered"] == rec["question_answered"]


def test_llm_caveat_is_attributed_not_presented_as_a_detected_anomaly(client, monkeypatch):
    sid, _, rec = make_session(monkeypatch)
    stats = post(client, sid).json()["stats"]

    assert stats["llm_caveat"] == rec["data_quality_warning"]
    assert stats["anomaly_text"] != rec["data_quality_warning"]


def test_chart_type_reports_the_recommendation_not_the_plotly_trace(client, monkeypatch):
    """chart_builder renders `line` as a Scatter trace, so reading the trace name
    labelled every line chart 'scatter'."""
    sid, _, _ = make_session(monkeypatch)
    body = post(client, sid).json()

    assert body["chart_type"] == "line"
    assert body["chart"]["data"][0]["type"] == "scatter"


def test_data_columns_exclude_bookkeeping_columns(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    body = post(client, sid).json()

    assert len(body["columns"]) == 4
    assert body["data_columns"] == ["order_date", "order_id_count"]
    for col in METADATA_COLUMNS:
        assert col not in body["data_columns"]


def test_rows_returned_for_the_table_view(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    body = post(client, sid).json()

    assert len(body["rows"]) == 90
    assert body["rows_truncated"] is False
    assert set(body["rows"][0]) == set(body["columns"])


def test_rows_are_capped_and_truncation_is_reported(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch, df=orders_frame(n=1200))
    body = post(client, sid).json()

    assert body["report_rows"] == 1200
    assert len(body["rows"]) == api.MAX_ROWS_RETURNED
    assert body["rows_truncated"] is True
    # The full set is still held server-side.
    assert len(api.SESSIONS[sid]["report"]["data"]) == 1200


def test_provenance_fields(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    body = post(client, sid).json()

    assert body["source_files"] == ["orders.csv"]
    assert body["pattern_used"] == "TREND"
    assert body["generated_at"]
    assert body["operations"] == ["group by order_date → count(order_id)"]


def test_nan_values_survive_as_null(client, monkeypatch):
    df = orders_frame(n=10)
    df.loc[3, "order_id_count"] = np.nan
    sid, _, _ = make_session(monkeypatch, df=df)
    body = post(client, sid).json()

    assert body["rows"][3]["order_id_count"] is None
    assert body["stats"]["dropped_null_rows"] == 1
    assert body["stats"]["null_count"] == 1


# ---------------------------------------------------------------------------
# Degraded paths - a failure in one part must not cost the user the rest
# ---------------------------------------------------------------------------

def test_stats_failure_does_not_break_the_report(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("stats exploded")

    monkeypatch.setattr(api, "build_report_stats", boom)
    body = post(client, sid).json()

    assert body["status"] == "generated"
    assert body["chart"] is not None
    assert body["stats"] == {"available": False}


def test_unavailable_stats_still_carry_a_reason(client, monkeypatch):
    """Two categorical axes: nothing numeric to compute, and the UI needs to say why."""
    df = pd.DataFrame({"region": ["N", "S", "E"], "segment": ["a", "b", "c"]})
    rec = recommendation(plotly_config={
        "chart_type": "bar", "x_axis": "region", "y_axis": "segment", "title": "t",
    })
    sid, _, _ = make_session(monkeypatch, df=df, rec=rec)
    stats = post(client, sid).json()["stats"]

    assert stats["available"] is False
    assert stats["unavailable_reason"]


def test_schema_warning_is_surfaced(client, monkeypatch):
    """report_builder computes this and it used to be discarded."""
    sid, staged, _ = make_session(monkeypatch)
    staged.attrs["schema_warning"] = "expected column 'total' not produced"

    body = post(client, sid).json()
    assert body["schema_warning"] == "expected column 'total' not produced"
    assert "expected column" in body["stats"]["quality_text"]


def test_missing_session_is_404(client):
    assert post(client, "nope").status_code == 404


def test_report_error_is_422(client, monkeypatch):
    sid, _, _ = make_session(monkeypatch)
    failed = pd.DataFrame()
    failed.attrs["error"] = "table not found"
    monkeypatch.setattr(api, "generate_report", lambda *a, **k: failed)

    res = post(client, sid)
    assert res.status_code == 422
    assert "table not found" in res.json()["detail"]


def test_categorical_axis_report_serializes(client, monkeypatch):
    """Ordered Categoricals come out of report_builder's bin/quantile step and are a
    known JSON-encoding hazard."""
    bands = pd.Categorical(
        ["0-25", "25-50", "50-75", "75-100"],
        categories=["0-25", "25-50", "50-75", "75-100"],
        ordered=True,
    )
    df = pd.DataFrame({"age_band": bands, "customer_id_count": [12, 40, 33, 9]})
    rec = recommendation(pattern_used="DISTRIBUTION", plotly_config={
        "chart_type": "bar", "x_axis": "age_band", "y_axis": "customer_id_count", "title": "t",
    })
    sid, _, _ = make_session(monkeypatch, df=df, rec=rec)
    body = post(client, sid).json()

    json.dumps(body)
    assert body["stats"]["available"] is True
    assert body["stats"]["axis_is_ordered"] is True
    assert body["rows"][0]["age_band"] == "0-25"


def test_ranking_report_gets_concentration_stats(client, monkeypatch):
    # 6 categories - the block requires the excluded tail to be at least as large
    # as the shown top-3 (n_cat >= 2 * top N), or "top 3" isn't a real subset.
    df = pd.DataFrame({
        "region": ["N", "S", "E", "W", "C", "X"],
        "revenue_sum": [500, 300, 100, 50, 30, 20],
    })
    rec = recommendation(pattern_used="RANKING", plotly_config={
        "chart_type": "bar", "x_axis": "region", "y_axis": "revenue_sum", "title": "t",
    })
    sid, _, _ = make_session(monkeypatch, df=df, rec=rec)
    stats = post(client, sid).json()["stats"]

    assert "concentration" in stats["blocks"]
    assert stats["top1_share"] == pytest.approx(50.0)
    assert stats["total"] == 1000
