import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go

_CHART_TYPE_TRACE = {
    "bar": "bar",
    "pie": "pie",
    "line": "line",
    "scatter": "scatter",
    "box": "box",
    "violin": "box",  # treated as a box; violin adds little for the small n these reports produce
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

# When a bar chart's values differ by less than this fraction of their magnitude and
# sit entirely above zero, a zero-based bar makes the differences invisible. Per the
# chart-design guide, the fix is NOT a truncated bar (a length encoding must start at
# zero) but a dot plot - a position encoding, which legitimately supports a zoomed,
# non-zero baseline. Below this ratio we switch bar -> dot.
SMALL_DIFF_RATIO = 0.25

# Hard caps enforced in code rather than trusting the LLM's "limit" (the prompt only
# asks for these). Guide: pie <= 5 slices (else a sorted bar / top-N + "Other"); bars
# stay readable up to ~15-25 categories.
MAX_PIE_SLICES = 5
MAX_BAR_CATEGORIES = 20

# Okabe-Ito: an 8-colour categorical palette designed to stay distinguishable under
# the common forms of colour-blindness. Used as a consistent colourway across reports.
COLORWAY = [
    "#0072B2", "#E69F00", "#009E73", "#D55E00",
    "#CC79A7", "#56B4E9", "#F0E442", "#000000",
]


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


def _numeric(values: List[Any]) -> List[float]:
    """Just the finite numbers from a list (drops None/NaN/strings)."""
    out = []
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out.append(float(v))
    return out


def _padded_range(values: List[Any]) -> Optional[List[float]]:
    """A zoomed [lo, hi] axis range padded ~15% around the data, for position-encoded
    charts (line/scatter/dot) where a non-zero baseline is legitimate and the signal
    is in the differences between points, not the distance from zero."""
    nums = _numeric(values)
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.1 or 1
    return [lo - pad, hi + pad]


def _is_small_positive_spread(values: List[Any]) -> bool:
    """True when the values sit entirely above zero and vary by less than
    SMALL_DIFF_RATIO of their magnitude - the case where a zero-based bar hides the
    real differences and a dot plot reads better (see SMALL_DIFF_RATIO)."""
    nums = _numeric(values)
    if len(nums) < 2:
        return False
    lo, hi = min(nums), max(nums)
    if lo <= 0 or hi <= lo:
        return False
    return (hi - lo) / abs(hi) < SMALL_DIFF_RATIO


def _fd_xbins(series: pd.Series) -> Optional[Dict[str, float]]:
    """Freedman-Diaconis bin spec (start/end/size) for a raw continuous column, or
    None when it can't be computed. Bin width = 2 * IQR * n^(-1/3) - a principled,
    data-driven choice, rather than letting the model (or Plotly's default) guess a
    round bin count that ignores the distribution's spread."""
    v = pd.to_numeric(series, errors="coerce").dropna()
    n = len(v)
    if n < 2:
        return None
    q1, q3 = v.quantile(0.25), v.quantile(0.75)
    iqr = float(q3 - q1)
    lo, hi = float(v.min()), float(v.max())
    if iqr <= 0 or hi <= lo:
        return None
    width = 2 * iqr * (n ** (-1.0 / 3.0))
    if width <= 0:
        return None
    return {"start": lo, "end": hi, "size": width}


def _cap_pie(labels: List[str], values: List[float]) -> Tuple[List[str], List[float]]:
    """Keep the top MAX_PIE_SLICES slices by value and roll the rest into a single
    "Other" slice, so a pie never shows more than the ~5 slices a reader can compare."""
    if len(labels) <= MAX_PIE_SLICES:
        return labels, values
    paired = sorted(zip(labels, values), key=lambda p: p[1], reverse=True)
    top = paired[:MAX_PIE_SLICES]
    other = sum(v for _, v in paired[MAX_PIE_SLICES:])
    return [l for l, _ in top] + ["Other"], [v for _, v in top] + [other]


def _prepare_categorical(df: pd.DataFrame, x_axis: str, y_axis: str) -> Tuple[List[str], List[float]]:
    """(labels, values) for a categorical value chart (bar/pie/dot), with ordering
    applied: an ordered Categorical x (from a bin/quantile derive) keeps its bucket
    order; anything else is sorted by value descending so the ranking reads top-down."""
    work = df[[x_axis, y_axis]].dropna(subset=[y_axis])
    x_dtype = df[x_axis].dtype
    ordered = isinstance(x_dtype, pd.CategoricalDtype) and x_dtype.ordered
    if ordered:
        work = work.sort_values(x_axis)
    else:
        work = work.sort_values(y_axis, ascending=False)

    labels = work[x_axis].astype(str).tolist()
    values = work[y_axis].tolist()

    # Enforce a category cap in code (only for unordered categories - dropping middle
    # buckets from an ordinal axis would misrepresent the distribution).
    if not ordered and len(labels) > MAX_BAR_CATEGORIES:
        labels = labels[:MAX_BAR_CATEGORIES]
        values = values[:MAX_BAR_CATEGORIES]

    return labels, values


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

    # A "histogram" with no y_axis means the LLM wants us to bin raw continuous values
    # (a plain DISTRIBUTION report: one column, no pre-aggregation). A "histogram" WITH
    # a y_axis means report_builder's groupby step already pre-counted/pre-binned the
    # data (x = bin/category label, y = count) - that needs a Bar chart, since
    # re-histogramming already-aggregated counts would double-bin them.
    raw_histogram = trace_type == "histogram" and not has_y
    if trace_type == "histogram" and not raw_histogram:
        trace_type = "bar"

    # bar/line/scatter/pie all need a y measure; box can plot a single distribution
    # from x alone; a raw histogram bins x itself.
    y_required = trace_type in ("bar", "line", "scatter", "pie")
    if y_required and not has_y:
        return None

    render_as_dot = False  # a bar converted to a dot plot for small-difference data

    # ---- Build the trace ----
    if raw_histogram:
        values = pd.to_numeric(df[x_axis], errors="coerce").dropna()
        fig = go.Figure(go.Histogram(x=values.tolist(), xbins=_fd_xbins(df[x_axis])))

    elif trace_type == "box":
        if has_y:
            # Grouped box: a distribution of y within each category of x.
            fig = go.Figure(go.Box(x=df[x_axis].astype(str).tolist(), y=df[y_axis].tolist()))
        else:
            # Single distribution of the x_axis column's raw values.
            vals = pd.to_numeric(df[x_axis], errors="coerce").dropna()
            fig = go.Figure(go.Box(y=vals.tolist()))

    elif trace_type in ("bar", "pie"):
        labels, values = _prepare_categorical(df, x_axis, y_axis)
        if trace_type == "pie":
            labels, values = _cap_pie(labels, values)
            fig = go.Figure(go.Pie(labels=labels, values=values))
        else:
            render_as_dot = _is_small_positive_spread(values)
            if render_as_dot:
                fig = go.Figure(go.Scatter(x=labels, y=values, mode="markers", marker=dict(size=11)))
            else:
                fig = go.Figure(go.Bar(x=labels, y=values))

    else:
        # line / scatter plot real (often continuous) values, so x stays as-is (numeric
        # x must NOT be stringified or Plotly treats the axis as categorical). Plain
        # Python lists keep Plotly's JSON as ordinary arrays, which older Plotly.js
        # builds decode reliably.
        x_values = df[x_axis].tolist()
        y_values = df[y_axis].tolist()
        if trace_type == "line":
            fig = go.Figure(go.Scatter(x=x_values, y=y_values, mode="lines+markers"))
        else:
            fig = go.Figure(go.Scatter(x=x_values, y=y_values, mode="markers"))

    # ---- Axis titles ----
    if trace_type == "box" and not has_y:
        x_label = None
        y_label = _resolve_axis_label(x_axis, plotly_config.get("x_axis_label"))
    else:
        x_label = _resolve_axis_label(x_axis, plotly_config.get("x_axis_label"))
        if has_y:
            y_label = _resolve_axis_label(y_axis, plotly_config.get("y_axis_label"))
        else:
            y_label = "Count"  # raw histogram

    is_pie = trace_type == "pie"
    fig.update_layout(
        title=title,
        colorway=COLORWAY,
        margin=dict(l=40, r=20, t=50, b=40),
        template=None,  # skip embedding Plotly's full default theme in every response
    )

    # automargin lets Plotly grow the plot margins to fit tick labels + axis titles,
    # and standoff pushes the title clear of the tick text so the two never overlap
    # (e.g. the vertical "Line Total (total)" landing on top of the "300k/250k" ticks).
    if not is_pie:
        fig.update_xaxes(title=dict(text=x_label, standoff=12), automargin=True)
        fig.update_yaxes(title=dict(text=y_label, standoff=12), automargin=True)

    # ---- Y-axis range ----
    # Bars keep their zero baseline (length encoding - the bar's own geometry must run
    # from 0). Position-encoded charts (line/scatter, and a bar demoted to a dot) can
    # zoom to the data so small differences stay legible.
    if render_as_dot:
        rng = _padded_range(values)
        if rng:
            fig.update_yaxes(range=rng)
    elif trace_type in ("line", "scatter") and has_y:
        rng = _padded_range(df[y_axis].tolist())
        if rng:
            fig.update_yaxes(range=rng)

    # Round-trip through Plotly's own JSON encoder so numpy/pandas types
    # (Timestamps, int64, etc.) are converted to plain JSON-safe values.
    return json.loads(fig.to_json())
