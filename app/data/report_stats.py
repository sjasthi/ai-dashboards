"""
report_stats.py

Computes real statistics from a generated report DataFrame, replacing the LLM's
pre-execution guesses (question_answered / data_quality_warning / rationale_bullets[0])
that the Reports dashboard used to show as if they were findings.

Everything here is derived from the report's own rows. Nothing is inferred, guessed,
or carried over from the model - so a number on the dashboard can always be traced
back to the data that produced it.

The output is intentionally one flat dict (plus a `blocks` list naming which optional
sections were populated) so the frontend never has to guess at the shape.

Usage (in api.py, inside generate_report_endpoint, after `chart = build_chart_figure(...)`):

    from app.data.report_stats import build_report_stats
    stats = build_report_stats(report_df, axes_config, pattern=rec.get("pattern_used"))
"""

import math
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.data.chart_builder import ADDITIVE_AGGS, humanize_column, split_agg_suffix

# Iglewicz-Hoaglin: flag |modified z| > 3.5, where modified z = 0.6745*(x - median)/MAD.
# Built on the median and MAD rather than the mean and standard deviation, because an
# outlier inflates the very std that is supposed to catch it (masking) - a single large
# spike can raise the bar above itself and go unflagged.
MAD_THRESHOLD = 3.5

# 0.6745 is the 0.75 quantile of the standard normal: it rescales MAD so that, for
# normally-distributed data, a modified z-score is comparable to an ordinary z-score.
_MAD_SCALE = 0.6745

# Tukey's fence, used as a cross-check on the MAD rule and as the fallback when MAD
# collapses to 0 (which happens whenever more than half the points share one value).
IQR_MULTIPLIER = 1.5

# Cap on how many flagged points travel in the response. The full count still ships as
# `anomaly_count`, so the UI can say "3 of 47" rather than implying the list is whole.
MAX_ANOMALIES_RETURNED = 20

# Below this, a trend is reported as flat. Without a dead band a 0.3% wobble between
# the first and last quarter of a series gets narrated as "trended up", which reads as
# a finding when it is noise.
TREND_DEAD_BAND_PCT = 2.0

# R-squared of the least-squares fit, used to qualify the trend's prose rather than to
# gate it. A direction with no fit behind it still gets reported - it just gets called
# weak instead of clear.
_R2_CLEAR = 0.5
_R2_MODERATE = 0.2

# Categories below this share of the total are "long tail" for a RANKING/COMPOSITION
# report - enough of them and the headline categories aren't really the story.
_TAIL_SHARE = 0.01

_STRFTIME_BY_GRANULARITY = {
    "yearly": "%Y",
    "monthly": "%b %Y",
    "weekly": "%d %b %Y",
    "daily": "%d %b %Y",
}


def build_report_stats(
    df: pd.DataFrame,
    plotly_config: Optional[Dict[str, Any]],
    pattern: Optional[str] = None,
    granularity: Optional[str] = None,
    llm_caveat: Optional[str] = None,
    schema_warning: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compute descriptive, trend, concentration and outlier statistics from a report
    DataFrame, using the same resolved axes the chart was drawn from.

    `pattern` is the recommendation's pattern_used (RANKING / DISTRIBUTION / TREND /
    COMPOSITION / COMPARISON / OUTLIER). It is passed through to the response as
    metadata only - which optional blocks appear is decided from the data itself
    (see _is_ordered_axis and the labels check below), not from this hint.

    Returns a dict with `available: bool` plus always-present prose fields
    (`top_insight_text` / `anomaly_text` / `recommendation_text` / `quality_text`),
    so callers can drop it into a response without extra branching.
    """
    quality = _quality_block(df, plotly_config, llm_caveat, schema_warning)

    if df is None or df.empty or not plotly_config:
        return _unavailable_result("The report returned no rows to analyze.", quality)

    resolved = _resolve_columns(df, plotly_config)
    if resolved is None:
        return _unavailable_result(
            "This report charts two categorical columns, so there is no numeric "
            "measure to compute statistics from.",
            quality,
        )
    value_col, label_col = resolved

    # Drop rows with no measure, but remember how many - a report that quietly lost a
    # third of its rows to nulls is itself the finding.
    n_before = len(df)
    keep = [c for c in (value_col, label_col) if c]
    data = df[keep].dropna(subset=[value_col]).reset_index(drop=True)
    if data.empty:
        return _unavailable_result(
            f"Every value in {humanize_column(value_col)} is missing.", quality
        )

    values = data[value_col].astype(float)
    labels = data[label_col] if label_col else None
    n = len(values)
    measure = humanize_column(value_col)

    # Label dates through the parsed values so every label reads unambiguously
    # ("1 Feb 2023" rather than "2/1/23", which means two different days depending on
    # who is reading it). Falls back to the raw labels for non-date axes. Note the
    # explicit `is None` test: a Series has no truth value to fall back on.
    display_labels = labels
    if labels is not None:
        parsed = _as_datetime(labels)
        if parsed is not None:
            display_labels = parsed

    result: Dict[str, Any] = {
        "available": True,
        "blocks": [],
        "pattern": (pattern or "").upper() or None,
        "measure_column": value_col,
        "measure_label": measure,
        "label_column": label_col,
        "label_axis_label": humanize_column(label_col) if label_col else None,
    }
    result.update(_descriptive_block(values, display_labels, n_before, granularity))

    is_ordered = _is_ordered_axis(labels) if labels is not None else False
    result["axis_is_ordered"] = is_ordered

    if is_ordered and n >= 4:
        result.update(_trend_block(data, values, label_col))
        result["blocks"].append("trend")

    if labels is not None and not is_ordered:
        concentration = _concentration_block(values, display_labels, granularity)
        if concentration:
            result.update(concentration)
            result["blocks"].append("concentration")

    result.update(_outlier_block(values, display_labels, granularity))
    result["blocks"].append("outliers")

    result.update(_headline(result, value_col))
    result.update(quality)
    result.pop("_sum_raw", None)

    result["top_insight_text"] = _insight_text(result, measure)
    result["anomaly_text"] = _anomaly_text(result, measure)
    result["recommendation_text"] = _recommendation_text(result, measure)

    return result


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------

def _resolve_columns(
    df: pd.DataFrame, plotly_config: Dict[str, Any]
) -> Optional[Tuple[str, Optional[str]]]:
    """Pick (value_col, label_col) from the resolved axes.

    Normal case: y_axis is the aggregated measure (groupby count/sum/mean) and x_axis
    is the label. Raw-histogram case (see chart_builder): no y_axis, and x_axis itself
    is the raw continuous column being binned, so it is the measure and there are no
    labels. Returns None when neither axis is numeric.
    """
    x_axis = plotly_config.get("x_axis")
    y_axis = plotly_config.get("y_axis")

    if y_axis and y_axis in df.columns and pd.api.types.is_numeric_dtype(df[y_axis]):
        label_col = x_axis if x_axis and x_axis in df.columns else None
        return y_axis, label_col

    if not y_axis and x_axis and x_axis in df.columns and pd.api.types.is_numeric_dtype(df[x_axis]):
        return x_axis, None

    return None


def _as_datetime(labels: Optional[pd.Series]) -> Optional[pd.Series]:
    """The label column parsed as datetimes, or None if it isn't a date axis.

    Dates routinely survive the pipeline as *strings* ("2/1/23"), because nothing
    upstream coerces them - report_builder only coerces numerics. Without this probe a
    90-day time series looks like 90 unordered categories, and the report gets
    described as a ranking: "the top 3 of 90 categories account for 4.3% of total".
    chart_builder._ordered_xy already probes the same way, so this keeps the numbers
    consistent with the chart the reader is looking at.
    """
    if labels is None or labels.empty:
        return None
    if pd.api.types.is_datetime64_any_dtype(labels.dtype):
        return labels
    if not pd.api.types.is_object_dtype(labels.dtype):
        return None
    with warnings.catch_warnings():
        # Report dates arrive in varied formats, so let pandas infer per element;
        # the resulting "could not infer format" warning is expected.
        warnings.simplefilter("ignore", UserWarning)
        parsed = pd.to_datetime(labels, errors="coerce")
    # All-or-nothing: a column where only some values parse is not a date axis.
    return parsed if parsed.notna().all() else None


def _is_ordered_axis(labels: pd.Series) -> bool:
    """Is this axis a real sequence (time, numeric bins) rather than an unordered
    category like genre or region?

    Ordered Categoricals from report_builder's bin/quantile derive step count, and so
    do datetimes - including date-shaped strings. A numeric axis counts only if its
    values repeat no more than they have to: an all-unique integer column is far more
    often an ID than a position on a scale, and treating an ID axis as a sequence
    manufactures a "trend" out of row order.
    """
    if labels is None:
        return False
    dtype = labels.dtype
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return True
    if isinstance(dtype, pd.CategoricalDtype):
        return bool(dtype.ordered)
    if pd.api.types.is_object_dtype(dtype):
        return _as_datetime(labels) is not None
    if pd.api.types.is_numeric_dtype(dtype):
        # An ID column is typically unique per row and monotonic in row order; a real
        # ordered axis (bins, years, months) has far fewer distinct values than rows,
        # or at least isn't just a row counter.
        n = len(labels)
        if n < 4:
            return False
        distinct = labels.nunique(dropna=True)
        if distinct < n:
            return True
        return not _looks_like_row_counter(labels)
    return False


def _looks_like_row_counter(labels: pd.Series) -> bool:
    """True when a unique numeric column is a plain 1..n / 0..n-1 sequence."""
    v = pd.to_numeric(labels, errors="coerce").dropna().sort_values()
    if len(v) < 4:
        return False
    diffs = v.diff().dropna()
    return bool((diffs == 1).all())


# ---------------------------------------------------------------------------
# Stat blocks
# ---------------------------------------------------------------------------

def _descriptive_block(
    values: pd.Series,
    labels: Optional[pd.Series],
    n_before: int,
    granularity: Optional[str],
) -> Dict[str, Any]:
    """Core five-number summary plus dispersion and shape. Always computed."""
    n = len(values)
    mean = float(values.mean())
    median = float(values.median())
    std = float(values.std(ddof=0))
    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    iqr = p75 - p25

    out: Dict[str, Any] = {
        "count": n,
        "dropped_null_rows": n_before - n,
        # Kept unrounded and un-exposed; _headline decides whether a total of this
        # measure means anything before it reaches the response.
        "_sum_raw": float(values.sum()),
        "min": float(values.min()),
        "p25": _round(p25),
        "median": _round(median),
        "p75": _round(p75),
        "max": float(values.max()),
        "mean": _round(mean),
        "std": _round(std),
        "iqr": _round(iqr),
        # Coefficient of variation: dispersion relative to level, so a series can be
        # called volatile or steady without knowing its units. Undefined at mean 0.
        "cv": _round(abs(std / mean) * 100) if mean else None,
        "distinct_labels": int(labels.nunique(dropna=True)) if labels is not None else None,
        "pct_within_1sd": _round(
            float(((values - mean).abs() <= std).mean() * 100)
        ) if std > 0 else None,
    }

    # Skew: mean pulled well off the median means a tail is dragging the average, and
    # the average is then the wrong summary to lead with. Scaled by IQR so the test is
    # unit-free; falls back to the range when the middle half is degenerate.
    scale = iqr or (out["max"] - out["min"])
    if scale > 0:
        divergence = (mean - median) / scale
        out["skew_ratio"] = _round(divergence)
        if divergence > 0.1:
            out["skew_flag"] = "right"
        elif divergence < -0.1:
            out["skew_flag"] = "left"
        else:
            out["skew_flag"] = None
    else:
        out["skew_ratio"] = None
        out["skew_flag"] = None

    peak_pos = int(values.values.argmax())
    trough_pos = int(values.values.argmin())
    out["peak_value"] = float(values.iloc[peak_pos])
    out["trough_value"] = float(values.iloc[trough_pos])
    out["peak_label"] = _fmt_label(labels.iloc[peak_pos], granularity) if labels is not None else None
    out["trough_label"] = _fmt_label(labels.iloc[trough_pos], granularity) if labels is not None else None

    return out


def _trend_block(data: pd.DataFrame, values: pd.Series, label_col: str) -> Dict[str, Any]:
    """Direction, magnitude and *confidence* of movement along an ordered axis.

    The first-vs-last-quarter comparison answers "how much did it move"; the
    least-squares fit answers "is that movement the shape of the data or two noisy
    endpoints". Reporting only the former is how a random walk becomes a trend.
    """
    # Sort by the parsed date when the axis is date-shaped strings. A lexical sort
    # would put "2/10/23" before "2/2/23", which silently scrambles the sequence the
    # trend is measured over.
    work = data.assign(_v=values.values)
    as_dates = _as_datetime(data[label_col])
    if as_dates is not None:
        work = work.assign(_order=as_dates.values).sort_values("_order")
    else:
        work = work.sort_values(label_col)
    ordered = work.reset_index(drop=True)
    series = ordered["_v"].astype(float)
    n = len(series)

    q = max(1, n // 4)
    early_mean = float(series.iloc[:q].mean())
    late_mean = float(series.iloc[-q:].mean())
    pct = _safe_pct_change(early_mean, late_mean)

    slope, r2 = _least_squares(series)

    if abs(pct) < TREND_DEAD_BAND_PCT:
        direction = "flat"
    else:
        direction = "up" if pct > 0 else "down"

    if r2 is None:
        strength = None
    elif r2 >= _R2_CLEAR:
        strength = "clear"
    elif r2 >= _R2_MODERATE:
        strength = "moderate"
    else:
        strength = "weak"

    out: Dict[str, Any] = {
        "trend_pct_change": _round(pct, 1),
        "trend_direction": direction,
        "trend_slope": _round(slope, 4) if slope is not None else None,
        "trend_r2": _round(r2, 3) if r2 is not None else None,
        "trend_strength": strength,
        "trend_early_mean": _round(early_mean),
        "trend_late_mean": _round(late_mean),
        "trend_window_size": q,
        "longest_run_up": _longest_run(series, up=True),
        "longest_run_down": _longest_run(series, up=False),
        # A 12-point resample for the KPI tile's sparkline. Deliberately small: the
        # tile shows shape, the chart shows detail.
        "sparkline": _sparkline(series),
    }

    # Last point vs the run-up to it - the "is right now unusual" question a trend
    # percentage over the whole period can't answer.
    if n >= 4:
        prior = float(series.iloc[:-1].mean())
        out["last_value"] = float(series.iloc[-1])
        out["last_vs_prior_mean_pct"] = _round(_safe_pct_change(prior, float(series.iloc[-1])), 1)

    out["period_gaps"] = _period_gaps(ordered["_order"]) if as_dates is not None else None
    return out


def _concentration_block(
    values: pd.Series, labels: pd.Series, granularity: Optional[str]
) -> Optional[Dict[str, Any]]:
    """How much of the total sits in the largest few categories.

    Only meaningful for non-negative additive measures - a share of a total that
    mixes positive and negative values isn't a share of anything.
    """
    if (values < 0).any():
        return None
    total = float(values.sum())
    if total <= 0:
        return None

    # Rank by position rather than index label, so this holds regardless of what the
    # upstream pipeline left the index looking like. One row per category is the norm
    # here (these axes come out of a groupby), so a row share is a category share.
    order = np.argsort(-values.to_numpy())
    shares = values.to_numpy()[order] / total
    n_cat = int(labels.nunique(dropna=True))

    top_labels = [_fmt_label(labels.iloc[int(pos)], granularity) for pos in order[:3]]

    return {
        "total": _round(total),
        "n_categories": n_cat,
        "top1_share": _round(float(shares[0]) * 100),
        "top3_share": _round(float(shares[:3].sum()) * 100),
        "top_labels": top_labels,
        "tail_count": int((shares < _TAIL_SHARE).sum()),
    }


def _outlier_block(
    values: pd.Series, labels: Optional[pd.Series], granularity: Optional[str]
) -> Dict[str, Any]:
    """Robust outlier detection: modified z-score on the MAD, cross-checked by Tukey.

    Both rules are median-based, so - unlike a plain z-score - the outliers being
    hunted don't get to raise the threshold that catches them.
    """
    n = len(values)
    if n < 4:
        return {"anomalies": [], "anomaly_count": 0, "anomaly_method": None, "anomaly_threshold": None}

    median = float(values.median())
    mad = float((values - median).abs().median())

    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    iqr = p75 - p25
    lo_fence = p25 - IQR_MULTIPLIER * iqr
    hi_fence = p75 + IQR_MULTIPLIER * iqr

    deviations = (values - median).abs()

    if mad > 0:
        method = "modified z-score (MAD)"
        threshold = MAD_THRESHOLD
        scores = _MAD_SCALE * (values - median) / mad
        flagged = scores.abs() > MAD_THRESHOLD
    elif iqr > 0:
        # MAD collapses to 0 as soon as over half the points share a single value.
        # Tukey's fence still has something to say there, so fall through to it rather
        # than reporting "no outliers" for a spike sitting on a plateau.
        method = "Tukey fence (1.5x IQR)"
        threshold = IQR_MULTIPLIER
        scores = pd.Series(np.nan, index=values.index)
        flagged = (values < lo_fence) | (values > hi_fence)
    elif deviations.max() > 0 and float((deviations > 0).mean()) <= 0.25:
        # Both robust scales are zero, so the middle half of the data is one repeated
        # value. A handful of points departing from a constant baseline is exactly the
        # shape of "flat line with a spike" - the case a reader most obviously calls an
        # outlier and the one both rules above are blind to. Requiring the departures
        # to be a small minority keeps this from firing on genuinely bimodal data,
        # where there is no baseline to be an outlier from.
        method = "departure from a constant baseline"
        threshold = None
        scores = pd.Series(np.nan, index=values.index)
        flagged = deviations > 0
    else:
        return {"anomalies": [], "anomaly_count": 0, "anomaly_method": None, "anomaly_threshold": None}

    anomalies: List[Dict[str, Any]] = []
    for pos in np.flatnonzero(flagged.to_numpy()):
        pos = int(pos)
        value = float(values.iloc[pos])
        score = scores.iloc[pos]
        anomalies.append({
            "label": _fmt_label(labels.iloc[pos], granularity) if labels is not None else f"row {pos + 1}",
            "value": value,
            "score": _round(float(score), 2) if not _isnan(score) else None,
            # Distance from the median, so the two score-less methods still have
            # something to rank by.
            "deviation": _round(float(deviations.iloc[pos])),
            "direction": "high" if value > median else "low",
            "outside_fence": bool(value < lo_fence or value > hi_fence) if iqr > 0 else None,
        })

    anomalies.sort(key=lambda a: a["deviation"] or 0, reverse=True)

    # A wide report can flag more points than any sidebar can show. Send the most
    # extreme few and report the true count alongside, so the UI never implies the
    # list is complete.
    shown = anomalies[:MAX_ANOMALIES_RETURNED]

    return {
        "anomalies": shown,
        "anomaly_count": len(anomalies),
        "anomaly_method": method,
        "anomaly_threshold": threshold,
        "fence_low": _round(lo_fence) if iqr > 0 else None,
        "fence_high": _round(hi_fence) if iqr > 0 else None,
    }


def _quality_block(
    df: Optional[pd.DataFrame],
    plotly_config: Optional[Dict[str, Any]],
    llm_caveat: Optional[str],
    schema_warning: Optional[str],
) -> Dict[str, Any]:
    """Completeness measured on the report itself, plus any warning carried in.

    The LLM's caveat is kept but kept *separate* - it is a guess made before the data
    was aggregated, and the frontend labels it as such rather than mixing it in with
    measured null counts.
    """
    null_pct = None
    null_count = None
    if df is not None and not df.empty and plotly_config:
        resolved = _resolve_columns(df, plotly_config)
        if resolved:
            col = resolved[0]
            null_count = int(df[col].isna().sum())
            null_pct = _round(null_count / len(df) * 100)

    text_parts: List[str] = []
    if null_count:
        text_parts.append(f"{null_count} of {len(df)} rows ({null_pct}%) have no measured value.")
    elif null_count == 0:
        text_parts.append("No missing values in the charted measure.")
    if schema_warning:
        text_parts.append(f"Schema: {schema_warning}")

    return {
        "null_count": null_count,
        "null_pct": null_pct,
        "schema_warning": schema_warning,
        "llm_caveat": llm_caveat,
        "quality_text": " ".join(text_parts) if text_parts else None,
    }


def _headline(stats: Dict[str, Any], value_col: str) -> Dict[str, Any]:
    """The single number the dashboard leads with.

    A total for additive measures (sums, counts, distinct counts), an average for
    everything else - adding up a column of means or standard deviations produces a
    number that looks authoritative and means nothing.
    """
    base, agg = split_agg_suffix(value_col)
    base_label = humanize_column(value_col)

    if agg in ADDITIVE_AGGS:
        total = _round(stats["_sum_raw"])
        return {
            "headline_label": f"Total {humanize_column(base)}",
            "headline_value": total,
            "headline_sublabel": f"across {stats['count']:,} data points",
            "sum": total,
            "sum_is_meaningful": True,
        }

    return {
        "headline_label": f"Average {base_label}",
        "headline_value": stats["mean"],
        "headline_sublabel": f"± {stats['std']} std dev",
        "sum": None,
        "sum_is_meaningful": False,
    }


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _least_squares(series: pd.Series) -> Tuple[Optional[float], Optional[float]]:
    """Slope and R-squared of an ordinary least-squares fit against position.

    Fitting against position rather than the x values themselves keeps this defined
    for ordered Categorical axes (bins, quantiles), which have order but no spacing.
    """
    y = series.to_numpy(dtype=float)
    n = len(y)
    if n < 3:
        return None, None
    x = np.arange(n, dtype=float)
    if np.ptp(y) == 0:
        return 0.0, 0.0  # perfectly flat: a real answer, not a failure
    try:
        slope, intercept = np.polyfit(x, y, 1)
    except (np.linalg.LinAlgError, ValueError):
        return None, None
    predicted = slope * x + intercept
    ss_res = float(((y - predicted) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), max(0.0, min(1.0, r2))


def _longest_run(series: pd.Series, up: bool) -> int:
    """Longest streak of consecutive step-over-step moves in one direction."""
    diffs = series.diff().dropna().to_numpy()
    if diffs.size == 0:
        return 0
    wanted = diffs > 0 if up else diffs < 0
    best = current = 0
    for flag in wanted:
        current = current + 1 if flag else 0
        best = max(best, current)
    return int(best)


def _sparkline(series: pd.Series, points: int = 12) -> List[float]:
    """Down-sample to at most `points` values for a stat-tile sparkline."""
    n = len(series)
    if n <= points:
        return [_round(float(v)) for v in series]
    idx = np.linspace(0, n - 1, points).round().astype(int)
    return [_round(float(series.iloc[int(i)])) for i in idx]


def _period_gaps(labels: Optional[pd.Series]) -> Optional[Dict[str, Any]]:
    """For a datetime axis, how many periods are missing from the sequence.

    A daily report covering 90 days with only 84 rows has six silent holes in it, and
    the chart draws straight through them as though nothing were missing.
    """
    if labels is None or not pd.api.types.is_datetime64_any_dtype(labels):
        return None
    ts = pd.Series(pd.to_datetime(labels, errors="coerce")).dropna().sort_values()
    if len(ts) < 3:
        return None
    deltas = ts.diff().dropna()
    step = deltas.mode()
    if step.empty:
        return None
    step = step.iloc[0]
    if step <= pd.Timedelta(0):
        return None
    span = ts.iloc[-1] - ts.iloc[0]
    expected = int(round(span / step)) + 1
    missing = expected - len(ts)
    if missing <= 0:
        return None
    return {
        "expected_periods": expected,
        "actual_periods": int(len(ts)),
        "missing_periods": int(missing),
    }


def _safe_pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return ((new - old) / abs(old)) * 100.0


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    if value is None or _isnan(value):
        return None
    return round(float(value), places)


def _isnan(value: Any) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _fmt_label(value: Any, granularity: Optional[str] = None) -> str:
    """Render an axis label for prose.

    Dates are formatted at the dataset's own granularity where known. Note that a
    date-only Timestamp still carries a midnight `time()`, and `datetime.time(0, 0)`
    has been truthy since Python 3.5 - so testing `if value.time()` to detect a
    date-only value silently never fires. Granularity comes from the file profile
    instead, and falls back to inspecting the timestamp directly.
    """
    if isinstance(value, pd.Timestamp) or isinstance(value, np.datetime64):
        ts = pd.Timestamp(value)
        fmt = _STRFTIME_BY_GRANULARITY.get((granularity or "").lower())
        if fmt is None:
            has_clock = bool(ts.hour or ts.minute or ts.second or ts.microsecond)
            fmt = "%d %b %Y %H:%M" if has_clock else "%d %b %Y"
        return ts.strftime(fmt).lstrip("0")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---------------------------------------------------------------------------
# Prose
# ---------------------------------------------------------------------------

def _insight_text(stats: Dict[str, Any], measure: str) -> str:
    """The key finding - a statement, never a question, and never a claim stronger
    than the fit behind it."""
    peak = _at(stats["peak_value"], stats.get("peak_label"))

    if "trend" in stats["blocks"]:
        direction = stats["trend_direction"]
        strength = stats["trend_strength"]
        pct = abs(stats["trend_pct_change"] or 0)
        r2 = stats.get("trend_r2")

        if direction == "flat":
            lead = (
                f"{measure} held steady across the period "
                f"(change of {pct:.1f}%, within normal variation)."
            )
        else:
            qualifier = ""
            if strength == "weak":
                qualifier = f", though the movement is noisy (R² {r2})"
            elif strength == "moderate":
                qualifier = f" (R² {r2})"
            lead = f"{measure} trended {direction} {pct:.1f}% from start to end of the period{qualifier}."
        return f"{lead} Highest was {peak}."

    if "concentration" in stats["blocks"]:
        top3 = stats["top3_share"]
        n_cat = stats["n_categories"]
        shown = min(3, n_cat)
        return (
            f"The top {shown} of {n_cat} categories account for {top3:.1f}% of total "
            f"{measure}. Highest was {peak}."
        )

    return (
        f"Highest {measure} was {peak}; the median across "
        f"{stats['count']:,} data points is {stats['median']}."
    )


def _anomaly_text(stats: Dict[str, Any], measure: str) -> str:
    anomalies = stats.get("anomalies") or []
    total = stats.get("anomaly_count") or 0
    method = stats.get("anomaly_method")

    if not anomalies:
        if not method:
            return f"Too few data points to test {measure} for outliers."
        return f"No outliers in {measure} — every point falls within the {method} threshold."

    top = anomalies[:3]
    parts = []
    for a in top:
        score = f", score {a['score']:+.2f}" if a["score"] is not None else ""
        parts.append(f"{a['label']} ({_num(a['value'])}{score})")
    noun = "outlier" if total == 1 else "outliers"
    more = f" (+{total - len(top)} more)" if total > len(top) else ""
    return f"{total} {noun} in {measure} by {method}: " + ", ".join(parts) + more + "."


def _recommendation_text(stats: Dict[str, Any], measure: str) -> str:
    """What to look at next - tied to whichever block actually found something."""
    gaps = stats.get("period_gaps")
    if gaps:
        return (
            f"{gaps['missing_periods']} period(s) are missing from the sequence "
            f"({gaps['actual_periods']} of {gaps['expected_periods']} expected). The chart "
            f"draws straight through those gaps — confirm whether the data is absent or the value is genuinely zero."
        )

    if stats.get("null_count"):
        return (
            f"{stats['null_pct']}% of rows have no {measure} value and were excluded "
            f"from every number here. Check whether those rows are missing at random."
        )

    if "trend" in stats["blocks"] and stats["trend_direction"] != "flat":
        strength = stats["trend_strength"]
        direction = stats["trend_direction"]
        if strength == "weak":
            return (
                f"The {direction}ward movement in {measure} has little statistical support "
                f"(R² {stats['trend_r2']}) — treat it as noise until a longer period confirms it."
            )
        anchor = stats["peak_label"] if direction == "up" else stats["trough_label"]
        at = f" around {anchor}" if anchor else ""
        return f"{measure} is trending {direction} — worth investigating what changed{at}."

    # Tail advice outranks outlier advice for a ranking/composition report. The
    # detector will happily flag the leading category as extreme, but a large leader
    # is what such a report is *for* - telling the reader to go verify it is noise.
    if "concentration" in stats["blocks"] and stats.get("tail_count"):
        return (
            f"{stats['tail_count']} categories each hold under 1% of the total. Consider "
            f"grouping them so the chart's tail doesn't crowd out the leaders."
        )

    if stats.get("anomalies"):
        worst = stats["anomalies"][0]
        return (
            f"Review {worst['label']} ({_num(worst['value'])}) first — it is the furthest "
            f"from typical, and confirming whether it is genuine or a data error changes every summary above."
        )

    if stats.get("skew_flag"):
        side = stats["skew_flag"]
        return (
            f"{measure} is {side}-skewed, so the mean ({stats['mean']}) sits away from the "
            f"median ({stats['median']}). Prefer the median when summarizing this report."
        )

    return f"{measure} looks stable — no trend, outliers, or data gaps worth flagging."


def _at(value: float, label: Optional[str]) -> str:
    return f"{_num(value)} at {label}" if label else _num(value)


def _num(value: float) -> str:
    """Format a measure for prose: no decimals when the value is whole."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _unavailable_result(reason: str, quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = {
        "available": False,
        "blocks": [],
        "unavailable_reason": reason,
        "top_insight_text": None,
        "anomaly_text": None,
        "recommendation_text": None,
        "anomalies": [],
    }
    if quality:
        result.update(quality)
    return result
