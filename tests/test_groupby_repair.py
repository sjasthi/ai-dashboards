"""
Tests for the empty-"groupby_columns" defence.

The LLM intermittently returns a groupby step with `"groupby_columns": []` while
naming the group key correctly everywhere else in the same recommendation. That is
schema-valid, so it used to reach the user as a bare "No groupby columns specified"
on the report card. These tests cover both halves of the fix: report_builder
recovering the key it meant, and response_validator rejecting the cases where no key
can be recovered so the retry loop gets a chance at it.
"""

import pandas as pd
import pytest

from app.data.report_builder import (
    METADATA_COLUMNS,
    _infer_groupby_columns,
    _repair_missing_groupby_columns,
    generate_report,
)
from app.data.response_validator import parse_and_validate


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def deals_frame():
    """A fact table with a low-cardinality key ("sector") and a measure."""
    return pd.DataFrame({
        "account": ["Acme", "Betatech", "Isdom", "Cancity", "Zumgoity"],
        "sector": ["technology", "medical", "technology", "retail", "medical"],
        "close_value": [1000.0, 250.0, 4500.0, 50.0, 700.0],
    })


def groupby_op(groupby_columns=None, aggregations=None):
    return {
        "operation_type": "groupby",
        "files_involved": ["deals.csv"],
        "groupby_columns": [] if groupby_columns is None else groupby_columns,
        "aggregations": (
            [{"column": "close_value", "func": "sum"}] if aggregations is None else aggregations
        ),
    }


def recommendation(operations=None, schema=None, plotly=None, **overrides):
    rec = {
        "rank": 1,
        "report_name": "Revenue Performance by Sector",
        "question_answered": "Which industry sectors generate the highest total revenue?",
        "pattern_used": "COMPOSITION",
        "justification": {"column": "sector", "profile_evidence": "10 unique sectors"},
        "required_operations": [groupby_op()] if operations is None else operations,
        "expected_output_schema": [
            {"name": "sector", "type": "string"},
            {"name": "close_value_sum", "type": "float"},
        ] if schema is None else schema,
        "plotly_config": {
            "chart_type": "bar",
            "x_axis": "sector",
            "y_axis": "close_value_sum",
            "title": "Total Revenue by Sector",
        } if plotly is None else plotly,
        "rationale_bullets": ["a", "b", "c"],
    }
    rec.update(overrides)
    return rec


def response(*recs):
    """A full three-recommendation response, padded with valid ones."""
    recs = list(recs)
    while len(recs) < 3:
        recs.append(recommendation(
            operations=[groupby_op(groupby_columns=["sector"])],
            rank=len(recs) + 1,
        ))
    return {"dataset_overview": "deals", "recommendations": recs}


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def test_infers_group_key_from_expected_output_schema():
    rec = recommendation()
    assert _infer_groupby_columns(rec, groupby_op()) == ["sector"]


def test_ignores_aggregated_columns_when_inferring():
    """A schema entry carrying an aggregation suffix is a measure, not a key -
    grouping by one would produce a row per distinct total. "deal_count" is excluded
    on its suffix alone, even though it isn't this step's own aggregation output."""
    rec = recommendation(schema=[
        {"name": "close_value_sum", "type": "float"},
        {"name": "deal_count", "type": "int"},
        {"name": "sector", "type": "string"},
    ])
    assert _infer_groupby_columns(rec, groupby_op()) == ["sector"]


def test_falls_back_to_plotly_x_axis():
    rec = recommendation(schema=[])
    assert _infer_groupby_columns(rec, groupby_op()) == ["sector"]


def test_infers_the_count_per_value_shape():
    """Rule 2c's shape: the same column is both the key and the counted column, so
    "sector" must survive even though "sector" is what gets aggregated."""
    op = groupby_op(aggregations=[{"column": "sector", "func": "count"}])
    rec = recommendation(operations=[op], schema=[
        {"name": "sector", "type": "string"},
        {"name": "sector_count", "type": "int"},
    ])
    assert _infer_groupby_columns(rec, op) == ["sector"]


def test_returns_nothing_when_no_key_is_recoverable():
    rec = recommendation(
        schema=[{"name": "close_value_sum", "type": "float"}],
        plotly={"chart_type": "bar", "x_axis": "close_value_sum", "title": "t"},
    )
    assert _infer_groupby_columns(rec, groupby_op()) == []


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def test_repair_does_not_mutate_the_caller_s_operations():
    """The recommendations dict is also what the API returns to the frontend."""
    operations = [groupby_op()]
    repaired, notes = _repair_missing_groupby_columns(operations, recommendation())

    assert repaired[0]["groupby_columns"] == ["sector"]
    assert operations[0]["groupby_columns"] == []
    assert len(notes) == 1


def test_repair_leaves_a_populated_groupby_alone():
    operations = [groupby_op(groupby_columns=["account"])]
    repaired, notes = _repair_missing_groupby_columns(operations, recommendation())

    assert repaired[0]["groupby_columns"] == ["account"]
    assert notes == []


def test_repair_leaves_an_empty_no_op_groupby_alone():
    """No columns AND no aggregations is a stray step _execute_groupby already skips;
    inventing a groupby for it would turn a harmless no-op into a real aggregation."""
    operations = [groupby_op(aggregations=[])]
    repaired, notes = _repair_missing_groupby_columns(operations, recommendation())

    assert repaired[0]["groupby_columns"] == []
    assert notes == []


# ---------------------------------------------------------------------------
# End to end through generate_report
# ---------------------------------------------------------------------------

def test_report_builds_despite_empty_groupby_columns():
    recs = response(recommendation())
    df = generate_report(recs, report_type="A", tables={"deals.csv": deals_frame()})

    assert df.attrs.get("error") is None
    data = df.drop(columns=list(METADATA_COLUMNS))
    assert list(data.columns) == ["sector", "close_value_sum"]
    # Checked against independently-computed totals, not against the implementation.
    assert dict(zip(data["sector"], data["close_value_sum"])) == {
        "technology": 5500.0, "medical": 950.0, "retail": 50.0,
    }


def test_report_still_fails_when_no_key_can_be_inferred():
    rec = recommendation(
        schema=[{"name": "close_value_sum", "type": "float"}],
        plotly={"chart_type": "bar", "x_axis": "close_value_sum", "title": "t"},
    )
    df = generate_report(response(rec), report_type="A", tables={"deals.csv": deals_frame()})

    assert df.empty
    assert "No groupby columns specified" in df.attrs["error"]


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------

def test_validation_accepts_a_repairable_empty_groupby():
    """Rejecting this would spend a full extra generation on a report that the
    deterministic repair builds correctly anyway."""
    parsed = parse_and_validate(response(recommendation()), {"deals.csv"})

    assert parsed.recommendations[0].required_operations[0].groupby_columns == []


def test_validation_rejects_an_unrecoverable_empty_groupby():
    rec = recommendation(
        schema=[{"name": "close_value_sum", "type": "float"}],
        plotly={"chart_type": "bar", "x_axis": "close_value_sum", "title": "t"},
    )

    with pytest.raises(ValueError) as excinfo:
        parse_and_validate(response(rec), {"deals.csv"})

    message = str(excinfo.value)
    assert "Revenue Performance by Sector" in message
    assert "groupby_columns" in message


def test_validation_ignores_an_empty_no_op_groupby():
    rec = recommendation(operations=[
        {"operation_type": "filter", "files_involved": ["deals.csv"],
         "filter_conditions": [{"column": "close_value", "condition": "not_null"}]},
        groupby_op(aggregations=[]),
    ])
    parse_and_validate(response(rec), {"deals.csv"})
