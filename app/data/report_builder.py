"""
Builds structured reports from AI recommendations using pandas/numpy.

Each recommendation's required_operations is executed as a single ordered
pipeline (filter -> derive -> groupby -> sort_limit -> join, using only the
steps the LLM actually included) rather than as independent operations, so
the DataFrame produced by one step feeds directly into the next.
"""

import re
import json
import pandas as pd
import numpy as np
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

# Tokens that show up in "numeric" columns but really mean "missing" (e.g. the
# Starbucks dataset uses "-" for unavailable nutrition facts). Compared as
# whole-cell matches (case-insensitive) so real substrings like "AB-123" are
# left untouched.
NULL_PLACEHOLDERS = {"-", "n/a", "na", "null", "none", "?", ""}


def generate_report(
    recommendations: Dict[str, Any],
    report_type: str = "A",
    file_paths: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None
) -> pd.DataFrame:
    """
    Generate a structured report by executing ONE AI-recommended operation
    pipeline on real data - the recommendation selected by report_type.

    Args:
        recommendations: Cleaned JSON response from AI_Engine with required_operations
        report_type: Which recommendation to build ('A' -> recommendations[0],
            'B' -> recommendations[1], 'C' -> recommendations[2], etc.), matching
            the frontend's option cards
        file_paths: Dict mapping filename -> full filepath (e.g., {"starbucks.csv": "/path/to/file"})
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
        print("[ReportBuilder] WARNING: No recommendations found in response")
        debug_lines.append("WARNING: No recommendations found in response")
        _save_debug_output(session_id, report_type, debug_lines, pd.DataFrame())
        return pd.DataFrame()

    rec_idx = report_type_to_index(report_type)
    if rec_idx is None or rec_idx >= len(recs):
        msg = (f"report_type '{report_type}' does not map to any of the "
               f"{len(recs)} available recommendation(s)")
        print(f"[ReportBuilder] WARNING: {msg}")
        debug_lines.append(f"WARNING: {msg}")
        _save_debug_output(session_id, report_type, debug_lines, pd.DataFrame())
        return pd.DataFrame()

    rec = recs[rec_idx]
    operations = rec.get("required_operations", [])
    debug_lines.append(
        f"Selected recommendation {rec_idx + 1}/{len(recs)}: {rec.get('report_name', 'Untitled')}"
    )
    debug_lines.append(f"Operations pipeline:\n{json.dumps(operations, indent=2, default=str)}")

    final_df = pd.DataFrame()

    if not operations:
        msg = f"Recommendation {rec_idx + 1} ({rec.get('report_name', 'Untitled')}): No operations specified"
        print(f"[ReportBuilder] {msg}")
        debug_lines.append(msg)
    else:
        try:
            final_df = _execute_pipeline(operations, file_paths)
            final_df.insert(0, "recommendation_rank", rec.get("rank", 0))
            final_df.insert(1, "recommendation_name", rec.get("report_name", "Untitled"))

            msg = (f"Executed pipeline for recommendation {rec_idx + 1} "
                   f"({len(operations)} operation(s))")
            print(f"[ReportBuilder] {msg}")
            debug_lines.append(msg)

        except Exception as e:
            msg = f"Error executing pipeline for recommendation {rec_idx + 1}: {e}"
            print(f"[ReportBuilder] {msg}")
            debug_lines.append(msg)
            final_df = pd.DataFrame()

    # ==================== TEMPORARY OUTPUT ====================
    print(f"\n{'='*80}")
    print(f"[TEMPORARY OUTPUT] Report Data (type {report_type}):")
    print(f"{'='*80}\n")
    print(final_df.head(15).to_string())
    print(f"\n{'='*80}")
    print(f"[TEMPORARY OUTPUT] Report shape: {final_df.shape} (rows, cols)")
    print(f"{'='*80}\n")

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


def _save_debug_output(
    session_id: Optional[str],
    report_type: str,
    debug_lines: List[str],
    final_df: pd.DataFrame
) -> None:
    """Write the report-generation trace to session_data/<session_id>/ for debugging."""
    if not session_id:
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
    file_paths: Optional[Dict[str, str]] = None
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

    for op_idx, operation in enumerate(operations):
        operation_type = (operation.get("operation_type") or "").lower()
        files_involved = operation.get("files_involved", [])

        if operation_type == "join":
            df = _execute_join(operation, file_paths, loaded_files, df, merged_filenames)
            continue

        # Lazily load the primary file the first time a step needs data.
        if df is None:
            if not files_involved:
                raise ValueError("No files specified for operation")
            filename = files_involved[0]
            filepath = _find_file_path(filename, file_paths)
            if not filepath:
                raise ValueError(f"File not found: {filename}")
            df = _load_file(filepath)
            loaded_files[filename] = df
            merged_filenames.add(filename)

        if operation_type == "filter":
            df = _execute_filter(df, operation)
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

    return df


def _execute_groupby(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute groupby operation with aggregations.

    Aggregated columns are renamed to "{column}_{function}" (e.g. "Calories_mean"),
    since that's the naming convention the LLM already uses in sort_by,
    plotly_config, and expected_output_schema - without this, those references
    would silently fail to match the actual output columns.
    """
    groupby_cols = operation.get("groupby_columns", [])
    aggregations = operation.get("aggregations", {})
    files_involved = operation.get("files_involved", [])

    if not groupby_cols and not aggregations:
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
    aggregations = {_resolve_column(df, col, files_involved): func for col, func in aggregations.items()}

    # Filter to only valid columns
    valid_groupby = [col for col in groupby_cols if col in df.columns]
    if not valid_groupby:
        raise ValueError(f"No valid groupby columns found. Requested: {groupby_cols}, Available: {df.columns.tolist()}")

    # Build named aggregations (map new column name -> (source column, agg function))
    agg_named = {}
    for col, func in aggregations.items():
        if col in df.columns:
            agg_named[f"{col}_{func}"] = (col, func)

    if not agg_named:
        # No aggregations specified, just group and count
        result = df.groupby(valid_groupby, as_index=False).size().rename(columns={"size": "count"})
    else:
        result = df.groupby(valid_groupby, as_index=False).agg(**agg_named)

    return result


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

    match = re.match(r'^(>=|<=|==|!=|>|<)\s*(.+)$', condition)
    if match:
        op, raw_value = match.groups()
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

    # Fallback: treat the condition as a plain equality match
    return df[df[column].astype(str) == condition]


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
            try:
                df[new_name] = pd.cut(numeric_col, bins=bins, labels=labels)
            except (ValueError, TypeError) as e:
                print(f"[ReportBuilder] Could not bin column {source_column} with bins={bins}: {e}")
                return df
        else:
            try:
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


def _normalize_join_keys(join_keys: List[Any]) -> List[tuple]:
    """Normalize join_keys entries to (left_column, right_column) pairs.

    Supports plain strings (same column name on both sides, e.g. "customer_id")
    and {"left": ..., "right": ...} dicts for foreign/primary keys named
    differently on each side (e.g. left="theme_id", right="id").
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


def _execute_join(
    operation: Dict[str, Any],
    file_paths: Optional[Dict[str, str]],
    loaded_files: Dict[str, pd.DataFrame],
    current_df: Optional[pd.DataFrame],
    merged_filenames: set
) -> pd.DataFrame:
    """Execute a join across two or more files (or the current pipeline result).

    Files already merged into current_df (tracked via merged_filenames) are
    skipped rather than re-loaded and re-merged - re-merging an already-joined
    file on a later shared key (e.g. a second join step reusing a file from an
    earlier step) would fan out rows instead of narrowing them.
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
            filepath = _find_file_path(filename, file_paths)
            if not filepath:
                raise ValueError(f"File not found: {filename}")
            loaded = _load_file(filepath)
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
        result = result.merge(right, left_on=left_on, right_on=right_on, how=join_type,
                               suffixes=("", right_suffix))

    return result


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


def _load_file(filepath: str) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame, normalizing placeholder nulls."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
        df = pd.read_excel(filepath)
    else:
        df = _read_csv_with_encoding(filepath)

    # Strip stray whitespace from column names (e.g. " Protein (g) ") so they
    # match the (also-stripped) names shown to the LLM during profiling -
    # otherwise a recommendation referencing "Protein (g)" silently fails to
    # match the real column and that report/chart can't be built at all.
    df.columns = df.columns.str.strip()

    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = _normalize_nulls(df[col])

    return _coerce_numeric_columns(df)


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
