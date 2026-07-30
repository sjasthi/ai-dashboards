"""
Tests for app.data.report_stats.

The point of this module is that every number the dashboard shows can be trusted, so
these tests check the arithmetic against independently-computed expectations rather
than against whatever the implementation happens to return.
"""

import numpy as np
import pandas as pd
import pytest

from app.data.report_stats import (
    MAD_THRESHOLD,
    TREND_DEAD_BAND_PCT,
    _fmt_label,
    _is_ordered_axis,
    build_report_stats,
)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def cfg(chart_type="line", x="order_date", y="order_id_count", **kw):
    out = {"chart_type": chart_type, "x_axis": x, "title": "t"}
    if y is not None:
        out["y_axis"] = y
    out.update(kw)
    return out


def daily_report(values, start="2023-01-01"):
    """A TREND-shaped report: datetime x, count y - the screenshot's shape."""
    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({"order_date": dates, "order_id_count": values})


def category_report(labels, values):
    """A RANKING-shaped report: unordered categorical x, sum y."""
    return pd.DataFrame({"region": labels, "revenue_sum": values})


# ---------------------------------------------------------------------------
# Availability / guard rails
# ---------------------------------------------------------------------------

def test_empty_dataframe_is_unavailable():
    stats = build_report_stats(pd.DataFrame(), cfg())
    assert stats["available"] is False
    assert stats["unavailable_reason"]
    assert stats["anomalies"] == []


def test_missing_config_is_unavailable():
    assert build_report_stats(daily_report([1, 2, 3]), None)["available"] is False


def test_two_categorical_axes_is_unavailable():
    df = pd.DataFrame({"region": ["N", "S"], "segment": ["A", "B"]})
    stats = build_report_stats(df, cfg(chart_type="bar", x="region", y="segment"))
    assert stats["available"] is False
    assert "categorical" in stats["unavailable_reason"]


def test_all_null_measure_is_unavailable():
    df = daily_report([np.nan] * 10)
    stats = build_report_stats(df, cfg())
    assert stats["available"] is False
    assert "missing" in stats["unavailable_reason"]


def test_prose_fields_always_present_when_available():
    stats = build_report_stats(daily_report([1, 2, 3, 4, 5]), cfg())
    for key in ("top_insight_text", "anomaly_text", "recommendation_text"):
        assert isinstance(stats[key], str) and stats[key]


def test_insight_is_a_statement_not_a_question():
    """The regression this whole module exists to prevent."""
    stats = build_report_stats(daily_report([10, 12, 14, 16, 18, 20]), cfg())
    assert "?" not in stats["top_insight_text"]


# ---------------------------------------------------------------------------
# Descriptive stats - checked against pandas directly
# ---------------------------------------------------------------------------

def test_descriptive_stats_match_pandas():
    values = [10, 20, 30, 40, 50, 60, 70, 80]
    df = daily_report(values)
    s = pd.Series(values, dtype=float)
    stats = build_report_stats(df, cfg())

    assert stats["count"] == 8
    assert stats["min"] == s.min()
    assert stats["max"] == s.max()
    assert stats["mean"] == pytest.approx(s.mean())
    assert stats["median"] == pytest.approx(s.median())
    assert stats["p25"] == pytest.approx(s.quantile(0.25))
    assert stats["p75"] == pytest.approx(s.quantile(0.75))
    assert stats["std"] == pytest.approx(s.std(ddof=0), abs=0.01)
    assert stats["iqr"] == pytest.approx(s.quantile(0.75) - s.quantile(0.25))


def test_cv_is_percentage_of_mean():
    values = [100, 100, 100, 100, 110, 90, 100, 100]
    stats = build_report_stats(daily_report(values), cfg())
    s = pd.Series(values, dtype=float)
    assert stats["cv"] == pytest.approx(s.std(ddof=0) / s.mean() * 100, abs=0.01)


def test_peak_and_trough_carry_their_labels():
    values = [5, 99, 3, 7, 8, 9]
    stats = build_report_stats(daily_report(values, start="2023-03-01"), cfg())
    assert stats["peak_value"] == 99
    assert stats["peak_label"] == "2 Mar 2023"
    assert stats["trough_value"] == 3
    assert stats["trough_label"] == "3 Mar 2023"


def test_dropped_null_rows_counted():
    df = daily_report([1.0, np.nan, 3.0, np.nan, 5.0])
    stats = build_report_stats(df, cfg())
    assert stats["count"] == 3
    assert stats["dropped_null_rows"] == 2
    assert stats["null_count"] == 2
    assert stats["null_pct"] == pytest.approx(40.0)


def test_right_skew_detected():
    # A long right tail drags the mean above the median.
    values = [1, 1, 2, 2, 2, 3, 3, 4, 50, 60]
    stats = build_report_stats(daily_report(values), cfg())
    assert stats["mean"] > stats["median"]
    assert stats["skew_flag"] == "right"


def test_symmetric_data_has_no_skew_flag():
    stats = build_report_stats(daily_report([10, 20, 30, 40, 50]), cfg())
    assert stats["skew_flag"] is None


# ---------------------------------------------------------------------------
# The `sum` gate - the bug where means got added up
# ---------------------------------------------------------------------------

def test_sum_exposed_for_additive_aggregate():
    values = [10, 20, 30, 40]
    stats = build_report_stats(daily_report(values), cfg(y="order_id_count"))
    assert stats["sum_is_meaningful"] is True
    assert stats["sum"] == 100
    assert stats["headline_label"] == "Total Order Id"
    assert stats["headline_value"] == 100


def test_sum_suppressed_for_mean_aggregate():
    """Summing 90 daily averages produces a number with no meaning."""
    df = pd.DataFrame({
        "order_date": pd.date_range("2023-01-01", periods=4, freq="D"),
        "basket_size_mean": [10.0, 20.0, 30.0, 40.0],
    })
    stats = build_report_stats(df, cfg(y="basket_size_mean"))
    assert stats["sum_is_meaningful"] is False
    assert stats["sum"] is None
    assert stats["headline_label"] == "Average Basket Size (avg)"
    assert stats["headline_value"] == pytest.approx(25.0)


def test_sum_is_exact_not_derived_from_rounded_mean():
    # mean of these is 33.333...; total must still land on the true sum.
    values = [10, 30, 60]
    df = pd.DataFrame({
        "day": pd.date_range("2023-01-01", periods=3, freq="D"),
        "hits_sum": values,
    })
    stats = build_report_stats(df, cfg(x="day", y="hits_sum"))
    assert stats["sum"] == 100


# ---------------------------------------------------------------------------
# Column naming in prose - no raw snake_case leaking to users
# ---------------------------------------------------------------------------

def test_prose_uses_humanized_column_names():
    df = pd.DataFrame({
        "order_date": pd.date_range("2023-01-01", periods=6, freq="D"),
        "line_total_sum": [1, 2, 3, 4, 5, 6],
    })
    stats = build_report_stats(df, cfg(y="line_total_sum"))
    blob = " ".join(
        str(stats[k]) for k in ("top_insight_text", "anomaly_text", "recommendation_text")
    )
    assert "line_total_sum" not in blob
    assert "Line Total (total)" in blob or "Line Total" in blob


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

def test_clean_upward_trend_is_clear():
    stats = build_report_stats(daily_report(list(range(1, 21))), cfg())
    assert "trend" in stats["blocks"]
    assert stats["trend_direction"] == "up"
    assert stats["trend_r2"] == pytest.approx(1.0, abs=0.001)
    assert stats["trend_strength"] == "clear"
    assert stats["trend_slope"] == pytest.approx(1.0, abs=0.001)


def test_downward_trend_direction():
    stats = build_report_stats(daily_report(list(range(20, 0, -1))), cfg())
    assert stats["trend_direction"] == "down"
    assert stats["trend_slope"] < 0


def test_tiny_change_reports_flat_not_up():
    """Without a dead band, a sub-1% wobble was narrated as 'trended up'."""
    values = [100, 100, 100, 100, 100, 100, 100, 100.5]
    stats = build_report_stats(daily_report(values), cfg())
    assert abs(stats["trend_pct_change"]) < TREND_DEAD_BAND_PCT
    assert stats["trend_direction"] == "flat"
    assert "held steady" in stats["top_insight_text"]


def test_noisy_series_trend_is_qualified_as_weak():
    rng = np.random.default_rng(0)
    values = 100 + rng.normal(0, 25, 60)
    stats = build_report_stats(daily_report(values.tolist()), cfg())
    assert stats["trend_r2"] < 0.2
    assert stats["trend_strength"] == "weak"
    if stats["trend_direction"] != "flat":
        assert "noisy" in stats["top_insight_text"]


def test_flat_series_has_zero_slope():
    stats = build_report_stats(daily_report([50] * 10), cfg())
    assert stats["trend_slope"] == 0.0
    assert stats["trend_direction"] == "flat"


def test_longest_runs():
    values = [1, 2, 3, 4, 3, 2, 1, 0]  # 3 consecutive ups, then 4 consecutive downs
    stats = build_report_stats(daily_report(values), cfg())
    assert stats["longest_run_up"] == 3
    assert stats["longest_run_down"] == 4


def test_sparkline_is_capped_at_twelve_points():
    stats = build_report_stats(daily_report(list(range(90))), cfg())
    assert len(stats["sparkline"]) == 12
    assert stats["sparkline"][0] == 0
    assert stats["sparkline"][-1] == 89


def test_sparkline_passes_short_series_through():
    stats = build_report_stats(daily_report([1, 2, 3, 4, 5]), cfg())
    assert stats["sparkline"] == [1, 2, 3, 4, 5]


def test_no_trend_block_on_unordered_axis():
    df = category_report(["North", "South", "East", "West"], [10, 20, 30, 40])
    stats = build_report_stats(df, cfg(chart_type="bar", x="region", y="revenue_sum"))
    assert "trend" not in stats["blocks"]
    assert stats["axis_is_ordered"] is False


def test_trend_block_on_ordered_categorical_bins():
    bins = pd.Categorical(
        ["0-10", "10-20", "20-30", "30-40", "40-50"],
        categories=["0-10", "10-20", "20-30", "30-40", "40-50"],
        ordered=True,
    )
    df = pd.DataFrame({"age_band": bins, "customer_id_count": [5, 10, 18, 25, 33]})
    stats = build_report_stats(df, cfg(chart_type="bar", x="age_band", y="customer_id_count"))
    assert stats["axis_is_ordered"] is True
    assert "trend" in stats["blocks"]
    assert stats["trend_direction"] == "up"


# ---------------------------------------------------------------------------
# Period gaps
# ---------------------------------------------------------------------------

def test_missing_days_detected():
    dates = pd.date_range("2023-01-01", periods=20, freq="D").delete([5, 6, 11])
    df = pd.DataFrame({"order_date": dates, "order_id_count": range(len(dates))})
    stats = build_report_stats(df, cfg())
    gaps = stats["period_gaps"]
    assert gaps["expected_periods"] == 20
    assert gaps["actual_periods"] == 17
    assert gaps["missing_periods"] == 3
    assert "missing from the sequence" in stats["recommendation_text"]


def test_complete_sequence_has_no_gaps():
    stats = build_report_stats(daily_report(list(range(30))), cfg())
    assert stats["period_gaps"] is None


# ---------------------------------------------------------------------------
# Outliers - robust detection
# ---------------------------------------------------------------------------

def test_single_spike_is_flagged():
    values = [10] * 20 + [500]
    stats = build_report_stats(daily_report(values), cfg())
    labels = [a["label"] for a in stats["anomalies"]]
    assert len(stats["anomalies"]) == 1
    assert stats["anomalies"][0]["value"] == 500
    assert stats["anomalies"][0]["direction"] == "high"
    assert "21 Jan 2023" in labels


def test_masking_a_plain_zscore_would_miss_is_caught():
    """Two extreme points inflate the standard deviation enough that neither clears
    a z-score threshold - the exact failure mode the MAD rule replaces."""
    rng = np.random.default_rng(7)
    body = (100 + rng.normal(0, 5, 30)).tolist()
    values = body + [900.0] * 6
    s = pd.Series(values)

    classic_z = (s - s.mean()).abs() / s.std(ddof=0)
    assert classic_z.max() < 3.5, "the outliers should be masking each other"

    stats = build_report_stats(daily_report(values), cfg())
    assert {a["value"] for a in stats["anomalies"]} == {900.0}
    assert stats["anomaly_count"] == 6
    assert "MAD" in stats["anomaly_method"]


def test_clean_data_reports_no_outliers():
    stats = build_report_stats(daily_report(list(range(1, 41))), cfg())
    assert stats["anomalies"] == []
    assert "No outliers" in stats["anomaly_text"]


def test_anomalies_sorted_by_severity():
    values = [100.0] * 15 + [90.0] * 8 + [110.0] * 8 + [400.0, 900.0]
    stats = build_report_stats(daily_report(values), cfg())
    ranked = [a["value"] for a in stats["anomalies"]]
    assert ranked[:2] == [900.0, 400.0]
    deviations = [a["deviation"] for a in stats["anomalies"]]
    assert deviations == sorted(deviations, reverse=True)


def test_tukey_fallback_when_mad_collapses():
    """Over half the points share one value, so MAD is 0 and the modified z-score is
    undefined. The upper quartile still varies, so Tukey's fence has an answer."""
    values = [7.0] * 20 + [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0] + [999.0]
    s = pd.Series(values)
    assert (s - s.median()).abs().median() == 0, "MAD should collapse here"
    assert s.quantile(0.75) - s.quantile(0.25) > 0, "but the IQR should not"

    stats = build_report_stats(daily_report(values), cfg())
    assert stats["anomaly_method"] == "Tukey fence (1.5x IQR)"
    assert 999.0 in {a["value"] for a in stats["anomalies"]}


def test_constant_baseline_fallback_when_both_scales_collapse():
    """A flat line with one spike: MAD and IQR are both 0, so neither rule above can
    see the spike that any reader would call the outlier."""
    values = [10.0] * 20 + [500.0]
    s = pd.Series(values)
    assert (s - s.median()).abs().median() == 0
    assert s.quantile(0.75) - s.quantile(0.25) == 0

    stats = build_report_stats(daily_report(values), cfg())
    assert stats["anomaly_method"] == "departure from a constant baseline"
    assert stats["anomaly_count"] == 1
    assert stats["anomalies"][0]["value"] == 500.0


def test_bimodal_data_gets_no_constant_baseline_outliers():
    """Half at one value and half at another is not a baseline with departures from
    it - there is nothing here to call an outlier."""
    values = [10.0] * 20 + [90.0] * 18
    stats = build_report_stats(daily_report(values), cfg())
    assert stats["anomalies"] == []


def test_anomaly_list_is_capped_but_count_is_honest():
    rng = np.random.default_rng(3)
    body = (100 + rng.normal(0, 1, 200)).tolist()
    spikes = [900.0 + i for i in range(30)]
    stats = build_report_stats(daily_report(body + spikes), cfg())
    assert stats["anomaly_count"] >= 30
    assert len(stats["anomalies"]) == 20
    assert f"{stats['anomaly_count']} outliers" in stats["anomaly_text"]
    assert "more)" in stats["anomaly_text"]


def test_constant_series_has_no_outliers_and_no_crash():
    stats = build_report_stats(daily_report([42] * 15), cfg())
    assert stats["anomalies"] == []
    assert stats["anomaly_method"] is None


def test_too_few_points_skips_outlier_test():
    stats = build_report_stats(daily_report([1, 2, 3]), cfg())
    assert stats["anomalies"] == []
    assert "Too few data points" in stats["anomaly_text"]


def test_mad_threshold_boundary():
    """Verify the flag fires on the documented rule, computed against the series the
    detector actually sees (appending a point moves the median and the MAD)."""
    values = [100.0] * 20 + [90.0] * 10 + [110.0] * 10 + [160.0]
    s = pd.Series(values)
    median = s.median()
    mad = (s - median).abs().median()
    assert mad > 0

    expected = {
        v for v in values
        if abs(0.6745 * (v - median) / mad) > MAD_THRESHOLD
    }
    stats = build_report_stats(daily_report(values), cfg())
    assert {a["value"] for a in stats["anomalies"]} == expected
    assert stats["anomaly_method"] == "modified z-score (MAD)"


# ---------------------------------------------------------------------------
# Concentration (RANKING / COMPOSITION)
# ---------------------------------------------------------------------------

def test_concentration_shares():
    df = category_report(["A", "B", "C", "D"], [50, 30, 15, 5])
    stats = build_report_stats(
        df, cfg(chart_type="bar", x="region", y="revenue_sum"), pattern="RANKING"
    )
    assert "concentration" in stats["blocks"]
    assert stats["total"] == 100
    assert stats["top1_share"] == pytest.approx(50.0)
    assert stats["top3_share"] == pytest.approx(95.0)
    assert stats["top_labels"] == ["A", "B", "C"]
    assert stats["n_categories"] == 4


def test_long_tail_counted():
    values = [500] + [1] * 20  # 20 categories under 1% each
    labels = [f"cat{i}" for i in range(21)]
    df = category_report(labels, values)
    stats = build_report_stats(df, cfg(chart_type="bar", x="region", y="revenue_sum"))
    assert stats["tail_count"] == 20
    assert "under 1%" in stats["recommendation_text"]


def test_concentration_skipped_for_negative_values():
    """A 'share of total' across mixed signs isn't a share of anything."""
    df = category_report(["A", "B", "C"], [100, -40, 20])
    stats = build_report_stats(df, cfg(chart_type="bar", x="region", y="revenue_sum"))
    assert "concentration" not in stats["blocks"]


# ---------------------------------------------------------------------------
# Raw histogram (no y_axis)
# ---------------------------------------------------------------------------

def test_raw_histogram_uses_x_as_the_measure():
    df = pd.DataFrame({"basket_value": [5.0, 7.0, 9.0, 11.0, 13.0, 15.0]})
    stats = build_report_stats(df, cfg(chart_type="histogram", x="basket_value", y=None))
    assert stats["available"] is True
    assert stats["measure_column"] == "basket_value"
    assert stats["label_column"] is None
    assert stats["peak_label"] is None
    assert stats["mean"] == pytest.approx(10.0)


def test_raw_histogram_anomaly_labels_fall_back_to_row_numbers():
    df = pd.DataFrame({"amount": [1.0] * 20 + [900.0]})
    stats = build_report_stats(df, cfg(chart_type="histogram", x="amount", y=None))
    assert stats["anomalies"][0]["label"] == "row 21"


# ---------------------------------------------------------------------------
# Label formatting - the _fmt_label regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "granularity,expected",
    [
        ("yearly", "2023"),
        ("monthly", "Mar 2023"),
        ("daily", "15 Mar 2023"),
        ("weekly", "15 Mar 2023"),
    ],
)
def test_fmt_label_honours_granularity(granularity, expected):
    assert _fmt_label(pd.Timestamp("2023-03-15"), granularity) == expected


def test_fmt_label_midnight_is_not_treated_as_year_only():
    """datetime.time(0, 0) has been truthy since Python 3.5, so the old
    `if value.time()` check could never select the year-only branch."""
    assert _fmt_label(pd.Timestamp("2023-03-15")) == "15 Mar 2023"


def test_fmt_label_shows_clock_when_present():
    assert _fmt_label(pd.Timestamp("2023-03-15 14:30")) == "15 Mar 2023 14:30"


def test_fmt_label_strips_decimal_from_whole_floats():
    assert _fmt_label(2023.0) == "2023"


def test_granularity_flows_into_peak_label():
    dates = pd.to_datetime(["2021-01-01", "2022-01-01", "2023-01-01", "2024-01-01"])
    df = pd.DataFrame({"year": dates, "sales_sum": [10, 90, 20, 30]})
    stats = build_report_stats(
        df, cfg(x="year", y="sales_sum"), granularity="yearly"
    )
    assert stats["peak_label"] == "2022"


# ---------------------------------------------------------------------------
# Ordered-axis detection
# ---------------------------------------------------------------------------

def test_id_column_is_not_an_ordered_axis():
    """A unique 1..n integer column is an ID, not a position on a scale - treating
    it as ordered manufactures a trend out of row order."""
    ids = pd.Series(range(1, 21))
    assert _is_ordered_axis(ids) is False


def test_repeated_numeric_axis_is_ordered():
    years = pd.Series([2020, 2020, 2021, 2021, 2022, 2022])
    assert _is_ordered_axis(years) is True


def test_unordered_categorical_is_not_ordered():
    cat = pd.Series(pd.Categorical(["a", "b", "c"], ordered=False))
    assert _is_ordered_axis(cat) is False


def test_datetime_axis_is_ordered():
    assert _is_ordered_axis(pd.Series(pd.date_range("2023-01-01", periods=5))) is True


# ---------------------------------------------------------------------------
# Date-shaped strings - dates survive the pipeline unparsed
# ---------------------------------------------------------------------------

def string_date_report(values, start="2023-01-01"):
    """What a real report looks like: nothing upstream coerces dates, so the axis
    arrives as object-dtype strings in the source file's own format."""
    dates = pd.date_range(start, periods=len(values), freq="D")
    # Built by hand rather than with strftime("%-m/..."), which is a glibc extension
    # and raises on Windows.
    return pd.DataFrame({
        "order_date": [f"{d.month}/{d.day}/{d.year % 100:02d}" for d in dates],
        "order_id_count": values,
    })


def test_string_date_axis_is_ordered():
    labels = pd.Series(["1/1/23", "1/2/23", "1/10/23", "2/1/23"])
    assert _is_ordered_axis(labels) is True


def test_partially_parseable_axis_is_not_a_date_axis():
    labels = pd.Series(["1/1/23", "North", "1/3/23", "1/4/23"])
    assert _is_ordered_axis(labels) is False


def test_string_dates_get_the_trend_block_not_concentration():
    """A 90-day series arriving as strings was being described as a ranking of 90
    categories: 'the top 3 of 90 categories account for 4.3% of total'."""
    df = string_date_report(list(range(50, 140)))
    stats = build_report_stats(df, cfg(), pattern="TREND")

    assert stats["axis_is_ordered"] is True
    assert "trend" in stats["blocks"]
    assert "concentration" not in stats["blocks"]
    assert "categories" not in stats["top_insight_text"]


def test_string_dates_sort_chronologically_not_lexically():
    """Lexically, '1/10/23' sorts before '1/2/23'. A trend measured over that order
    is measuring the wrong sequence."""
    labels = ["1/1/23", "1/2/23", "1/3/23", "1/9/23", "1/10/23", "1/11/23",
              "1/20/23", "1/21/23"]
    rising = [10, 20, 30, 40, 50, 60, 70, 80]
    df = pd.DataFrame({"order_date": labels, "order_id_count": rising})
    stats = build_report_stats(df, cfg())

    assert stats["trend_direction"] == "up"
    assert stats["trend_r2"] == pytest.approx(1.0, abs=0.001)


def test_string_date_labels_render_unambiguously():
    """'2/1/23' means two different days depending on the reader."""
    df = pd.DataFrame({
        "order_date": ["1/31/23", "2/1/23", "2/2/23", "2/3/23"],
        "order_id_count": [10, 99, 12, 13],
    })
    stats = build_report_stats(df, cfg())
    assert stats["peak_label"] == "1 Feb 2023"


def test_string_date_gaps_detected():
    labels = ["1/1/23", "1/2/23", "1/3/23", "1/6/23", "1/7/23"]
    df = pd.DataFrame({"order_date": labels, "order_id_count": [1, 2, 3, 4, 5]})
    stats = build_report_stats(df, cfg())
    assert stats["period_gaps"]["missing_periods"] == 2


# ---------------------------------------------------------------------------
# Quality block
# ---------------------------------------------------------------------------

def test_schema_warning_and_llm_caveat_carried_through_separately():
    stats = build_report_stats(
        daily_report([1, 2, 3, 4, 5]),
        cfg(),
        llm_caveat="Some orders may be missing item info.",
        schema_warning="expected column 'total' not produced",
    )
    assert stats["llm_caveat"] == "Some orders may be missing item info."
    assert stats["schema_warning"] == "expected column 'total' not produced"
    # The measured statement and the model's guess must not be blended together.
    assert "Some orders may be missing" not in stats["quality_text"]
    assert "expected column" in stats["quality_text"]


def test_quality_block_present_even_when_stats_unavailable():
    stats = build_report_stats(pd.DataFrame(), cfg(), llm_caveat="caveat")
    assert stats["available"] is False
    assert stats["llm_caveat"] == "caveat"


def test_clean_data_reports_no_missing_values():
    stats = build_report_stats(daily_report([1, 2, 3, 4]), cfg())
    assert stats["null_count"] == 0
    assert "No missing values" in stats["quality_text"]


# ---------------------------------------------------------------------------
# Duplicate index safety
# ---------------------------------------------------------------------------

def test_duplicate_index_does_not_corrupt_labels():
    """`.loc[idx]` on a non-unique index returns a Series, which used to get
    stringified into the peak label."""
    df = daily_report([5, 40, 7, 9])
    df.index = [0, 0, 1, 1]
    stats = build_report_stats(df, cfg())
    assert stats["peak_value"] == 40
    assert stats["peak_label"] == "2 Jan 2023"
    assert "Series" not in stats["top_insight_text"]


# ---------------------------------------------------------------------------
# The screenshot's own report shape, end to end
# ---------------------------------------------------------------------------

def test_daily_order_volume_report():
    rng = np.random.default_rng(42)
    values = np.clip(rng.normal(135, 20, 90), 60, None).round()
    df = daily_report(values.tolist())
    stats = build_report_stats(
        df, cfg(), pattern="TREND", granularity="daily",
        llm_caveat="A small portion of order records are missing item information.",
    )

    assert stats["available"] is True
    assert stats["count"] == 90
    assert stats["blocks"] == ["trend", "outliers"]
    assert stats["headline_label"] == "Total Order Id"
    assert stats["headline_value"] == pytest.approx(values.sum())
    assert stats["peak_value"] == values.max()
    assert stats["trough_value"] == values.min()
    assert stats["period_gaps"] is None
    # The insight must be a finding, and the model's guess must stay attributed.
    assert "?" not in stats["top_insight_text"]
    assert stats["llm_caveat"].startswith("A small portion")
