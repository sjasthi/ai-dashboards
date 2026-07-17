import json
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

    fig.update_layout(
        title=title,
        xaxis_title=x_axis if trace_type != "pie" else None,
        yaxis_title=(y_axis if has_y else "count") if trace_type != "pie" else None,
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
