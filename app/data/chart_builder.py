import json
import re
from typing import Any, Dict, Optional

import pandas as pd
import plotly.graph_objects as go

_CHART_TYPE_TRACE = {
    "bar": "bar",
    "pie": "pie",
    "line": "line",
    "scatter": "scatter",
    "histogram": "histogram",  # resolved to a real trace type below
}

# report_builder's groupby step renames aggregated columns to "{column}_{function}"
# (see report_builder._execute_groupby) - stripping a known suffix here lets us tell
# an aggregated column (e.g. "churn_signal_mean") from a raw pass-through one (e.g.
# "churn_signal") using only the column name, no access to the operations pipeline.
_AGG_SUFFIXES = {
    "mean": "avg",
    "sum": "total",
    "count": "count",
    "min": "min",
    "max": "max",
    "median": "median",
    "std": "std dev",
    "nunique": "unique count",
}

# Words that imply an aggregation was computed (a rate, an average, a total, ...).
# Applying one of these to a raw/un-aggregated column is how a display bug (ugly
# label) turns into a misleading-data bug (a label that claims something the chart
# doesn't actually show) - see _resolve_axis_label.
_AGG_IMPLYING_WORDS = re.compile(
    r"\b(rate|percent|ratio|average|avg|mean|total)\b|%", re.IGNORECASE
)


def _humanize_column(column: str) -> str:
    """Turn a raw/derived column name into a readable axis label, e.g.
    "lifetime_value_mean" -> "Lifetime Value (avg)", "age_group" -> "Age Group"."""
    base = column
    qualifier = None
    for suffix, word in _AGG_SUFFIXES.items():
        if column.endswith(f"_{suffix}"):
            base = column[: -len(suffix) - 1]
            qualifier = word
            break

    label = base.replace("_", " ").strip().title()
    return f"{label} ({qualifier})" if qualifier else label


def _resolve_axis_label(column: str, llm_label: Optional[str]) -> str:
    """Pick the axis title for `column`, preferring the LLM's `llm_label` but
    falling back to a mechanically-derived one whenever the LLM's text isn't
    trustworthy for this column - see the module-level comment on
    _AGG_IMPLYING_WORDS for why that check exists."""
    if not llm_label:
        return _humanize_column(column)

    is_aggregated = any(column.endswith(f"_{suffix}") for suffix in _AGG_SUFFIXES)
    if not is_aggregated and _AGG_IMPLYING_WORDS.search(llm_label):
        return _humanize_column(column)

    return llm_label


def build_chart_figure(df: pd.DataFrame, plotly_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build a Plotly figure (as a JSON-safe dict) from a report DataFrame and the
    AI recommendation's plotly_config ({"chart_type", "x_axis", "y_axis", "title"}).

    Returns None if the config or data isn't usable, so callers can fall back
    to showing no chart rather than erroring the whole report out.
    """
    if df is None or df.empty or not plotly_config:
        return None

    chart_type = (plotly_config.get("chart_type") or "bar").lower()
    x_axis = plotly_config.get("x_axis")
    y_axis = plotly_config.get("y_axis")
    title = plotly_config.get("title") or ""

    if not x_axis or x_axis not in df.columns:
        return None

    trace_type = _CHART_TYPE_TRACE.get(chart_type, "bar")
    has_y = bool(y_axis) and y_axis in df.columns

    # A "histogram" with no y_axis means the LLM wants Plotly to bin raw
    # continuous values itself (a plain DISTRIBUTION report: one column, no
    # pre-aggregation). A "histogram" WITH a y_axis means report_builder's
    # groupby step already pre-counted/pre-binned the data (x = bin/category
    # label, y = count) - that needs a Bar chart, since re-histogramming
    # already-aggregated counts would double-bin them.
    raw_histogram = trace_type == "histogram" and not has_y
    if trace_type == "histogram" and not raw_histogram:
        trace_type = "bar"

    if trace_type != "pie" and not raw_histogram and not has_y:
        return None

    if raw_histogram:
        fig = go.Figure(go.Histogram(x=df[x_axis].tolist()))
    else:
        # Categorical charts (bar/pie) get x forced to strings so pandas
        # Interval objects from a "bin" derive step (e.g. "(18, 30]") render
        # as plain display labels instead of Plotly guessing an axis type.
        # Scatter/line plot real (often continuous) values, so a numeric
        # x_axis must stay numeric - stringifying it would make Plotly treat
        # the axis as categorical, ordered by first appearance instead of by
        # value. Plain Python lists (not pandas Series/numpy arrays) keep
        # Plotly's JSON output as ordinary arrays instead of its compact
        # base64 array encoding, which older Plotly.js builds don't decode.
        if trace_type in ("bar", "pie"):
            x_values = df[x_axis].astype(str).tolist()
        else:
            x_values = df[x_axis].tolist()
        y_values = df[y_axis].tolist() if has_y else None

        if trace_type == "pie":
            fig = go.Figure(go.Pie(labels=x_values, values=y_values))
        elif trace_type == "line":
            fig = go.Figure(go.Scatter(x=x_values, y=y_values, mode="lines+markers"))
        elif trace_type == "scatter":
            fig = go.Figure(go.Scatter(x=x_values, y=y_values, mode="markers"))
        else:
            fig = go.Figure(go.Bar(x=x_values, y=y_values))

    x_label = _resolve_axis_label(x_axis, plotly_config.get("x_axis_label"))
    y_label = _resolve_axis_label(y_axis, plotly_config.get("y_axis_label")) if has_y else "Count"

    fig.update_layout(
        title=title,
        xaxis_title=x_label if trace_type != "pie" else None,
        yaxis_title=y_label if trace_type != "pie" else None,
        margin=dict(l=40, r=20, t=50, b=40),
        template=None,  # skip embedding Plotly's full default theme in every response
    )

    if trace_type != "pie" and not raw_histogram and has_y:
        # These reports mostly plot averages/rates (avg_session_time_mean,
        # churn_label_mean, ...) where the meaningful signal is in small
        # differences between categories, not in the absolute distance from
        # zero. A bar's own geometry is drawn from 0 to its value, so Plotly
        # includes 0 in the auto-computed data extent no matter what
        # rangemode says - that has to be overridden with an explicit
        # numeric range to actually zoom in on the data (e.g. 10.40 vs 10.52
        # instead of both looking like ~10 out of a 0-11 axis).
        y_values = df[y_axis].tolist()
        y_min, y_max = min(y_values), max(y_values)
        padding = (y_max - y_min) * 0.15 or abs(y_max) * 0.1 or 1
        fig.update_yaxes(range=[y_min - padding, y_max + padding])

    # Round-trip through Plotly's own JSON encoder so numpy/pandas types
    # (Timestamps, int64, etc.) are converted to plain JSON-safe values.
    return json.loads(fig.to_json())
