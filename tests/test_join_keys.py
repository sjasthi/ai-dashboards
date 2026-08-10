"""
Tests for join keys that mean the same thing but are spelled differently.

A workbook in the wild spelled a product "GTXPro" in its sales sheet and "GTX Pro"
in its product list. The inner join between them dropped every affected row - 729 of
4,238 won deals, 35% of the revenue - and the report's headline came out at 6.5M
instead of 10.0M with nothing on screen saying so.

These tests cover all three halves of the fix: report_builder re-matching the rows it
missed, response_validator no longer rejecting a join whose values correspond but are
spelled differently, and summary_builder still detecting the relationship so the model
is told the files relate at all.

The safety rule is what most of this file is about. Re-matching only happens when a
normalized key stands for exactly one distinct raw value on each side; merging two
categories that are genuinely different would be a worse bug than the dropped rows.
"""

import numpy as np
import pandas as pd
import pytest

from app.data.report_builder import (
    _execute_pipeline,
    _inner_join_counting_misses,
    _rescue_by_spelling,
)
from app.data.report_stats import _join_rescue_sentence, _quality_block
from app.data.response_validator import parse_and_validate
from app.data.summary_builder import SummaryGenerator, _normalize_key_value


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def pipeline_frame(products=None):
    """The many side: one row per deal, product spelled without the space."""
    products = ["GTXPro", "Widget", "GTXPro"] if products is None else products
    return pd.DataFrame({
        "product": products,
        "close_value": [100.0 * (i + 1) for i in range(len(products))],
    })


def products_frame(products=None, series=None):
    """The one side: a lookup keyed on product, spelled with the space."""
    products = ["GTX Pro", "Widget"] if products is None else products
    series = ["GTX", "WID"] if series is None else series
    return pd.DataFrame({"product": products, "series": series})


def join(left, right, left_on=None, right_on=None, rescues=None, losses=None):
    """_inner_join_counting_misses with the plumbing spelled out once."""
    left_on = ["product"] if left_on is None else left_on
    right_on = left_on if right_on is None else right_on
    return _inner_join_counting_misses(
        left, right, left_on, right_on, "_products", "products.csv",
        [] if losses is None else losses,
        rescues,
    )


def join_op(left="product", right="product"):
    return {
        "operation_type": "join",
        "files_involved": ["pipeline.csv", "products.csv"],
        "join_keys": [{"left": left, "right": right}],
    }


def groupby_op(files=("products.csv",)):
    return {
        "operation_type": "groupby",
        "files_involved": list(files),
        "groupby_columns": ["series"],
        "aggregations": [{"column": "close_value", "func": "sum"}],
    }


def recommendation(operations=None, **overrides):
    rec = {
        "rank": 1,
        "report_name": "Revenue by Product Series",
        "question_answered": "Which product series generates the most revenue?",
        "pattern_used": "COMPOSITION",
        "justification": {"column": "series", "profile_evidence": "3 unique series"},
        "required_operations": [join_op(), groupby_op()] if operations is None else operations,
        "expected_output_schema": [
            {"name": "series", "type": "string"},
            {"name": "close_value_sum", "type": "float"},
        ],
        "plotly_config": {
            "chart_type": "bar",
            "x_axis": "series",
            "y_axis": "close_value_sum",
            "title": "Revenue by Series",
        },
        "rationale_bullets": ["a", "b", "c"],
    }
    rec.update(overrides)
    return rec


def response(*recs):
    recs = list(recs)
    while len(recs) < 3:
        recs.append(recommendation(rank=len(recs) + 1))
    return {"dataset_overview": "sales", "recommendations": recs}


# ---------------------------------------------------------------------------
# The normalizer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["GTX Pro", "GTXPro", "gtx pro", "GTX-Pro",
                                   "  GTX   Pro  ", "GTX_Pro", "gtx.pro"])
def test_spelling_variants_share_one_normalized_key(value):
    assert _normalize_key_value(value) == "gtxpro"


def test_a_different_product_is_not_collapsed():
    """The near miss that makes edit distance unusable here."""
    assert _normalize_key_value("GTX Plus Pro") != _normalize_key_value("GTX Pro")


def test_accents_fold_but_other_scripts_survive():
    assert _normalize_key_value("Café") == _normalize_key_value("cafe")
    # export_builder._slugify would turn both of these into "" and collide them.
    assert _normalize_key_value("東京") is not None
    assert _normalize_key_value("東京") != _normalize_key_value("大阪")


def test_long_values_are_not_truncated():
    """The other _slugify trap: two long names sharing a prefix must stay distinct."""
    a = _normalize_key_value("x" * 200 + "a")
    b = _normalize_key_value("x" * 200 + "b")
    assert len(a) == 201 and a != b


@pytest.mark.parametrize("value", [None, float("nan"), "", "   ", "-", "???", "!!"])
def test_values_with_nothing_to_match_on_normalize_to_none(value):
    assert _normalize_key_value(value) is None


# ---------------------------------------------------------------------------
# Re-matching
# ---------------------------------------------------------------------------

def test_misspelled_rows_are_recovered():
    rescues, losses = [], []
    result = join(pipeline_frame(), products_frame(), rescues=rescues, losses=losses)

    assert len(result) == 3           # nothing dropped
    assert losses == []
    assert rescues == [{
        "file": "products.csv",
        "rows": 2,
        "key": "product",
        "pairs": [{"left": "GTXPro", "right": "GTX Pro"}],
    }]


def test_recovered_rows_take_the_matched_spelling():
    """So a later group-by on the key produces one bar, not two."""
    result = join(pipeline_frame(), products_frame(), rescues=[])
    assert set(result["product"]) == {"GTX Pro", "Widget"}


def test_rows_that_matched_exactly_are_untouched():
    without = join(pipeline_frame(), products_frame(), rescues=None)
    with_rescue = join(pipeline_frame(), products_frame(), rescues=[])

    widget_before = without[without["product"] == "Widget"].reset_index(drop=True)
    widget_after = with_rescue[with_rescue["product"] == "Widget"].reset_index(drop=True)
    pd.testing.assert_frame_equal(widget_before, widget_after)


def test_one_left_row_fans_out_over_several_matches():
    """The safety rule counts distinct values, not rows.

    A lookup holding the same key twice is still one spelling, so the row must be
    recovered - and it fans out exactly as an exact inner join would.
    """
    right = products_frame(products=["GTX Pro", "GTX Pro"], series=["GTX", "GTX-ALT"])
    rescues = []
    result = join(pipeline_frame(products=["GTXPro"]), right, rescues=rescues)

    assert len(result) == 2          # fanned out
    assert rescues[0]["rows"] == 1   # but one left row was recovered


def test_no_rows_recovered_leaves_the_result_alone():
    rescues, losses = [], []
    result = join(pipeline_frame(products=["Widget"]), products_frame(),
                  rescues=rescues, losses=losses)
    assert len(result) == 1
    assert rescues == [] and losses == []


def test_result_matches_a_plain_inner_join_when_nothing_needs_recovering():
    left = pipeline_frame(products=["Widget", "GTX Pro"])
    right = products_frame()

    expected = left.merge(right, left_on="product", right_on="product",
                          how="inner", suffixes=("", "_products")).reset_index(drop=True)
    pd.testing.assert_frame_equal(join(left, right, rescues=[]), expected)


# ---------------------------------------------------------------------------
# The safety rule
# ---------------------------------------------------------------------------

def test_two_spellings_on_the_lookup_side_block_the_match():
    """"GTX Pro" and "GTX-PRO" as separate products: which one did they mean?"""
    right = products_frame(products=["GTX Pro", "GTX-PRO"], series=["GTX", "OTHER"])
    rescues, losses = [], []
    result = join(pipeline_frame(products=["GTXPro"]), right,
                  rescues=rescues, losses=losses)

    assert len(result) == 0
    assert rescues == []
    assert losses[0]["rows"] == 1
    assert losses[0]["examples"] == ["GTXPro"]


def test_two_unmatched_spellings_on_the_left_block_the_match():
    left = pipeline_frame(products=["GTXPro", "GTX.Pro"])
    rescues, losses = [], []
    result = join(left, products_frame(products=["GTX Pro"], series=["GTX"]),
                  rescues=rescues, losses=losses)

    assert len(result) == 0
    assert rescues == []
    assert losses[0]["rows"] == 2


def test_a_left_value_that_already_matches_exactly_does_not_block_its_own_variant():
    """The relaxation: a file spelling it both ways is the clearest evidence, not
    the murkiest, so the exactly-matching spelling is exempt from the census."""
    left = pipeline_frame(products=["GTX Pro", "GTXPro"])
    rescues = []
    result = join(left, products_frame(products=["GTX Pro"], series=["GTX"]),
                  rescues=rescues)

    assert len(result) == 2
    assert rescues[0]["rows"] == 1


def test_an_unmatched_null_key_is_never_re_matched():
    """A row with no product must not be handed an arbitrary one.

    Note this is the rescue's guarantee, not the join's: pandas' own merge treats
    null as a matchable value, so two null keys already pair up in the exact join
    and never reach the code under test. What is pinned here is that the rescue
    does not *introduce* a match a null key would otherwise not have had.
    """
    left = pd.DataFrame({"product": ["GTXPro", None], "close_value": [1.0, 2.0]})
    right = pd.DataFrame({"product": ["GTX Pro"], "series": ["GTX"]})
    rescues, losses = [], []
    result = join(left, right, rescues=rescues, losses=losses)

    assert len(result) == 1                       # only the spelling variant
    assert list(result["series"]) == ["GTX"]
    assert rescues[0]["rows"] == 1
    assert losses[0]["rows"] == 1                 # the null row stays unmatched


def test_values_that_normalize_to_nothing_never_match_each_other():
    left = pd.DataFrame({"product": ["-"], "close_value": [1.0]})
    right = pd.DataFrame({"product": ["???"], "series": ["X"]})
    rescues = []
    assert len(join(left, right, rescues=rescues)) == 0
    assert rescues == []


# ---------------------------------------------------------------------------
# Key shapes normalization must not touch
# ---------------------------------------------------------------------------

def test_numeric_keys_are_left_alone():
    """101.0 stringifies to "1010", which would invent matches. Skip, don't guess."""
    left = pd.DataFrame({"pid": [1, 10], "close_value": [1.0, 2.0]})
    right = pd.DataFrame({"pid": [1, 100], "series": ["A", "B"]})
    rescues, losses = [], []
    result = join(left, right, left_on=["pid"], rescues=rescues, losses=losses)

    assert len(result) == 1          # only the exact match on 1
    assert rescues == []
    assert losses[0]["rows"] == 1


def test_a_text_key_against_a_numeric_key_still_refuses_to_merge():
    """pandas rejects this outright, before any re-matching gets a look in.

    Pinned so the repair is never mistaken for a licence to bridge dtypes: "1-0"
    normalizing to "10" must not become a way to join text to numbers.
    """
    left = pd.DataFrame({"pid": ["1-0"], "close_value": [1.0]})
    right = pd.DataFrame({"pid": [10], "series": ["A"]})
    with pytest.raises(ValueError, match="object and int64"):
        join(left, right, left_on=["pid"], rescues=[])


def test_multi_column_keys_apply_the_rule_per_tuple():
    left = pd.DataFrame({
        "region": ["EU", "US"],
        "product": ["GTXPro", "GTXPro"],
        "close_value": [1.0, 2.0],
    })
    # Only EU is stocked, and the US spelling variant lives under a different
    # region - so it must not make the EU bucket ambiguous.
    right = pd.DataFrame({
        "region": ["EU", "US"],
        "product": ["GTX Pro", "GTX-Pro"],
        "series": ["GTX", "GTX"],
    })
    rescues = []
    result = join(left, right, left_on=["region", "product"], rescues=rescues)

    assert len(result) == 2
    assert rescues[0]["rows"] == 2


# ---------------------------------------------------------------------------
# Hygiene: nothing the join uses internally may escape
# ---------------------------------------------------------------------------

def test_no_working_columns_survive():
    result = join(pipeline_frame(), products_frame(), rescues=[])
    assert not [c for c in result.columns if c.startswith("_join")]


def test_columns_match_a_plain_inner_join():
    left, right = pipeline_frame(), products_frame()
    expected = left.merge(right, left_on="product", right_on="product",
                          how="inner", suffixes=("", "_products"))
    assert list(join(left, right, rescues=[]).columns) == list(expected.columns)


def test_a_file_with_its_own_join_match_column_still_works():
    """The working names used to be literals, so this used to break the merge."""
    left = pipeline_frame().assign(_join_match=["a", "b", "c"],
                                   _join_left_row=[1, 2, 3])
    result = join(left, products_frame(), rescues=[])

    assert len(result) == 3
    assert list(result["_join_match"]) == ["a", "b", "c"]
    assert list(result["_join_left_row"]) == [1, 2, 3]


def test_recovered_rows_keep_left_order_and_a_clean_index():
    left = pipeline_frame(products=["Widget", "GTXPro", "Widget"])
    result = join(left, products_frame(), rescues=[])

    assert list(result["close_value"]) == [100.0, 200.0, 300.0]
    assert result.index.equals(pd.RangeIndex(len(result)))


def test_a_duplicated_index_on_the_left_does_not_confuse_the_match():
    left = pipeline_frame().set_axis([0, 0, 0], axis=0)
    rescues = []
    result = join(left, products_frame(), rescues=rescues)

    assert len(result) == 3
    assert rescues[0]["rows"] == 2


def test_rescue_helper_skips_rather_than_raises_on_a_column_mismatch():
    """A bug in the repair must never cost the user a report that would build."""
    left = pipeline_frame().assign(_join_left_row=np.arange(3))
    rescued, pairs = _rescue_by_spelling(
        left, np.array([0, 2]), products_frame(), ["product"], ["product"],
        "_products", ["product", "close_value", "nonexistent"],
    )
    assert rescued is None and pairs == []


# ---------------------------------------------------------------------------
# End to end through the pipeline
# ---------------------------------------------------------------------------

def test_pipeline_recovers_the_revenue_and_records_it():
    tables = {"pipeline.csv": pipeline_frame(), "products.csv": products_frame()}
    df = _execute_pipeline([join_op(), groupby_op()], None, tables)

    # 100 + 200 + 300, i.e. every row, not just the one that matched exactly.
    assert df["close_value_sum"].sum() == pytest.approx(600.0)
    ledger = df.attrs["row_ledger"]
    assert ledger["join_losses"] == []
    assert ledger["join_rescues"][0]["rows"] == 2


def test_working_columns_do_not_reach_the_finished_report():
    tables = {"pipeline.csv": pipeline_frame(), "products.csv": products_frame()}
    df = _execute_pipeline([join_op()], None, tables)
    assert not [c for c in df.columns if c.startswith("_join")]


# ---------------------------------------------------------------------------
# What the card says
# ---------------------------------------------------------------------------

def rescue(rows=729, pairs=None):
    return {
        "file": "products (big workbook).xlsx",
        "rows": rows,
        "key": "product",
        "pairs": [{"left": "GTXPro", "right": "GTX Pro"}] if pairs is None else pairs,
    }


def test_rescue_sentence_names_both_spellings():
    text = _join_rescue_sentence(rescue())
    assert "729 rows matched on product in products" in text
    assert "ignoring case, spacing and punctuation" in text
    assert "“GTXPro” matched “GTX Pro”" in text


def test_rescue_sentence_counts_one_row_in_the_singular():
    assert _join_rescue_sentence(rescue(rows=1)).startswith("1 row matched")


def test_a_recovered_report_still_reports_its_completeness():
    """The reassurance is about nulls, and a rescue must not stand in for it."""
    df = pd.DataFrame({"series": ["GTX"], "close_value_sum": [600.0]})
    cfg = {"x_axis": "series", "y_axis": "close_value_sum"}
    stats = _quality_block(df, cfg, None, None,
                           {"join_losses": [], "join_rescues": [rescue()]})

    assert stats["join_rescue_rows"] == 729
    assert stats["join_loss_rows"] == 0
    assert "No missing data in this chart." in stats["quality_text"]
    assert "ignoring case" in stats["quality_text"]


def test_losses_are_stated_before_recoveries():
    df = pd.DataFrame({"series": ["GTX"], "close_value_sum": [600.0]})
    cfg = {"x_axis": "series", "y_axis": "close_value_sum"}
    loss = {"file": "products.xlsx", "rows": 5, "key": "product", "examples": ["ZZZ"]}
    text = _quality_block(df, cfg, None, None,
                          {"join_losses": [loss], "join_rescues": [rescue()]})["quality_text"]

    assert text.index("had no matching entry") < text.index("ignoring case")
    # The all-clear has no place next to a real loss.
    assert "No missing data" not in text


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------

def validate(left, right):
    return parse_and_validate(
        response(recommendation()),
        {"pipeline.csv", "products.csv"},
        tables={"pipeline.csv": left, "products.csv": right},
    )


def test_a_wholly_misspelled_join_is_no_longer_rejected():
    """0% of values shared, but every one of them corresponds."""
    left = pipeline_frame(products=["GTXPro", "MGSpecial", "GTXBasic"])
    right = products_frame(products=["GTX Pro", "MG Special", "GTX Basic"],
                           series=["GTX", "MG", "GTX"])
    validate(left, right)   # must not raise


def test_genuinely_unrelated_columns_are_still_rejected():
    left = pipeline_frame(products=["Acme", "Betatech"])
    right = products_frame(products=["technology", "medical"], series=["T", "M"])
    with pytest.raises(ValueError, match="share only"):
        validate(left, right)


def test_a_column_with_nothing_to_normalize_is_still_rejected():
    """All-punctuation values must not collapse to one key and score a perfect match."""
    left = pipeline_frame(products=["-", "--", "?"])
    right = products_frame(products=["!!", "!"], series=["A", "B"])
    with pytest.raises(ValueError, match="share only"):
        validate(left, right)


def test_non_overlapping_numeric_ids_are_still_rejected():
    left = pd.DataFrame({"product": [1, 2, 3], "close_value": [1.0, 2.0, 3.0]})
    right = pd.DataFrame({"product": [900, 901], "series": ["A", "B"]})
    with pytest.raises(ValueError, match="share only"):
        validate(left, right)


# ---------------------------------------------------------------------------
# Relationship detection
# ---------------------------------------------------------------------------

class _Loader:
    """detect_relationships reads (path, df) pairs off a loader."""
    def __init__(self, frames):
        self.files = list(frames.items())


def detect(left, right):
    gen = SummaryGenerator()
    profiles = [gen.profile_df_with_ydata("pipeline.csv", left),
                gen.profile_df_with_ydata("products.csv", right)]
    return gen.detect_relationships(profiles, _Loader({"pipeline.csv": left,
                                                       "products.csv": right}))


def key_frame(values, name="product_id"):
    return pd.DataFrame({name: values, "close_value": [1.0] * len(values)})


def test_a_misspelled_relationship_is_reported_rather_than_discarded():
    """Otherwise the model is never told the files relate, and the join is never
    proposed - losing the whole cross-file report, not just degrading it."""
    left = key_frame(["A-1", "B-2", "C-3"])
    right = key_frame(["A1", "B2", "C3"])
    found = detect(left, right)

    assert len(found) == 1
    assert found[0]["spelling_variants"] is True
    assert found[0]["normalized_overlap_ratio"] == 1.0
    assert found[0]["confidence"] == "medium"


def test_an_unrelated_key_pair_is_still_discarded():
    assert detect(key_frame(["A1", "B2"]), key_frame(["ZZ9", "YY8"])) == []


def test_a_cleanly_matching_relationship_is_unchanged():
    left = key_frame(["A1", "B2", "C3"])
    found = detect(left, key_frame(["A1", "B2", "C3"]))

    assert len(found) == 1
    assert found[0]["confidence"] == "high"
    assert "spelling_variants" not in found[0]
