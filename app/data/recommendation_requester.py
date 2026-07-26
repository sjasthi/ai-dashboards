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
        """Build the full LLM prompt: data + analysis guidance + output contract."""
        return (
            f"{self._data_section(file_profiles, relationships)}\n"
            f"{self._guidance_section()}\n"
            f"{self._output_contract()}"
        )

    def build_correction_prompt(self, file_profiles: List[FileProfile], relationships: List[dict] = None) -> str:
        """Prompt for a validation retry: the data and the output contract, WITHOUT the
        analysis guidance.

        By the time a response fails validation the model has already chosen its reports;
        what it needs to fix a schema/column/join error is the data and the output rules,
        not the role-and-pattern guidance that shaped the original choice. Retries
        otherwise resend the entire prompt, so a single failure nearly doubles the tokens
        spent on an analysis.
        """
        return (
            f"{self._data_section(file_profiles, relationships)}\n"
            f"{self._output_contract()}"
        )

    def _data_section(self, file_profiles: List[FileProfile], relationships: List[dict] = None) -> str:
        """The per-dataset half of the prompt: profiles, detected relationships, and how
        to read them."""
        # Serialize summaries to JSON. Compact (no indent) rather than
        # pretty-printed - the LLM parses it identically either way, but
        # compact JSON is meaningfully fewer tokens (whitespace/newlines are
        # tokens too), which matters on smaller-TPM fallback models.
        summaries_json = json.dumps(
            [self._profile_to_dict(p) for p in file_profiles],
            separators=(",", ":"),
            default=str
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
There is NO limit on how many "join" steps a single recommendation's required_operations
may contain - chain as many as the detected relationships actually support (e.g. file1
into file2 into file3 into file4). The two-join example in the JSON structure below is
only illustrating the join syntax, not capping how many joins you may use.

This list is a floor, not a ceiling. Detection only recognizes columns NAMED like keys
("id", or ending in "_id"/"_key"/"_num"), so it misses natural keys - e.g. a "product"
column joining to a product list, or an "account" column joining to an account list. If
you can see such a pair in the profiles/sample_rows above, use it as a join too. Every
join you propose is checked against the actual column values before your answer is
accepted, so propose only pairs whose values genuinely line up.
"""
            else:
                relationships_block = f"""
You were given {len(file_profiles)} files but the automatic key detection found no
relationship between them. That detection only recognizes columns NAMED like keys
("id", or ending in "_id"/"_key"/"_num"), so it misses natural keys - a "product"
column joining to a product list, an "account" column joining to an account list, an
"email" or "sku" shared across files. Those are real joins and you should use them.

Read the profiles, sample_rows and top_values above and decide for yourself whether
any two files can be joined. If you find a pair of columns that plainly refer to the
same real-world entity AND whose sample values visibly overlap, treat it as a join and
use it - you are analyzing {len(file_profiles)} files together, not {len(file_profiles)}
separate datasets. State the evidence in that recommendation's justification.

Be honest rather than eager: propose a join only where the values really do line up.
Every join you propose is checked against the actual column values before your answer
is accepted, and a pair that does not genuinely share values will be rejected and sent
back to you. If two files truly share nothing, say so and recommend single-file reports.
"""
        else:
            relationships_block = ""

        return f"""You are a data analyst expert. I have the following CSV/Excel files and their profiles:

{summaries_json}
{relationships_block}
READING THE PROFILES: both sample fields are positional, to save space.
- "sample_rows": each entry is one real row, given as an array of values in the SAME
  ORDER as that file's "columns" array. The Nth value belongs to the Nth column.
- "top_values" (categorical columns only): each entry is [value, count].
- Stats that don't apply to a column (e.g. "mean" on text) are omitted, not null.
Use these to understand what the data actually MEANS - the real entities, units and
categories behind the column names. But when filling "justification.profile_evidence",
cite aggregate stats (unique_values / null_percent / dtype), never a sample value.
"""

    def _guidance_section(self) -> str:
        """How to choose reports. Dropped on retries - see build_correction_prompt."""
        patterns_block = "\n".join(
            f"- {name}: {desc}" for name, desc in self.REPORT_PATTERNS.items()
        )

        return f"""Your task: (1) a short "dataset_overview" narrative, and (2) meaningful, EXECUTABLE
report recommendations built specifically from the data above.

DATASET OVERVIEW - 1-2 short plain-English paragraphs: what this dataset represents as
a whole, the key entities each file captures, how the files relate to each other
(reference the DETECTED CROSS-FILE RELATIONSHIPS when present), and the most notable
patterns. Ground every statement in the data above - do NOT invent facts, sources or
figures it doesn't support.

STEP 1 - Each column's "role" was computed deterministically from the actual data, not
guessed from its name. Treat it as ground truth; do not second-guess it:
- "primary_key" -> identifier-like; never a groupby column
- "categorical" -> good groupby/composition axis
- "measure" -> numeric; good for distribution/comparison, generally not a groupby column
- "temporal" -> good trend x-axis
Role doesn't capture everything, so also watch for:
- null_percent > 40% -> avoid as a primary axis unless you filter it first
- placeholder tokens ("-", "N/A", "?") in an otherwise numeric column are missing data
  -> filter them before aggregating that column

STEP 2 - Only recommend a pattern whose data requirements this profile actually meets.
Choose from this fixed set (do not invent others):
{patterns_block}

STEP 3 - If a categorical breakdown would help but no low-cardinality column exists,
"derive" one (regex_extract a family/prefix from high-cardinality text, or bin a numeric
column) rather than grouping on a near-unique column.

STEP 4 - Express each recommendation as an ordered pipeline (filter -> derive -> groupby
-> sort_limit -> join, using only the steps that apply), and declare the
expected_output_schema it produces so every column named in plotly_config exists in the
result. Include a step only if it does real work - never one with empty placeholder
fields like "groupby_columns": []. Special case: a DISTRIBUTION report with chart_type
"histogram" plots one continuous column's raw values (it bins them itself), so it needs
only a "filter" (e.g. not_null), never "groupby" or "sort_limit", and its plotly_config
must OMIT "y_axis" entirely - not null, not a column name.

STEP 5 - Always return EXACTLY 3 recommendations, ranked 1-3 by insight value. If the
data is too sparse, dirty or uniform to make the 3rd report as strong as the first two,
still return it as the lowest-ranked recommendation and attach a data_quality_warning
explaining the caveat - do NOT drop it or return fewer than 3. Write that warning in
plain English for a non-technical business user (see the data_quality_warning rule
below): describe the real-world impact, never the raw profile stat.
"""

    def _output_contract(self) -> str:
        """The required JSON shape and the rules a response must satisfy to validate.
        Always sent, including on retries."""
        return f"""IMPORTANT: You MUST return a valid JSON object (not markdown, not prose) with this exact structure:

{{
  "dataset_overview": "1-2 short paragraphs telling the story of what this data represents (see DATASET OVERVIEW above).",
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
          "operation_type": "join",
          "files_involved": ["file3.csv", "file4.csv"],
          "join_keys": [{{"left": "foreign_key_in_file3", "right": "primary_key_in_file4"}}],
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
1. All step types (filter/derive/groupby/sort_limit/join) are optional - omit any that doesn't apply rather than including it with empty/placeholder fields. A groupby you do include must have real groupby_columns AND aggregations.
1b. A derive "method" is one of "regex_extract", "bin", "quantile" - never invent another.
   - "bin": "bins" must be NUMBERS (boundary edges, e.g. [18,30,40,100]) - never range strings like "18-30", never dates. Display strings go in "bin_labels" (one FEWER than bins). Only bin a role=="measure" column; never bin a "temporal" one, whose edges would have to be dates - instead derive a period label first (e.g. regex_extract the year out of a formatted date) and group by that.
   - "quantile": cut POINTS between 0 and 1 in "quantiles" (e.g. [0.25,0.5,0.75] for quartiles), with "bin_labels" (one MORE than quantiles).
   Later steps reference a derived column by its "new_name" (e.g. "age_group"), never by the literal field name "derive_column".
2. Never group by an identifier/primary-key-like column (see Step 1).
2b. Groupby renames aggregated outputs to "{{column}}_{{function}}" (aggregations {{"Calories":"mean"}} produces a column literally named "Calories_mean"). Use that exact renamed form - never the bare column name - in sort_by, plotly_config axes and expected_output_schema.
2c. "groupby_columns" must NEVER be empty. For a count of records per value of a column, put that column in BOTH groupby_columns AND aggregations (e.g. "groupby_columns": ["year"], "aggregations": {{"year":"count"}}).
2d. If two joined files share a column name (e.g. both have "name"), any later step (groupby/filter/derive/sort_limit) using it must name the SPECIFIC file in that step's own "files_involved" - not reuse the join's file list. E.g. to group by the theme's "name" rather than the set's, that step's files_involved is ["themes.csv"], even though the join's was ["sets.csv","themes.csv"].
2e. "x_axis_label"/"y_axis_label" are OPTIONAL plain-English overrides of the displayed axis title; the raw "x_axis"/"y_axis" column names still pull the data, so never change those. Omit either when the raw name is already clear. A label must describe the column as actually computed - never imply an aggregation ("rate", "average", "%", "total") for a column that is not a groupby output (i.e. lacks an "_mean"/"_sum"/"_count"/etc. suffix per 2b). A pass-through column like "churn_signal" must not be labeled "Churn Rate"; only "churn_signal_mean" may be.
2f. Before any groupby/filter/sort_limit, confirm every column you reference (in aggregations, groupby_columns or filter_conditions) actually appears in the profiled "columns" of a file you have joined - never assume one exists because it sounds plausible. If the column lives on a DIFFERENT file, add a join to that file (per DETECTED CROSS-FILE RELATIONSHIPS) instead of aggregating a column that isn't there. When several files share the same join key (e.g. both "inventories" and "inventory_sets" join on "set_num"), pick the one whose profile actually contains the column you need.
3. Bar/pie charts must resolve to at most ~15-20 categories after sort_limit; use limit to enforce this.
4. Cite real profile values (unique_values, null_percent, dtype) in "justification" - never fabricate stats.
5. Rank recommendations by relevance/insight value.
6. Exactly 3 rationale_bullets per recommendation - short, plain English, no jargon.
7. Return EXACTLY 3 recommendations, ranked 1-3 by relevance/insight value - never fewer. If the data only strongly supports fewer, still produce a 3rd and mark it with a data_quality_warning (see Step 5).
8. If DETECTED CROSS-FILE RELATIONSHIPS were provided, at least one recommendation must use them via a "join" - do not confine every recommendation to a single file when the data supports connecting them.
9. Join columns MUST go in a "join_keys" array - never "join_column" or any other field name. "join_type" is optional (defaults to "inner"). Each entry is either:
   - a plain string when both files use the same column name, e.g. ["customer_id"]
   - a {{"left":...,"right":...}} object when the names differ (per column_a/column_b above), e.g. [{{"left":"theme_id","right":"id"}}], where "left" is the column in the FIRST file of "files_involved" and "right" the second.
10. "data_quality_warning" (when present) is shown directly to a non-technical business user, so write it in plain English about the real-world impact - NEVER expose raw profile internals. Do NOT mention column names, file names, "null_percent", "dtype", or precise decimals. Translate the stat into an everyday phrase and round to a friendly figure: e.g. instead of 'the item_id column in order_details has a null_percent of 1.12', write 'About 1% of orders are missing an item, which may slightly affect the ranking.' ("about 1%", "roughly 1 in 100", "a small number of records").

CRITICAL: Return ONLY the raw JSON object. Do NOT wrap in markdown code fences. Do NOT include ```json or ```. Start your response with {{ and end with }}.
"""

    def _profile_to_dict(self, profile: FileProfile) -> dict:
        """Convert FileProfile dataclass to dict for JSON serialization."""
        def _column_dict(col):
            d = {
                "name": col.name,
                "dtype": col.dtype,
                "unique_values": col.unique_values,
                "null_percent": col.null_percent,
                "role": col.role,
                "min": _round_if_numeric(col.min_value),
                "max": _round_if_numeric(col.max_value),
                "mean": _round_if_numeric(col.mean_value)
            }
            # min/max/mean are None on non-numeric columns - omit those keys rather
            # than spending tokens on "min":null,"max":null,"mean":null for every
            # text column. Only categorical columns carry top_values at all.
            d = {key: value for key, value in d.items() if value is not None}
            if col.top_values:
                d["top_values"] = [[t["value"], t["count"]] for t in col.top_values]
            return d

        column_order = [col.name for col in profile.columns]

        return {
            "filename": profile.filename,
            "row_count": profile.row_count,
            "columns": [_column_dict(col) for col in profile.columns],
            "quality_flags": profile.quality_flags,
            # Real rows so the LLM can read actual value combinations, not just
            # aggregate stats. Positional (aligned to "columns") instead of repeating
            # every column name on every row - see the reading note in the prompt.
            "sample_rows": [
                [row.get(name) for name in column_order] for row in profile.sample_rows
            ]
        }
