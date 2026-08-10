"""
Builds structured reports from AI recommendations using pandas/numpy.

Each recommendation's required_operations is executed as a single ordered
pipeline (filter -> derive -> groupby -> sort_limit -> join, using only the
steps the LLM actually included) rather than as independent operations, so
the DataFrame produced by one step feeds directly into the next.
"""

import re
import json
import math
import pandas as pd
import numpy as np
import os
from pathlib import Path
from typing import Dict, Any, Iterable, List, Optional, Tuple

from .chart_builder import split_agg_suffix
from .summary_builder import _normalize_key_value

def _debug_files_enabled() -> bool:
    """Whether to write report-generation traces to session_data/.

    Read on each call rather than captured at import: this module is imported (via
    response_validator) partway through AI_Engine's own imports, which is *before*
    AI_Engine calls load_dotenv() - so a module-level read here would miss anything
    set in .env and silently enable only half the debug output.

    The env var is deliberately the same one AI_Engine uses, so one setting governs
    every debug artifact a run produces. It isn't imported from AI_Engine because
    AI_Engine -> response_validator -> report_builder already, and importing it back
    would close an import cycle.
    """
    return os.getenv("SAVE_DEBUG_FILES", "false").strip().lower() in ("1", "true", "yes")

# Bookkeeping columns prepended to every successful report so a stored DataFrame can
# be traced back to the recommendation that produced it. They are not part of the
# user's data: anything counting or displaying "the report's columns" must exclude
# them, or a two-column report reports itself as having four.
METADATA_COLUMNS = ("recommendation_rank", "recommendation_name")

# Tokens that show up in "numeric" columns but really mean "missing". Compared as
# whole-cell matches (case-insensitive) so real substrings like "AB-123" are
# left untouched.
NULL_PLACEHOLDERS = {"-", "n/a", "na", "null", "none", "?", ""}

# Aggregation functions that only make sense on numbers. Applied to a text column,
# "sum" silently CONCATENATES ("Other"*n -> "OtherOther...") and the rest error out
# cryptically, so a numeric agg on a non-numeric column is caught up front instead.
# min/max/count/nunique are excluded - they're meaningful on text too.
_NUMERIC_ONLY_FUNCS = {"sum", "mean", "median", "std"}


def generate_report(
    recommendations: Dict[str, Any],
    report_type: str = "A",
    file_paths: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None,
    tables: Optional[Dict[str, pd.DataFrame]] = None
) -> pd.DataFrame:
    """
    Generate a structured report by executing ONE AI-recommended operation
    pipeline on real data - the recommendation selected by report_type.

    Args:
        recommendations: Cleaned JSON response from AI_Engine with required_operations
        report_type: Which recommendation to build ('A' -> recommendations[0],
            'B' -> recommendations[1], 'C' -> recommendations[2], etc.), matching
            the frontend's option cards
        file_paths: Dict mapping filename -> full filepath (e.g., {"orders.csv": "/path/to/file"})
        tables: Dict mapping table name -> already-loaded DataFrame (from
            DataLoader.tables()). Preferred over file_paths - uploaded files are
            deleted after analysis, and worksheets have no file of their own.
        session_id: If given, debug output (exact operations run + resulting data)
            is written to session_data/<session_id>/report_debug_<report_type>.txt

    Returns:
        pandas DataFrame containing that single recommendation's report data
    """
    print(f"\n[ReportBuilder] Generating report type: {report_type}")
    print(f"[ReportBuilder] Recommendations keys: {recommendations.keys()}")

    debug_lines = [f"Report type: {report_type}", f"Recommendations keys: {list(recommendations.keys())}"]

    # Extract recommendations list
    recs = recommendations.get("recommendations", [])

    if not recs:
        return _failed_report(session_id, report_type, debug_lines,
                              "No recommendations found in response")

    rec_idx = report_type_to_index(report_type)
    if rec_idx is None or rec_idx >= len(recs):
        return _failed_report(session_id, report_type, debug_lines,
                              f"report_type '{report_type}' does not map to any of the "
                              f"{len(recs)} available recommendation(s)")

    rec = recs[rec_idx]
    operations = rec.get("required_operations", [])
    debug_lines.append(
        f"Selected recommendation {rec_idx + 1}/{len(recs)}: {rec.get('report_name', 'Untitled')}"
    )
    debug_lines.append(f"Operations pipeline:\n{json.dumps(operations, indent=2, default=str)}")

    if not operations:
        return _failed_report(session_id, report_type, debug_lines,
                              f"Recommendation {rec_idx + 1} "
                              f"({rec.get('report_name', 'Untitled')}): no operations specified")

    # Logged after the pipeline above, so the debug trace shows what the LLM actually
    # sent first and then what we changed about it.
    operations, repairs = _repair_missing_groupby_columns(operations, rec)
    for note in repairs:
        print(f"[ReportBuilder] {note}")
        debug_lines.append(f"REPAIR: {note}")

    try:
        final_df = _execute_pipeline(operations, file_paths, tables)

        # Cross-check the produced columns against what the recommendation declared
        # in expected_output_schema. This is a soft check: it warns (console + debug
        # file + .attrs) so a recommendation whose pipeline silently produced the
        # wrong columns is caught here, rather than only surfacing later as a blank
        # or mis-plotted chart. Deliberately NOT a retry - retries cost LLM quota.
        schema_warning = _check_expected_output_schema(final_df, rec)
        if schema_warning:
            print(f"[ReportBuilder] {schema_warning}")
            debug_lines.append(f"SCHEMA WARNING: {schema_warning}")
            final_df.attrs["schema_warning"] = schema_warning

        # Held across the drops below: drop() returns a new frame, and .attrs only
        # survives that by pandas' own propagation rules rather than by guarantee.
        row_ledger = final_df.attrs.get("row_ledger")

        for col in METADATA_COLUMNS:
            # Defensive: a pipeline that produced a column of the same name would
            # otherwise make insert() raise on a duplicate.
            if col in final_df.columns:
                final_df = final_df.drop(columns=col)

        if row_ledger is not None:
            final_df.attrs["row_ledger"] = row_ledger
        final_df.insert(0, "recommendation_rank", rec.get("rank", 0))
        final_df.insert(1, "recommendation_name", rec.get("report_name", "Untitled"))

        msg = (f"Executed pipeline for recommendation {rec_idx + 1} "
               f"({len(operations)} operation(s))")
        print(f"[ReportBuilder] {msg}")
        debug_lines.append(msg)

    except Exception as e:
        return _failed_report(session_id, report_type, debug_lines,
                              f"Error executing pipeline for recommendation {rec_idx + 1}: {e}")

    _save_debug_output(session_id, report_type, debug_lines, final_df)

    return final_df


def report_type_to_index(report_type: str) -> Optional[int]:
    """Map a report_type letter ('A', 'B', 'C', ...) to a 0-based recommendation index."""
    if not report_type:
        return None
    letter = report_type.strip().upper()[:1]
    if len(letter) == 1 and "A" <= letter <= "Z":
        return ord(letter) - ord("A")
    return None


def _failed_report(
    session_id: Optional[str],
    report_type: str,
    debug_lines: List[str],
    message: str
) -> pd.DataFrame:
    """Empty report carrying the reason it failed in .attrs["error"], so callers can
    surface it instead of showing a silently blank report. Kept as a DataFrame
    return so existing callers that just check .empty are unaffected."""
    print(f"[ReportBuilder] WARNING: {message}")
    debug_lines.append(f"WARNING: {message}")

    empty = pd.DataFrame()
    empty.attrs["error"] = message
    _save_debug_output(session_id, report_type, debug_lines, empty)
    return empty


def _save_debug_output(
    session_id: Optional[str],
    report_type: str,
    debug_lines: List[str],
    final_df: pd.DataFrame
) -> None:
    """Write the report-generation trace to session_data/<session_id>/ for debugging.

    Off unless the SAVE_DEBUG_FILES env var is set, so a normal run leaves no artifacts.
    """
    if not session_id or not _debug_files_enabled():
        return

    session_dir = Path(__file__).resolve().parent.parent.parent / "session_data" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    debug_file = session_dir / f"report_debug_{report_type}.txt"

    content = "\n".join(debug_lines)
    content += f"\n\n{'='*80}\nReport shape: {final_df.shape} (rows, cols)\n{'='*80}\n"
    content += final_df.head(50).to_string()

    try:
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[ReportBuilder] Debug output saved: {debug_file}")
    except Exception as e:
        print(f"[ReportBuilder] Warning: Failed to save debug output: {e}")


def _execute_pipeline(
    operations: List[Dict[str, Any]],
    file_paths: Optional[Dict[str, str]] = None,
    tables: Optional[Dict[str, pd.DataFrame]] = None
) -> pd.DataFrame:
    """
    Execute an ordered list of operations as a single pipeline, threading one
    DataFrame through each step (filter -> derive -> groupby -> sort_limit -> join).

    Raises:
        ValueError: If the pipeline never loads any data or a step is invalid.
    """
    df: Optional[pd.DataFrame] = None
    loaded_files: Dict[str, pd.DataFrame] = {}
    merged_filenames: set = set()  # files whose columns are already part of `df`

    # Two different kinds of row loss, kept apart on purpose. A filter is the
    # report's definition - "revenue from won deals" means the lost rows were never
    # in scope, and reporting them as missing would read as a fault. A join miss is
    # the opposite: those rows were in scope and vanished anyway.
    scope: List[Dict[str, Any]] = []
    join_losses: List[Dict[str, Any]] = []
    join_rescues: List[Dict[str, Any]] = []
    source_rows: Optional[int] = None

    for op_idx, operation in enumerate(operations):
        operation_type = (operation.get("operation_type") or "").lower()
        files_involved = operation.get("files_involved", [])

        if operation_type == "join":
            df = _execute_join(operation, file_paths, loaded_files, df, merged_filenames,
                               tables, join_losses, join_rescues)
            continue

        # Lazily load the primary file the first time a step needs data.
        if df is None:
            if not files_involved:
                raise ValueError("No files specified for operation")
            filename = files_involved[0]
            df = _resolve_table(filename, file_paths, tables)
            loaded_files[filename] = df
            merged_filenames.add(filename)
            source_rows = len(df)

        if operation_type == "filter":
            rows_before = len(df)
            df = _execute_filter(df, operation)
            scope.append({
                "rows_before": rows_before,
                "rows_after": len(df),
                "conditions": [
                    {"column": c.get("column"), "condition": c.get("condition")}
                    for c in operation.get("filter_conditions", [])
                    if c.get("column")
                ],
            })
        elif operation_type == "derive":
            df = _execute_derive(df, operation)
        elif operation_type == "groupby":
            df = _execute_groupby(df, operation)
        elif operation_type == "sort_limit":
            df = _execute_sort_limit(df, operation)
        elif operation_type == "sort":  # backward-compat with older schema
            df = _execute_sort(df, operation)
        else:
            print(f"[ReportBuilder] Unknown operation type '{operation_type}' at step {op_idx + 1}, skipping")

    if df is None:
        raise ValueError("Pipeline produced no data (no operation loaded a file)")

    df.attrs["row_ledger"] = {
        "source_rows": source_rows,
        "scope": scope,
        "join_losses": join_losses,
        "join_rescues": join_rescues,
    }
    return df


def _execute_groupby(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute groupby operation with aggregations.

    Aggregated columns are renamed to "{column}_{function}" (e.g. "Calories_mean"),
    since that's the naming convention the LLM already uses in sort_by,
    plotly_config, and expected_output_schema - without this, those references
    would silently fail to match the actual output columns.
    """
    groupby_cols = operation.get("groupby_columns", [])
    agg_pairs = _normalize_aggregations(operation.get("aggregations", []))
    files_involved = operation.get("files_involved", [])

    if not groupby_cols and not agg_pairs:
        # A completely empty groupby step (no columns, no aggregations) is a
        # no-op the LLM shouldn't have included at all (e.g. a stray step
        # tacked onto a plain histogram/DISTRIBUTION report) - skip it rather
        # than failing the whole report over an unnecessary step.
        print("[ReportBuilder] Skipping empty groupby step (no groupby_columns or aggregations)")
        return df

    if not groupby_cols:
        raise ValueError("No groupby columns specified")

    # Resolve names that a prior join suffixed due to a same-named column
    # colliding across files (e.g. both files having "name").
    groupby_cols = [_resolve_column(df, col, files_involved) for col in groupby_cols]
    agg_pairs = [(_resolve_column(df, col, files_involved), func) for col, func in agg_pairs]

    # Filter to only valid columns
    valid_groupby = [col for col in groupby_cols if col in df.columns]
    if not valid_groupby:
        raise ValueError(f"No valid groupby columns found. Requested: {groupby_cols}, Available: {df.columns.tolist()}")

    # Build named aggregations (map new column name -> (source column, agg function))
    agg_named = {}
    for col, func in agg_pairs:
        if col in df.columns:
            if func in _NUMERIC_ONLY_FUNCS and not pd.api.types.is_numeric_dtype(df[col]):
                # e.g. summing a text column concatenates its values into one long
                # string instead of erroring - fail loudly with the real cause instead.
                raise ValueError(
                    f"Cannot apply '{func}' to non-numeric column '{col}' (dtype "
                    f"{df[col].dtype}). An upstream step likely produced text where "
                    f"numbers were expected - check the operations that created '{col}'."
                )
            agg_named[f"{col}_{func}"] = (col, func)

    if agg_pairs and not agg_named:
        # The LLM asked to aggregate column(s) that don't actually exist in the
        # joined data (e.g. it aggregated a "quantity" column that only lives in
        # a file it never joined in). Silently falling back to a plain count
        # would produce a result whose columns no longer match the
        # expected_output_schema/plotly_config the LLM declared, so the chart
        # would fail downstream with no indication of why - raise instead so
        # the real cause shows up in the report's error/debug output.
        raise ValueError(
            f"Aggregation column(s) {[col for col, _ in agg_pairs]} not found after "
            f"prior steps. Available columns: {df.columns.tolist()}"
        )

    if not agg_named:
        # No aggregations specified, just group and count
        result = df.groupby(valid_groupby, as_index=False).size().rename(columns={"size": "count"})
    else:
        result = df.groupby(valid_groupby, as_index=False).agg(**agg_named)

    return result


def _infer_groupby_columns(rec: Dict[str, Any], operation: Dict[str, Any]) -> List[str]:
    """Recover the group key(s) a groupby step should have named, from the rest of the
    same recommendation.

    The LLM intermittently returns "groupby_columns": [] on an otherwise correct
    groupby step, while still naming the group key everywhere else it describes the
    same report - expected_output_schema and plotly_config.x_axis. That single empty
    field is a hard failure at execution time (_execute_groupby raises), so the key it
    plainly meant is recovered here rather than losing the whole report to one slip.

    Aggregation outputs are excluded on both counts: this step's own "{column}_{func}"
    names, and any name carrying a known aggregation suffix - a schema entry like
    "close_value_sum" is the measure being aggregated, never the key it's grouped by.

    Returns [] when nothing can be inferred, leaving the step to fail with the original
    error rather than guessing at a column.
    """
    agg_outputs = {
        f"{col}_{func}"
        for col, func in _normalize_aggregations(operation.get("aggregations", []))
    }

    def is_group_key(name: Optional[str]) -> bool:
        return bool(name) and name not in agg_outputs and split_agg_suffix(name)[1] is None

    inferred = [
        col.get("name") for col in rec.get("expected_output_schema") or []
        if is_group_key(col.get("name"))
    ]

    if not inferred:
        x_axis = (rec.get("plotly_config") or {}).get("x_axis")
        inferred = [x_axis] if is_group_key(x_axis) else []

    return list(dict.fromkeys(inferred))  # de-duplicated, order preserved


def _repair_missing_groupby_columns(
    operations: List[Dict[str, Any]],
    rec: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fill in the groupby steps whose groupby_columns the LLM left empty (see
    _infer_groupby_columns), returning (operations, notes describing each repair).

    A step with neither columns nor aggregations is left alone - that one is a stray
    no-op _execute_groupby already skips, and inventing a groupby for it would turn a
    harmless empty step into a real (and wrong) aggregation.

    Operations are copied rather than mutated: the recommendations dict passed in is
    the same object the API hands back to the frontend, and a report run should not
    quietly rewrite it.
    """
    repaired: List[Dict[str, Any]] = []
    notes: List[str] = []

    for op in operations:
        is_empty_groupby = (
            (op.get("operation_type") or "").lower() == "groupby"
            and not op.get("groupby_columns")
            and _normalize_aggregations(op.get("aggregations", []))
        )
        if is_empty_groupby:
            inferred = _infer_groupby_columns(rec, op)
            if inferred:
                op = {**op, "groupby_columns": inferred}
                notes.append(
                    f"groupby step specified no groupby_columns; inferred {inferred} from "
                    f"the recommendation's expected_output_schema/plotly_config"
                )
        repaired.append(op)

    return repaired, notes


def _execute_filter(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute a filter operation.

    Supports the current schema's filter_conditions: [{"column", "condition"}],
    where condition is either "not_null" / "is_null" or a comparison expression
    like "> 10", "== Latte", "!= 0". Falls back to the older dict-based
    {"filters": {"column": value}} equality format if present.
    """
    result = df
    files_involved = operation.get("files_involved", [])

    filter_conditions = operation.get("filter_conditions", [])
    for cond in filter_conditions:
        column = _resolve_column(result, cond.get("column"), files_involved)
        condition = cond.get("condition")
        if not column or column not in result.columns or condition is None:
            continue
        result = _apply_filter_condition(result, column, str(condition))

    # Backward-compat: older schema used a flat {"filters": {col: value}} dict
    legacy_filters = operation.get("filters", {})
    for col, condition in legacy_filters.items():
        if col in result.columns:
            result = result[result[col] == condition]

    return result


def _apply_filter_condition(df: pd.DataFrame, column: str, condition: str) -> pd.DataFrame:
    """Apply a single textual filter condition to one column."""
    condition = condition.strip()
    lowered = condition.lower()

    if lowered in ("not_null", "notnull", "not null"):
        series = _normalize_nulls(df[column])
        return df[series.notna()]

    if lowered in ("is_null", "isnull", "null"):
        series = _normalize_nulls(df[column])
        return df[series.isna()]

    # A bare "=" is accepted alongside "==" because the LLM often writes the
    # SQL-style form (e.g. "='Won'"); without it the whole condition falls through
    # to the literal fallback below and silently matches nothing.
    match = re.match(r'^(>=|<=|==|!=|=|>|<)\s*(.+)$', condition)
    if match:
        op, raw_value = match.groups()
        if op == "=":
            op = "=="
        raw_value = raw_value.strip().strip('"\'')

        try:
            value = float(raw_value)
            series = pd.to_numeric(_normalize_nulls(df[column]), errors="coerce")
        except ValueError:
            value = raw_value
            series = df[column].astype(str)

        if op == ">":
            return df[series > value]
        if op == "<":
            return df[series < value]
        if op == ">=":
            return df[series >= value]
        if op == "<=":
            return df[series <= value]
        if op == "==":
            return df[series == value]
        if op == "!=":
            return df[series != value]

    # Fallback: treat the condition as a plain equality match (quotes stripped, so
    # an operator-less "'Won'" still matches)
    return df[df[column].astype(str) == condition.strip('"\'')]


def _execute_derive(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute a derive operation, adding a new computed column."""
    derive = operation.get("derive_column") or {}
    new_name = derive.get("new_name")
    source_column = _resolve_column(df, derive.get("source_column"), operation.get("files_involved", []))
    method = (derive.get("method") or "").lower()

    if not new_name or not source_column or source_column not in df.columns:
        print(f"[ReportBuilder] Skipping derive: invalid spec {derive}")
        return df

    df = df.copy()

    if method == "compute":
        return _execute_compute(df, derive, new_name, source_column, operation.get("files_involved", []))

    if method == "regex_extract":
        pattern = derive.get("pattern")
        if not pattern:
            print(f"[ReportBuilder] Skipping regex_extract derive: no pattern given")
            return df
        try:
            extracted = df[source_column].astype(str).str.extract(pattern, expand=False)
        except ValueError as e:
            # AI-generated patterns don't always include a capture group (e.g. "^\d{4}"),
            # which pandas' str.extract() requires - wrap the whole pattern in one and retry.
            if "capture group" not in str(e):
                raise
            extracted = df[source_column].astype(str).str.extract(f"({pattern})", expand=False)
        # A pattern that matches NOTHING (e.g. the model put arithmetic like
        # "unitPrice * quantity" where a regex belongs) would otherwise fill every row
        # with the "Other" bucket, producing a column of literal "Other" strings that
        # then silently corrupts a downstream sum. Treat an all-unmatched extract as a
        # failed derive and skip it, so the missing column surfaces as a loud error
        # rather than a plausible-looking wrong chart. A partial match is legitimate
        # (genuine "Other" bucket for the unmatched rows) and still fills as before.
        if extracted.notna().sum() == 0:
            print(f"[ReportBuilder] Skipping regex_extract derive: pattern {pattern!r} "
                  f"matched no values in {source_column!r} (is it arithmetic? use method='compute')")
            return df
        df[new_name] = extracted.fillna("Other")

    elif method == "bin":
        numeric_col = pd.to_numeric(_normalize_nulls(df[source_column]), errors="coerce")
        bins = derive.get("bins") or []
        labels = derive.get("bin_labels") or None

        # "bins" must be numeric boundary edges (e.g. [18, 30, 40, 100]) - the
        # LLM occasionally puts display range strings like "18-25" here
        # instead (those belong in "bin_labels"), which pd.cut can't compare
        # against the numeric column. Fall back to quartile binning rather
        # than crashing the whole report over it.
        if bins and not all(isinstance(b, (int, float)) for b in bins):
            print(f"[ReportBuilder] Ignoring non-numeric bin edges {bins}, falling back to quartile binning")
            bins = []

        if bins:
            # Honor explicit LLM edges (they come paired with matching bin_labels).
            try:
                df[new_name] = pd.cut(numeric_col, bins=bins, labels=labels)
            except (ValueError, TypeError) as e:
                print(f"[ReportBuilder] Could not bin column {source_column} with bins={bins}: {e}")
                return df
        else:
            # No usable edges given: choose them deterministically via Freedman-Diaconis
            # (bin width from the data's IQR) rather than trusting a guessed bin count.
            # Auto interval labels here since the FD bin count won't match any supplied
            # bin_labels. Fall back to quartiles only if FD can't be computed.
            fd_edges = _freedman_diaconis_edges(numeric_col)
            try:
                if fd_edges:
                    df[new_name] = pd.cut(numeric_col, bins=fd_edges, include_lowest=True)
                else:
                    df[new_name] = pd.qcut(numeric_col, q=4, labels=labels, duplicates="drop")
            except (ValueError, TypeError) as e:
                print(f"[ReportBuilder] Could not bin column {source_column}: {e}")
                return df

    elif method == "quantile":
        # Split into quantile-based groups from cut POINTS (e.g. [0.25, 0.5,
        # 0.75] -> quartiles), as opposed to "bin"'s fixed numeric edges.
        numeric_col = pd.to_numeric(_normalize_nulls(df[source_column]), errors="coerce")
        quantiles = derive.get("quantiles") or []
        labels = derive.get("bin_labels") or None

        if not quantiles:
            print(f"[ReportBuilder] Skipping quantile derive: no quantiles given")
            return df

        try:
            q_bounds = sorted({0.0, 1.0, *(float(q) for q in quantiles)})
            df[new_name] = pd.qcut(numeric_col, q=q_bounds, labels=labels, duplicates="drop")
        except (ValueError, TypeError) as e:
            print(f"[ReportBuilder] Could not quantile-bin column {source_column} with quantiles={quantiles}: {e}")
            return df

    else:
        print(f"[ReportBuilder] Unknown derive method '{method}', skipping")

    return df


def _execute_compute(
    df: pd.DataFrame,
    derive: Dict[str, Any],
    new_name: str,
    source_column: str,
    files_involved: List[str],
) -> pd.DataFrame:
    """Add a column computed as an arithmetic expression: source_column (the left
    operand) `operator` a second column (right_column) or a constant (right_value).

    This is the correct home for things like "line_total = unitPrice * quantity".
    Both operands are coerced to numbers (placeholder nulls cleaned first), so a stray
    non-numeric cell becomes NaN for that row rather than failing the whole derive.
    Any malformed spec is skipped with a message rather than raised, matching the rest
    of _execute_derive - a bad optional step shouldn't sink the whole report.
    """
    operator = derive.get("operator")
    if operator not in ("+", "-", "*", "/"):
        print(f"[ReportBuilder] Skipping compute derive: missing/unknown operator {operator!r}")
        return df

    left = pd.to_numeric(_normalize_nulls(df[source_column]), errors="coerce")

    right_column = derive.get("right_column")
    right_value = derive.get("right_value")
    if right_column:
        resolved = _resolve_column(df, right_column, files_involved)
        if resolved not in df.columns:
            print(f"[ReportBuilder] Skipping compute derive: right_column {right_column!r} not found")
            return df
        right: Any = pd.to_numeric(_normalize_nulls(df[resolved]), errors="coerce")
    elif right_value is not None:
        right = float(right_value)
    else:
        print(f"[ReportBuilder] Skipping compute derive: neither right_column nor right_value given")
        return df

    if operator == "/":
        # Guard divide-by-zero: a zero divisor becomes NaN (not inf) so it drops out of
        # a later sum/mean instead of poisoning it.
        if isinstance(right, pd.Series):
            right = right.replace(0, np.nan)
        elif right == 0:
            print(f"[ReportBuilder] Skipping compute derive: division by zero constant")
            return df

    ops = {"+": left.add, "-": left.sub, "*": left.mul, "/": left.truediv}
    df[new_name] = ops[operator](right)
    return df


def _execute_sort_limit(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute a sort + top-N limit operation."""
    sort_by = _resolve_column(df, operation.get("sort_by"), operation.get("files_involved", []))
    ascending = operation.get("ascending", True)
    limit = operation.get("limit")

    result = df
    if sort_by and sort_by in result.columns:
        result = result.sort_values(sort_by, ascending=ascending).reset_index(drop=True)

    if limit:
        try:
            result = result.head(int(limit))
        except (TypeError, ValueError):
            pass

    return result


def _freedman_diaconis_edges(series: pd.Series) -> Optional[List[float]]:
    """Bin edges chosen by the Freedman-Diaconis rule (width = 2*IQR*n^(-1/3)), or
    None if they can't be computed (too few points, zero IQR, or flat data). Used as
    the deterministic default when the LLM supplies no usable numeric bin edges - a
    small model has no principled way to pick a bin count, so it's computed here."""
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
    # Cap the count so a tiny width on a wide range can't explode into thousands of bins.
    n_bins = min(50, max(1, int(math.ceil((hi - lo) / width))))
    return list(np.linspace(lo, hi, n_bins + 1))


def _normalize_aggregations(aggregations: Any) -> List[tuple]:
    """Normalize aggregations to (column, func) pairs, accepting both the current
    list form ([{"column": "revenue", "func": "sum"}]) and the legacy dict form
    ({"revenue": "sum"}). The dict path keeps old saved sessions replayable when
    their operations are fed straight to report_builder without going through the
    Pydantic model (which would otherwise have normalized them already)."""
    if isinstance(aggregations, dict):
        return [(col, func) for col, func in aggregations.items()]

    pairs = []
    for item in aggregations or []:
        if isinstance(item, dict):
            col, func = item.get("column"), item.get("func")
            if col and func:
                pairs.append((col, func))
    return pairs


def _normalize_join_keys(join_keys: List[Any]) -> List[tuple]:
    """Normalize join_keys entries to (left_column, right_column) pairs.

    Supports plain strings (same column name on both sides, e.g. "customer_id")
    and {"left": ..., "right": ...} dicts for foreign/primary keys named
    differently on each side (e.g. left="customer_id", right="id").
    """
    normalized = []
    for key in join_keys:
        if isinstance(key, str):
            normalized.append((key, key))
        elif isinstance(key, dict):
            left = key.get("left") or key.get("left_column")
            right = key.get("right") or key.get("right_column")
            if left and right:
                normalized.append((left, right))
    return normalized


def _unique_temp_name(base: str, taken: Iterable[str]) -> str:
    """A working column name guaranteed absent from both sides of a merge.

    pandas' own suffixing can only ever append to an existing name, so a name absent
    from both frames cannot be produced by the merge either.
    """
    taken = set(taken)
    name = base
    while name in taken:
        name += "_"
    return name


def _normalized_key_series(s: pd.Series) -> Optional[pd.Series]:
    """Normalized keys for a text column, or None when normalization doesn't apply.

    Numeric, boolean and datetime keys are skipped on purpose. They have no spelling
    variants, and stringifying them first invents collisions that have nothing to do
    with how anyone spelled anything: a float column formats as "101.0" -> "1010" and
    "1e+05" -> "1e05".
    """
    if (pd.api.types.is_numeric_dtype(s)
            or pd.api.types.is_bool_dtype(s)
            or pd.api.types.is_datetime64_any_dtype(s)
            or pd.api.types.is_timedelta64_dtype(s)):
        return None

    # object first, so a Categorical doesn't come back Categorical and break the
    # merge against a plain object column.
    values = s.astype(object)
    # Mapped over distinct values: a 200k-row lookup with 300 products would
    # otherwise pay 200k Python-level normalizations.
    mapping = {v: _normalize_key_value(v) for v in pd.unique(values)}
    return values.map(mapping)


# The four functions below implement join-miss rescue: an inner join silently drops
# rows with no match on the right (e.g. "GTXPro" vs "GTX Pro" between two files), so
# `losses`/`rescues` make that visible and, where the mismatch is just spelling/case/
# punctuation, recover the row instead of dropping it.
def _execute_join(
    operation: Dict[str, Any],
    file_paths: Optional[Dict[str, str]],
    loaded_files: Dict[str, pd.DataFrame],
    current_df: Optional[pd.DataFrame],
    merged_filenames: set,
    tables: Optional[Dict[str, pd.DataFrame]] = None,
    losses: Optional[List[Dict[str, Any]]] = None,
    rescues: Optional[List[Dict[str, Any]]] = None
) -> pd.DataFrame:
    """Execute a join across two or more files (or the current pipeline result).

    Files already merged into current_df are skipped rather than re-merged, since
    re-merging on a later shared key would fan out rows instead of narrowing them.
    """
    files_involved = operation.get("files_involved", [])
    join_keys = operation.get("join_keys", [])
    join_type = operation.get("join_type") or "inner"

    if not join_keys:
        raise ValueError("No join_keys specified for join operation")

    normalized_keys = _normalize_join_keys(join_keys)
    if not normalized_keys:
        raise ValueError(f"No usable join_keys found in {join_keys}")

    new_dfs = []  # (filename, df) pairs, in files_involved order
    for filename in files_involved:
        if filename in merged_filenames:
            continue  # already part of current_df - don't re-merge it
        if filename in loaded_files:
            new_dfs.append((filename, loaded_files[filename]))
        else:
            loaded = _resolve_table(filename, file_paths, tables)
            loaded_files[filename] = loaded
            new_dfs.append((filename, loaded))
        merged_filenames.add(filename)

    dfs = ([(None, current_df)] if current_df is not None else []) + new_dfs

    if len(dfs) < 2:
        raise ValueError("Join requires at least 2 dataframes/files")

    result = dfs[0][1]
    for right_filename, right in dfs[1:]:
        valid_pairs = [
            (left_col, right_col) for left_col, right_col in normalized_keys
            if left_col in result.columns and right_col in right.columns
        ]
        if not valid_pairs:
            raise ValueError(f"No valid join keys found among {join_keys}")
        left_on = [p[0] for p in valid_pairs]
        right_on = [p[1] for p in valid_pairs]
        # Custom suffix (file stem, not pandas' generic "_x"/"_y") so downstream
        # steps referencing a bare column name that collided across files (e.g.
        # both sides having "name") can be resolved back via _resolve_column.
        right_suffix = f"_{_file_stem(right_filename)}" if right_filename else "_right"

        if join_type == "inner" and losses is not None:
            # Rescue only applies to inner joins. An outer join drops nothing, so
            # there is nothing to recover, and re-matching there would change which
            # right row attaches to a left row - a behaviour change, not a repair.
            result = _inner_join_counting_misses(
                result, right, left_on, right_on, right_suffix, right_filename,
                losses, rescues,
            )
        else:
            result = result.merge(right, left_on=left_on, right_on=right_on, how=join_type,
                                   suffixes=("", right_suffix))

    return result


def _inner_join_counting_misses(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_on: List[str],
    right_on: List[str],
    right_suffix: str,
    right_filename: Optional[str],
    losses: List[Dict[str, Any]],
    rescues: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Inner-join `left` to `right`, recording the left rows that matched nothing.

    Implemented as a left join with an indicator, then dropping the misses -
    produces the same rows (and row multiplication) an inner join would, while
    making the dropped rows countable (comparing row totals before/after would be
    wrong, since a one-to-many match can grow the count while still losing rows).
    When `rescues` is given, missed rows get a second chance via _rescue_by_spelling.
    """
    taken = set(left.columns) | set(right.columns)
    indicator = _unique_temp_name("_join_match", taken)
    # A positional tag, not the index: an earlier step may leave a duplicated or
    # non-range index, and a one-to-many match fans one left row into several merged
    # rows, so neither the index nor the merged row number addresses left rows.
    row_id = _unique_temp_name("_join_left_row", taken)

    left_tagged = left.assign(**{row_id: np.arange(len(left), dtype="int64")})
    merged = left_tagged.merge(
        right, left_on=left_on, right_on=right_on, how="left",
        suffixes=("", right_suffix), indicator=indicator,
    )

    missed = merged[indicator] == "left_only"
    matched = merged.loc[~missed].drop(columns=[indicator])
    missed_ids = merged.loc[missed, row_id].to_numpy()

    rescued = None
    rescued_ids: set = set()
    if rescues is not None and len(missed_ids):
        rescued, pairs = _rescue_by_spelling(
            left_tagged, missed_ids, right, left_on, right_on,
            right_suffix, list(matched.columns),
        )
        if rescued is not None and not rescued.empty:
            rescued_ids = set(rescued[row_id].tolist())
            rescues.append({
                "file": right_filename,
                # Left rows, counted before any one-to-many fanout: this answers
                # "how many rows did the report nearly lose", which is what the
                # sentence built from it says.
                "rows": len(rescued_ids),
                "key": left_on[0],
                "pairs": pairs[:3],
            })

    if rescued is not None and not rescued.empty:
        result = pd.concat([matched, rescued], ignore_index=True)
        # Stable sort on the positional tag puts rescued rows back where their left
        # row was rather than in a block at the end, and keeps the relative order of
        # a fanned-out row's matches.
        result = result.sort_values(row_id, kind="stable")
    else:
        result = matched

    still_missed = [int(i) for i in missed_ids if int(i) not in rescued_ids]
    if still_missed:
        # The key values themselves, because the fix is almost always visible in
        # them - a stray space, a case difference, a trailing code. Drawn from the
        # rows that are *still* missing, so a rescued value is never listed as lost.
        examples = (
            left[left_on[0]].iloc[still_missed].dropna().astype(str).unique()[:3].tolist()
        )
        losses.append({
            "file": right_filename,
            "rows": len(still_missed),
            "key": left_on[0],
            "examples": examples,
        })

    return result.drop(columns=[row_id]).reset_index(drop=True)


def _unambiguous_key_map(
    frame: pd.DataFrame,
    raw_cols: List[str],
    ignore_raw: Optional[set] = None,
) -> Optional[Dict[tuple, tuple]]:
    """Map each normalized key tuple to the single raw key tuple it stands for.

    Tuples whose normalization is shared by two or more distinct raw values are
    left out - matching would be a guess, and a wrong guess merges two real
    categories, which is worse than the dropped rows it would fix. Returns None
    when any key column can't be normalized (numeric, boolean, dates).
    """
    normalized = []
    for col in raw_cols:
        norm = _normalized_key_series(frame[col])
        if norm is None:
            return None
        normalized.append(norm)

    buckets: Dict[tuple, set] = {}
    raw_values = [frame[col].astype(object) for col in raw_cols]
    for pos in range(len(frame)):
        norm_key = tuple(n.iat[pos] for n in normalized)
        # One unnormalizable component makes the whole tuple unusable. This is also
        # what stops a null key on one side matching a null key on the other.
        if any(part is None for part in norm_key):
            continue
        raw_key = tuple(r.iat[pos] for r in raw_values)
        if ignore_raw is not None and raw_key in ignore_raw:
            continue
        buckets.setdefault(norm_key, set()).add(raw_key)

    return {norm: next(iter(raws)) for norm, raws in buckets.items() if len(raws) == 1}


def _rescue_by_spelling(
    left_tagged: pd.DataFrame,
    missed_ids: np.ndarray,
    right: pd.DataFrame,
    left_on: List[str],
    right_on: List[str],
    right_suffix: str,
    matched_columns: List[str],
) -> Tuple[Optional[pd.DataFrame], List[Dict[str, str]]]:
    """Re-match rows an exact join missed, ignoring case, spacing and punctuation.

    Rescued rows take the right side's spelling before merging, so the result is a
    second exact merge (identical columns/dtypes) and a later group-by on the key
    produces one bar rather than two.
    """
    right_map = _unambiguous_key_map(right, right_on)
    if not right_map:
        return None, []

    # The left census runs over the whole left frame, not just the missed rows, so a
    # value that already matched exactly still counts as a distinct spelling. It is
    # exempted when it appears verbatim on the right, though: a left file holding
    # both "GTX Pro" and "GTXPro" would otherwise block its own rescue, which is
    # precisely the case with the clearest evidence.
    right_raw = set(right_map.values())
    left_map = _unambiguous_key_map(left_tagged, left_on, ignore_raw=right_raw)
    if not left_map:
        return None, []

    remap = {
        left_raw: right_map[norm]
        for norm, left_raw in left_map.items()
        if norm in right_map and left_raw != right_map[norm]
    }
    if not remap:
        return None, []

    candidates = left_tagged.iloc[missed_ids]
    keys = list(zip(*(candidates[col].astype(object) for col in left_on)))
    replacements = [remap.get(key) for key in keys]
    keep = [i for i, r in enumerate(replacements) if r is not None]
    if not keep:
        return None, []

    rewritten = candidates.iloc[keep].copy()
    for col_pos, col in enumerate(left_on):
        rewritten[col] = [replacements[i][col_pos] for i in keep]

    rescued = rewritten.merge(
        right, left_on=left_on, right_on=right_on, how="inner",
        suffixes=("", right_suffix),
    )

    # A rescue is an enhancement; a bug in it must never fail a report that would
    # otherwise have built, so a surprise here skips rather than raises.
    if set(rescued.columns) != set(matched_columns):
        print("[ReportBuilder] Join key rescue changed the column set - skipping it")
        return None, []
    rescued = rescued[matched_columns]

    pairs = []
    for key in dict.fromkeys(keys[i] for i in keep):
        pairs.append({"left": str(key[0]), "right": str(remap[key][0])})
    return rescued, pairs


def _execute_sort(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute sort operation (older schema: sort_columns/ascending)."""
    sort_by = operation.get("sort_columns", [])
    ascending = operation.get("ascending", True)

    valid_sort = [col for col in sort_by if col in df.columns]
    if valid_sort:
        return df.sort_values(valid_sort, ascending=ascending).reset_index(drop=True)
    return df


def _file_stem(filename: str) -> str:
    """Filename with extension stripped, lowercased, safe for use as a suffix."""
    stem = os.path.splitext(filename)[0].strip().lower()
    return re.sub(r"[^0-9a-z]+", "_", stem)


def _resolve_column(df: pd.DataFrame, name: Optional[str], files_involved: List[str]) -> Optional[str]:
    """Resolve a column name that may have been suffixed by _execute_join because
    it collided with a same-named column from another file (e.g. both "parts.csv"
    and "part_categories.csv" having a "name" column becomes "name" and
    "name_part_categories"). If the operation's files_involved names one of the
    joined-in files, that hint is checked FIRST - it means "the column from this
    specific file", which should win even when an unsuffixed same-named column
    also exists (from whichever file happened to be the join's left/anchor
    side). Falls back to the plain name unchanged if nothing else matches, so
    normal non-colliding lookups are unaffected.
    """
    if name is None:
        return None
    for filename in files_involved or []:
        candidate = f"{name}_{_file_stem(filename)}"
        if candidate in df.columns:
            return candidate
    if name in df.columns:
        return name
    return name


def _check_expected_output_schema(df: pd.DataFrame, rec: Dict[str, Any]) -> Optional[str]:
    """Return a warning if the recommendation's declared expected_output_schema columns
    aren't all present in the produced DataFrame, else None.

    Uses _resolve_column so a name the LLM declared plainly still matches after a join
    suffixed it (e.g. declared "name" -> actual "name_themes"). Returns a description
    only; the caller decides how to surface it (this never raises)."""
    expected = [c.get("name") for c in rec.get("expected_output_schema", []) if c.get("name")]
    if not expected:
        return None

    files_involved: List[str] = []
    for op in rec.get("required_operations", []) or []:
        for filename in op.get("files_involved", []) or []:
            if filename not in files_involved:
                files_involved.append(filename)

    missing = [name for name in expected if _resolve_column(df, name, files_involved) not in df.columns]
    if missing:
        return (
            f"Report output does not match expected_output_schema - declared column(s) "
            f"{missing} are not in the result (actual columns: {list(df.columns)}). "
            f"The chart may be blank or plot the wrong values."
        )
    return None


def resolve_plotly_axes(
    df: pd.DataFrame,
    plotly_config: Optional[Dict[str, Any]],
    operations: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Resolve plotly_config's x_axis/y_axis to the actual output column names.

    The LLM writes plotly_config against expected_output_schema, which doesn't
    account for _execute_join's collision suffixing (e.g. both "orders.csv" and
    "customers.csv" having a "name" column makes the real output column
    "name_customers"). Reuses _resolve_column with every file named across the
    pipeline's operations, since we don't know upfront which operation's
    files_involved is the relevant hint for a given axis.
    """
    if not plotly_config:
        return plotly_config or {}

    files_involved: List[str] = []
    for op in operations or []:
        for filename in op.get("files_involved", []) or []:
            if filename not in files_involved:
                files_involved.append(filename)

    resolved = dict(plotly_config)
    for key in ("x_axis", "y_axis"):
        if resolved.get(key):
            resolved[key] = _resolve_column(df, resolved[key], files_involved)
    return resolved


def _normalize_nulls(series: pd.Series) -> pd.Series:
    """Treat common missing-value placeholder tokens (e.g. '-', 'N/A') as NaN."""
    def clean(value):
        if pd.isna(value):
            return value
        if isinstance(value, str) and value.strip().lower() in NULL_PLACEHOLDERS:
            return np.nan
        return value

    return series.apply(clean)


def _find_file_path(filename: str, file_paths: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Find the full path to a file by name.

    Searches in:
    1. Provided file_paths dict
    2. datasets/ directory
    3. session_data/profiles/ directory
    """
    if not filename:
        return None

    # Check provided file_paths
    if file_paths and filename in file_paths:
        return file_paths[filename]

    # Search in datasets directory
    datasets_root = os.path.join(os.path.dirname(__file__), "..", "..", "datasets")
    for root, dirs, files in os.walk(datasets_root):
        if filename in files:
            return os.path.join(root, filename)

    # Search in session_data/profiles
    profiles_root = os.path.join(os.path.dirname(__file__), "..", "..", "session_data", "profiles")
    if os.path.exists(profiles_root):
        full_path = os.path.join(profiles_root, filename)
        if os.path.exists(full_path):
            return full_path

    return None


CSV_ENCODINGS = ['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'cp1252']


def _read_csv_with_encoding(filepath: str) -> pd.DataFrame:
    """Try to read a CSV with multiple encoding options (mirrors DataLoader)."""
    for encoding in CSV_ENCODINGS:
        try:
            return pd.read_csv(filepath, encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue

    raise ValueError(
        f"Could not read {filepath} with any supported encoding. Tried: {CSV_ENCODINGS}"
    )


def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a DataFrame for report operations. Applied to both files read from
    disk and in-memory session tables so the two paths behave identically. Copies
    so cached session tables are never mutated."""
    df = df.copy()

    # Strip stray whitespace from column names (e.g. " Protein (g) ") so they
    # match the (also-stripped) names shown to the LLM during profiling -
    # otherwise a recommendation referencing "Protein (g)" silently fails to
    # match the real column and that report/chart can't be built at all.
    df.columns = df.columns.map(lambda c: c.strip() if isinstance(c, str) else c)

    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = _normalize_nulls(df[col])

    return _coerce_numeric_columns(df)


def _load_file(filepath: str) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame, normalizing placeholder nulls."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
        df = pd.read_excel(filepath)
    else:
        df = _read_csv_with_encoding(filepath)

    return _prepare_df(df)


def _resolve_table(
    filename: str,
    file_paths: Optional[Dict[str, str]] = None,
    tables: Optional[Dict[str, pd.DataFrame]] = None
) -> pd.DataFrame:
    """Get the data for a table named in a recommendation's files_involved.

    Prefers the session's in-memory tables (uploaded data, including individual
    worksheets, which have no file of their own on disk), falling back to reading
    a real file for non-upload flows like datasets/.
    """
    if tables and filename in tables:
        return _prepare_df(tables[filename])

    filepath = _find_file_path(filename, file_paths)
    if filepath:
        return _load_file(filepath)

    available = sorted(tables) if tables else []
    raise ValueError(
        f"File not found: {filename}"
        + (f" (available tables: {available})" if available else "")
    )


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert object columns back to numeric dtype when they're "numeric-like"
    after null-placeholder cleanup (e.g. a Calories column read as strings
    because some cells were '-'). A column is coerced only if at least 90% of
    its non-null values parse as numbers, so genuinely text columns are left alone.
    """
    for col in df.select_dtypes(include=["object"]).columns:
        non_null = df[col].notna().sum()
        if non_null == 0:
            continue
        coerced = pd.to_numeric(df[col], errors="coerce")
        if coerced.notna().sum() >= non_null * 0.9:
            df[col] = coerced
    return df
