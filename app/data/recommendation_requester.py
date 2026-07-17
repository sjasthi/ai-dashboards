import json
from typing import List
from .summary_builder import FileProfile


def _round_if_numeric(value, ndigits: int = 2):
    """Round a profile stat to fewer decimals to shave prompt tokens - min/max
    are stored as strings (dates and numbers alike), so only round the ones
    that actually parse as a number; leave dates/text untouched."""
    if value is None:
        return None
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return value


class RecommendationRequester:
    """Handles request for report recommendations from LLM."""

    # Report archetypes the LLM is allowed to choose from. Keeping this list
    # explicit (rather than letting the LLM invent chart ideas) is what makes
    # the prompt generalize across arbitrary spreadsheets: every dataset gets
    # evaluated against the same small set of patterns, and only the patterns
    # whose data requirements are actually met should be recommended.
    REPORT_PATTERNS = {
        "RANKING": "order entities by a measure, show top/bottom N. Requires an "
                   "identifier-like column (unique_values / row_count > 0.8) and a numeric measure.",
        "DISTRIBUTION": "histogram/bin a single numeric column. Requires a continuous "
                        "numeric column with low null_percent and a reasonable value range.",
        "COMPOSITION": "breakdown of a measure across a low-cardinality categorical column "
                       "(unique_values / row_count < 0.3), either existing or derived.",
        "TREND": "change of a measure over time. Requires a date/datetime/temporal column.",
        "COMPARISON": "relationship or correlation between two numeric measures.",
        "OUTLIER": "flag rows far from the norm on a numeric measure (e.g. via IQR or z-score)."
    }

    def build_request_prompt(self, file_profiles: List[FileProfile], relationships: List[dict] = None) -> str:
        """Build LLM prompt with explicit JSON schema instructions."""

        # Serialize summaries to JSON. Compact (no indent) rather than
        # pretty-printed - the LLM parses it identically either way, but
        # compact JSON is meaningfully fewer tokens (whitespace/newlines are
        # tokens too), which matters on smaller-TPM fallback models.
        summaries_json = json.dumps(
            [self._profile_to_dict(p) for p in file_profiles],
            separators=(",", ":"),
            default=str
        )

        patterns_block = "\n".join(
            f"- {name}: {desc}" for name, desc in self.REPORT_PATTERNS.items()
        )

        relationships = relationships or []
        if len(file_profiles) > 1:
            if relationships:
                relationships_json = json.dumps(relationships, separators=(",", ":"), default=str)
                relationships_block = f"""
DETECTED CROSS-FILE RELATIONSHIPS: these join keys were programmatically verified
(overlapping actual values between files, not just similar names), so you can trust
them without re-deriving them yourself. Each entry gives "column_a" (the column in
"file_a") and "column_b" (the column in "file_b") - these are frequently NAMED
DIFFERENTLY (e.g. a foreign key "theme_id" in one file referencing the primary key
"id" in another), so always use the exact column_a/column_b names given here, never
assume both files use the same column name:

{relationships_json}

You are analyzing {len(file_profiles)} files together, not {len(file_profiles)} separate
datasets. At least one of your recommendations MUST use a "join" operation across
related files (per the relationships above) to answer a question that no single file
could answer alone - e.g. combining a customer attribute with a transaction/behavior
measure, or a product attribute with sales performance. Only fall back to purely
single-file recommendations for files that have no detected relationship to anything else.
"""
            else:
                relationships_block = f"""
You were given {len(file_profiles)} files but no reliable shared key was detected between
them (no matching column names with overlapping values). Do not invent a join - treat
these as independent datasets unless you can point to a real shared column in the
profiles above.
"""
        else:
            relationships_block = ""

        prompt = f"""You are a data analyst expert. I have the following CSV/Excel files and their profiles:

{summaries_json}
{relationships_block}
Your task: recommend meaningful, EXECUTABLE reports built specifically from the data above.

STEP 1 - Each column's "role" field above was already computed deterministically from
the actual data (not guessed from its name) - treat it as ground truth and do not
re-derive or second-guess it. Apply these role-based rules directly:
- role == "primary_key" -> identifier-like; never use as a groupby column
- role == "categorical" -> good for a groupby/composition axis
- role == "measure" -> numeric measure; good for distribution/comparison, generally not a groupby column
- role == "temporal" -> good for a trend x-axis
Independently of role, still watch for two things the role field doesn't capture:
- null_percent > 40% -> low-quality column; avoid as a primary axis unless you filter it first
- non-numeric placeholder tokens (e.g. "-", "N/A", "?") in an otherwise numeric/measure column also count as missing data - add a filter/clean operation before aggregating that column

STEP 2 - Only recommend a report pattern whose data requirements are actually met by
this file's profile. Choose from this fixed set of patterns (do not invent others):
{patterns_block}

STEP 3 - If a categorical breakdown would be useful but no low-cardinality column
exists, define a "derive" operation to create one (e.g. regex_extract a family/prefix
out of a high-cardinality text column, or bin a numeric column into ranges) instead of
grouping directly on a near-unique column.

STEP 4 - Express each recommendation as an ordered operation pipeline
(filter -> derive -> groupby -> sort_limit -> join, using only the steps that apply),
and declare the expected_output_schema that pipeline produces, so the columns you
reference in plotly_config actually exist in the result. A DISTRIBUTION report with
chart_type "histogram" plots ONE continuous column's raw values directly (the
histogram bins them itself) - it needs only a "filter" step (e.g. not_null on that
column), never a "groupby" or "sort_limit" step, and its plotly_config must OMIT
"y_axis" entirely (do not set it to null or any column name) since Plotly computes
the y values itself. Only include an operation step at
all if it does real work; never include a "groupby" (or any other step) with empty/
placeholder fields like "groupby_columns": [] just to have every step type present.

STEP 5 - If the data is too sparse, too dirty, or too uniform to support a good report,
it is fine to return fewer than 3 recommendations, and to add a data_quality_warning
explaining the caveat, rather than forcing a low-quality suggestion.

IMPORTANT: You MUST return a valid JSON object (not markdown, not prose) with this exact structure:

{{
  "recommendations": [
    {{
      "rank": 1,
      "report_name": "Report Name Here",
      "question_answered": "What business question does this answer?",
      "pattern_used": "COMPOSITION",
      "justification": {{
        "column": "column_name",
        "profile_evidence": "cite the actual unique_values/null_percent/dtype from the profile above that justify this pattern"
      }},
      "required_operations": [
        {{
          "operation_type": "filter",
          "files_involved": ["file1.csv"],
          "filter_conditions": [{{"column": "some_column", "condition": "not_null"}}]
        }},
        {{
          "operation_type": "join",
          "files_involved": ["file1.csv", "file2.csv"],
          "join_keys": ["shared_id_column"],
          "join_type": "inner"
        }},
        {{
          "operation_type": "join",
          "files_involved": ["file2.csv", "file3.csv"],
          "join_keys": [{{"left": "foreign_key_in_file2", "right": "primary_key_in_file3"}}],
          "join_type": "inner"
        }},
        {{
          "operation_type": "derive",
          "files_involved": ["file1.csv"],
          "derive_column": {{
            "new_name": "derived_category",
            "source_column": "high_cardinality_column",
            "method": "regex_extract",
            "pattern": "^(\\\\w+)"
          }}
        }},
        {{
          "operation_type": "derive",
          "files_involved": ["file1.csv"],
          "derive_column": {{
            "new_name": "age_group",
            "source_column": "age",
            "method": "bin",
            "bins": [18, 30, 40, 50, 60, 100],
            "bin_labels": ["18-30", "30-40", "40-50", "50-60", "60+"]
          }}
        }},
        {{
          "operation_type": "derive",
          "files_involved": ["file1.csv"],
          "derive_column": {{
            "new_name": "loyalty_segment",
            "source_column": "loyalty_score",
            "method": "quantile",
            "quantiles": [0.25, 0.5, 0.75],
            "bin_labels": ["Low", "Medium", "High", "Top"]
          }}
        }},
        {{
          "operation_type": "groupby",
          "files_involved": ["file1.csv"],
          "groupby_columns": ["derived_category"],
          "aggregations": {{"measure_column": "mean"}}
        }},
        {{
          "operation_type": "sort_limit",
          "files_involved": ["file1.csv"],
          "sort_by": "measure_column_mean",
          "ascending": false,
          "limit": 15
        }}
      ],
      "expected_output_schema": [
        {{"name": "derived_category", "type": "string"}},
        {{"name": "measure_column_mean", "type": "float"}}
      ],
      "plotly_config": {{
        "chart_type": "bar",
        "x_axis": "derived_category",
        "y_axis": "measure_column_mean",
        "title": "Chart Title",
        "x_axis_label": "Derived Category",
        "y_axis_label": "Average Measure Column"
      }},
      "rationale_bullets": [
        "First short bullet explaining what this report shows",
        "Second bullet highlighting a key insight or use case",
        "Third bullet describing the ideal audience or scenario"
      ],
      "data_quality_warning": "optional - omit this key entirely if there is no caveat"
    }}
  ]
}}

Rules:
1. Only include operation_type values that apply - filter/derive/groupby/sort_limit/join are ALL optional per recommendation; omit any step (including groupby and sort_limit) entirely when it doesn't apply, rather than including it with empty/placeholder fields. When you DO include a groupby step, it must have real groupby_columns and aggregations (never skip that validity check).
1b. A "derive" step's "method" is one of "regex_extract", "bin", or "quantile" - never invent another method name. "bin"'s "bins" field must be NUMBERS only (the numeric boundary edges, e.g. [18, 30, 40, 100]) - never range strings like "18-30" and never date strings; put display strings in "bin_labels" instead (one fewer label than bins). Only bin a numeric (role == "measure") column this way - never bin a role == "temporal" column directly, since its bin edges would have to be date strings. To bucket a date/temporal column into ranges (e.g. "signups by year"), derive a period label first (e.g. regex_extract the year out of a formatted date string) and group by that instead of using "bin" on the raw date. "quantile" splits by cut POINTS between 0 and 1 in a "quantiles" field (e.g. [0.25, 0.5, 0.75] for quartiles), also paired with "bin_labels" (one more label than quantiles). In any LATER step, reference the derived column by its own "new_name" value (e.g. "age_group") - never by the literal field name "derive_column".
2. Never group by a column classified as identifier/primary-key-like in Step 1.
2b. A groupby's aggregated output columns are always renamed to "{{column}}_{{function}}" (e.g. aggregations {{"Calories": "mean"}} produces a column literally named "Calories_mean"). Always use that exact renamed form - never the bare column name - in sort_by, plotly_config axes, and expected_output_schema.
2e. "x_axis_label"/"y_axis_label" in plotly_config are OPTIONAL plain-English overrides of the axis title shown to end users (the raw "x_axis"/"y_axis" column name is still used to pull the data - never change those). Omit either key entirely when the raw column name is already clear. When you do include one, it must describe the column as actually computed - never imply an aggregation (e.g. "rate", "average", "%", "total") for a column that is NOT the output of a groupby aggregation (i.e. does not carry an "_mean"/"_sum"/"_count"/etc. suffix per rule 2b). E.g. a raw pass-through column like "churn_signal" must not be labeled "Churn Rate" - only a column like "churn_signal_mean" may be.
2c. A "groupby" operation's "groupby_columns" must NEVER be empty. If you want a count of records per value of some column (e.g. "how many sets per year"), that column goes in BOTH "groupby_columns" AND "aggregations" (e.g. "groupby_columns": ["year"], "aggregations": {{"year": "count"}}) - never leave "groupby_columns" empty and only reference the column in "aggregations".
2d. If two joined files both have a column with the same name (e.g. both have "name"), any later step (groupby/filter/derive/sort_limit) that uses that column MUST list the SPECIFIC file it means in that step's own "files_involved" - not just repeat whichever file list the join used. E.g. if you joined sets.csv with themes.csv and want to group by the theme's "name" (not the set's "name"), that groupby step's "files_involved" must be ["themes.csv"], even though the join step's "files_involved" was ["sets.csv", "themes.csv"].
3. Bar/pie charts must resolve to at most ~15-20 categories after sort_limit; use limit to enforce this.
4. Cite real values from the profile (unique_values, null_percent, dtype) in "justification" - do not fabricate stats.
5. Rank recommendations by relevance/insight value.
6. Write exactly 3 rationale_bullets per recommendation - short, plain-English, no jargon.
7. Return between 1 and 4 recommendations; only include as many as the data genuinely supports.
8. If DETECTED CROSS-FILE RELATIONSHIPS were provided above, at least one recommendation must use them via a "join" operation - do not restrict every recommendation to a single file when the data supports connecting them.
9. A "join" operation's shared column(s) MUST go in a "join_keys" array field - never "join_column" or any other field name. "join_type" is optional and defaults to "inner". Each entry in "join_keys" is either:
   - a plain string when both files use the exact same column name, e.g. "join_keys": ["customer_id"]
   - a {{"left": ..., "right": ...}} object when the column names differ between the two files (per column_a/column_b in DETECTED CROSS-FILE RELATIONSHIPS above), e.g. "join_keys": [{{"left": "theme_id", "right": "id"}}] where "left" is the column in the first file listed in "files_involved" and "right" is the column in the second.

CRITICAL: Return ONLY the raw JSON object. Do NOT wrap in markdown code fences. Do NOT include ```json or ```. Start your response with {{ and end with }}.
"""
        return prompt

    def _profile_to_dict(self, profile: FileProfile) -> dict:
        """Convert FileProfile dataclass to dict for JSON serialization."""
        return {
            "filename": profile.filename,
            "row_count": profile.row_count,
            "columns": [
                {
                    "name": col.name,
                    "dtype": col.dtype,
                    "unique_values": col.unique_values,
                    "null_percent": col.null_percent,
                    "role": col.role,
                    "min": _round_if_numeric(col.min_value),
                    "max": _round_if_numeric(col.max_value),
                    "mean": _round_if_numeric(col.mean_value)
                }
                for col in profile.columns
            ],
            "quality_flags": profile.quality_flags
        }
