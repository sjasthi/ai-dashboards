# delete me
"""
report_stats.py

Computes real statistics from a generated report DataFrame, to replace the
LLM's pre-execution guesses (question_answered / data_quality_warning /
rationale_bullets[0]) that Reportsdashboard.jsx currently falls back to.

Designed to slot into api.py's /api/generate-report, right after
resolve_plotly_axes() + build_chart_figure() are called - it reuses the same
resolved axes_config so it looks at the exact same x/y columns the chart uses.

Usage (in api.py, inside generate_report_endpoint, after `chart = build_chart_figure(...)`):

    from app.data.report_stats import build_report_stats
    stats = build_report_stats(report_df, axes_config)

    # then include in both the SESSIONS[...]["report"] dict and the return dict:
    "stats": stats,
"""

from typing import Any, Dict, Optional

import pandas as pd


def build_report_stats(df: pd.DataFrame, plotly_config: Optional[Dict[str, Any]], z_thresh: float = 2.0) -> Dict[str, Any]:
    """
    Computes descriptive stats, peak/trough, trend (if the x-axis is ordered),
    and z-score anomalies from the report's own data.

    Returns a dict with `available: bool` plus always-present `top_insight_text`
    / `anomaly_text` / `recommendation_text` (usable text even when the data
    doesn't support deeper stats), so callers can drop it straight into the
    response without extra branching.
    """
    empty = _unavailable_result()

    if df is None or df.empty or not plotly_config:
        return empty

    x_axis = plotly_config.get("x_axis")
    y_axis = plotly_config.get("y_axis")
    chart_type = (plotly_config.get("chart_type") or "").lower()

    # Pick which column holds the numbers to analyze. Normal case: y_axis is the
    # aggregated measure (e.g. groupby count/sum/mean) and x_axis is the label.
    # Raw-histogram case (see chart_builder.py): no y_axis, x_axis itself is the
    # raw continuous column being distributed.
    if y_axis and y_axis in df.columns and pd.api.types.is_numeric_dtype(df[y_axis]):
        value_col, label_col = y_axis, x_axis if x_axis in df.columns else None
    elif not y_axis and x_axis and x_axis in df.columns and pd.api.types.is_numeric_dtype(df[x_axis]):
        value_col, label_col = x_axis, None
    else:
        return empty  # no numeric column to analyze (e.g. two categorical axes)

    data = df[[c for c in [value_col, label_col] if c]].dropna(subset=[value_col])
    if data.empty:
        return empty

    values = data[value_col].astype(float)
    n = len(values)

    result: Dict[str, Any] = {
        "available": True,
        "count": n,
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": round(float(values.mean()), 2),
        "median": float(values.median()),
        "sum": round(float(values.sum()), 2),
    }

    labels = data[label_col] if label_col else None
    is_ordered = _is_ordered_axis(labels) if labels is not None else False

    # --- Peak / trough, with a human label if we have one ---
    peak_idx = values.idxmax()
    trough_idx = values.idxmin()
    result["peak_value"] = float(values.loc[peak_idx])
    result["peak_label"] = _fmt_label(labels.loc[peak_idx]) if labels is not None else None
    result["trough_value"] = float(values.loc[trough_idx])
    result["trough_label"] = _fmt_label(labels.loc[trough_idx]) if labels is not None else None

    # --- Trend: only meaningful when the axis has a real order (time, numeric bins) ---
    trend_pct = None
    trend_direction = None
    if is_ordered and n >= 4:
        ordered = data.assign(_v=values).sort_values(label_col)
        q = max(1, n // 4)
        early_mean = ordered["_v"].iloc[:q].mean()
        late_mean = ordered["_v"].iloc[-q:].mean()
        trend_pct = round(_safe_pct_change(early_mean, late_mean), 1)
        trend_direction = "up" if trend_pct > 0 else ("down" if trend_pct < 0 else "flat")
    result["trend_pct_change"] = trend_pct
    result["trend_direction"] = trend_direction

    # --- Anomalies via z-score (needs some spread and a handful of points) ---
    anomalies = []
    std = values.std(ddof=0)
    if std > 0 and n >= 4:
        mean = values.mean()
        z_scores = (values - mean) / std
        for idx in values[z_scores.abs() > z_thresh].index:
            anomalies.append({
                "label": _fmt_label(labels.loc[idx]) if labels is not None else str(idx),
                "value": float(values.loc[idx]),
                "z_score": round(float(z_scores.loc[idx]), 2),
            })
    result["anomalies"] = anomalies

    result["top_insight_text"] = _insight_text(result, value_col, is_ordered)
    result["anomaly_text"] = _anomaly_text(anomalies, value_col)
    result["recommendation_text"] = _recommendation_text(result, value_col, is_ordered)

    return result


def _is_ordered_axis(labels: pd.Series) -> bool:
    """Is this axis a real sequence (time/numeric) rather than an unordered
    category (e.g. genre, region)? Ordered Categoricals from report_builder's
    bin/quantile derive step count too."""
    dtype = labels.dtype
    if pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_datetime64_any_dtype(dtype):
        return True
    if isinstance(dtype, pd.CategoricalDtype) and dtype.ordered:
        return True
    return False


def _fmt_label(value) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d") if value.time() else value.strftime("%Y")
    return str(value)


def _safe_pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / abs(old)) * 100.0


def _insight_text(stats: Dict[str, Any], value_col: str, is_ordered: bool) -> str:
    peak_clause = (
        f"The highest value was {stats['peak_value']:.2f} at {stats['peak_label']}."
        if stats.get("peak_label") is not None
        else f"The highest value was {stats['peak_value']:.2f}."
    )
    if is_ordered and stats["trend_direction"]:
        direction = stats["trend_direction"]
        pct = abs(stats["trend_pct_change"])
        if direction == "flat":
            trend_clause = f"{value_col} stayed roughly flat across the period."
        else:
            trend_clause = f"{value_col} trended {direction} by {pct:.1f}% from the start to the end of the period."
        return f"{trend_clause} {peak_clause}"
    return f"{peak_clause} Average {value_col} across {stats['count']} data point(s) was {stats['mean']:.2f}."


def _anomaly_text(anomalies, value_col: str) -> str:
    if not anomalies:
        return f"No statistical outliers detected in {value_col} (all points within 2 standard deviations of the mean)."
    top = sorted(anomalies, key=lambda a: abs(a["z_score"]), reverse=True)[:3]
    parts = [f"{a['label']} ({a['value']:.2f}, z={a['z_score']:+.2f})" for a in top]
    return f"{len(anomalies)} outlier point(s) detected in {value_col}, most notably: " + ", ".join(parts) + "."


def _recommendation_text(stats: Dict[str, Any], value_col: str, is_ordered: bool) -> str:
    if is_ordered and stats["trend_direction"] == "up":
        return f"{value_col} is trending upward — worth investigating what's driving growth around {stats['peak_label']}."
    if is_ordered and stats["trend_direction"] == "down":
        return f"{value_col} is trending downward — worth checking whether this reflects a real decline or a data gap around {stats['trough_label']}."
    if stats["anomalies"]:
        return f"Review the flagged outlier(s) in {value_col} to confirm whether they're genuine or data errors."
    return f"{value_col} looks stable overall with no major trend or outliers to flag."


def _unavailable_result() -> Dict[str, Any]:
    return {
        "available": False,
        "top_insight_text": None,
        "anomaly_text": None,
        "recommendation_text": None,
        "anomalies": [],
    }
