import pandas as pd
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
import os

@dataclass
class ColumnProfile:
    name: str
    dtype: str
    unique_values: int
    null_count: int
    null_percent: float
    role: str  # "primary_key", "temporal", "measure", "categorical"
    min_value: str = None
    max_value: str = None
    mean_value: float = None
    top_values: List[Dict] = None # Most common values for categorical columns

@dataclass
class FileProfile:
    filename: str
    row_count: int
    columns: List[ColumnProfile]
    quality_flags: List[str]
    sample_rows: List[Dict]


def _primary_key_like_columns(profile: "FileProfile") -> List[ColumnProfile]:
    """Columns that look like a table's primary key: named "id", flagged as
    primary_key by profiling, or near-unique per row (id-like cardinality)."""
    candidates = []
    for col in profile.columns:
        name_lower = col.name.strip().lower()
        is_named_id = name_lower == "id"
        is_high_cardinality = (
            profile.row_count > 0 and col.unique_values / profile.row_count > 0.95
        )
        if is_named_id or col.role == "primary_key" or is_high_cardinality:
            candidates.append(col)
    return candidates


def _is_key_like_column(col: ColumnProfile) -> bool:
    """Is this column plausibly a join key, as opposed to a generic measure or
    label column? A bare "id" is deliberately excluded here - it's virtually
    always a table's own surrogate primary key, not evidence that THIS file
    relates to some other file whose column also happens to be named "id"
    (every table has one). Genuine same-named-key relationships (e.g. both
    files having "customer_id") and FK->PK relationships (see
    _fk_entity_stem/_fk_target_matches) are unaffected by this exclusion."""
    name = col.name.strip().lower()
    if name == "id":
        return False
    return col.role == "primary_key" or name.endswith(("_id", "_key", "_num"))


def _fk_entity_stem(col_name: str) -> Optional[str]:
    """Extract the entity name from a foreign-key-style column, e.g. "theme_id" -> "theme"."""
    name = col_name.strip().lower()
    for suffix in ("_id", "_key"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def _fk_target_matches(entity: str, file_stem: str) -> bool:
    """Does a foreign-key entity name (e.g. "theme") plausibly refer to a target
    file (e.g. "themes")? Small surrogate-int ID columns (theme_id, color_id,
    part_cat_id, ...) commonly have overlapping value ranges purely by chance,
    so value overlap alone isn't a reliable enough signal - this requires the FK
    name to actually resemble the target file name too (tolerating simple
    pluralization via a shared-prefix check rather than exact match)."""
    if not entity or not file_stem:
        return False
    if entity == file_stem:
        return True
    common = os.path.commonprefix([entity, file_stem])
    return len(common) >= max(4, int(0.75 * len(entity)))


class SummaryGenerator:
    """Handles summary generation + optional persistence."""
    
    def profile_all_files(self, data_loader) -> List[FileProfile]:
        """Generate profiles for all files."""
        profiles = []
        for file_path, df in data_loader.files:
            filename = os.path.basename(file_path)
            profile = self.profile_df_with_ydata(filename, df)
            profiles.append(profile)
        return profiles

    def detect_relationships(self, profiles: List[FileProfile], data_loader=None) -> List[Dict]:
        """
        Detect likely join keys across files so multi-file reports (e.g. joining
        transactions to customers on customer_id) can be recommended instead of
        every file being analyzed in isolation.

        Two passes:
        1. Identical column names (case/whitespace insensitive) across every pair
           of files - e.g. both files have "customer_id".
        2. Foreign-key style matches where the names differ - e.g. "theme_id" in
           one file referencing the primary key "id" in another. This is the
           normalized-schema pattern (child FK column named "<entity>_id", parent
           PK column just named "id") that pass 1 can never catch.

        If data_loader is provided, actual values are cross-checked for overlap
        so a match with no shared values (a coincidence, not a real key) is
        discarded.
        """
        dfs_by_filename = {}
        if data_loader is not None:
            for file_path, df in data_loader.files:
                dfs_by_filename[os.path.basename(file_path)] = df

        relationships = []
        matched_pairs = set()  # (file_a, col_a, file_b, col_b) already reported

        def value_overlap(file_a_name, col_a_name, file_b_name, col_b_name):
            df_a = dfs_by_filename.get(file_a_name)
            df_b = dfs_by_filename.get(file_b_name)
            if df_a is None or df_b is None:
                return None
            try:
                values_a = set(df_a[col_a_name].dropna().astype(str))
                values_b = set(df_b[col_b_name].dropna().astype(str))
                if values_a and values_b:
                    return len(values_a & values_b) / min(len(values_a), len(values_b))
            except Exception:
                return None
            return None

        # ---- Pass 1: identical column names ----
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                file_a, file_b = profiles[i], profiles[j]
                cols_a = {col.name.strip().lower(): col for col in file_a.columns}
                cols_b = {col.name.strip().lower(): col for col in file_b.columns}

                for col_key in set(cols_a) & set(cols_b):
                    col_a, col_b = cols_a[col_key], cols_b[col_key]

                    # Skip generic non-key columns (e.g. both files happening to
                    # have a "quantity" or "name" column) - a name match alone on
                    # a measure/label column is not evidence of a real relationship.
                    if not (_is_key_like_column(col_a) or _is_key_like_column(col_b)):
                        continue

                    overlap_ratio = value_overlap(file_a.filename, col_a.name, file_b.filename, col_b.name)

                    # A name match with verified near-zero overlap is almost
                    # certainly a coincidence (e.g. two unrelated "id" columns).
                    if overlap_ratio is not None and overlap_ratio < 0.05:
                        continue

                    is_key_like = col_a.role == "primary_key" or col_b.role == "primary_key"
                    if overlap_ratio is not None and overlap_ratio > 0.5:
                        confidence = "high"
                    elif is_key_like or overlap_ratio is not None:
                        confidence = "medium"
                    else:
                        confidence = "low"  # name matched but overlap unverified

                    relationships.append({
                        "file_a": file_a.filename,
                        "column_a": col_a.name,
                        "file_b": file_b.filename,
                        "column_b": col_b.name,
                        "value_overlap_ratio": round(overlap_ratio, 3) if overlap_ratio is not None else None,
                        "confidence": confidence
                    })
                    matched_pairs.add((file_a.filename, col_a.name.lower(), file_b.filename, col_b.name.lower()))

        # ---- Pass 2: foreign-key style matches with differing names ----
        # e.g. file_a."theme_id" -> file_b."id"
        for i in range(len(profiles)):
            for j in range(len(profiles)):
                if i == j:
                    continue
                file_a, file_b = profiles[i], profiles[j]
                pk_candidates_b = _primary_key_like_columns(file_b)

                file_b_stem = os.path.splitext(file_b.filename)[0].strip().lower()

                for col_a in file_a.columns:
                    name_a = col_a.name.strip().lower()
                    entity = _fk_entity_stem(col_a.name)
                    if entity is None:
                        continue

                    # Small surrogate-int ID columns (theme_id, color_id, ...)
                    # commonly have overlapping value ranges by pure chance, so
                    # also require the FK's name to actually resemble the target
                    # file's name (e.g. "theme" -> "themes.csv") before trusting
                    # value overlap as confirmation.
                    if not _fk_target_matches(entity, file_b_stem):
                        continue

                    for col_b in pk_candidates_b:
                        name_b = col_b.name.strip().lower()
                        if name_a == name_b:
                            continue  # already covered by pass 1

                        key = (file_a.filename, name_a, file_b.filename, name_b)
                        if key in matched_pairs:
                            continue

                        overlap_ratio = value_overlap(file_a.filename, col_a.name, file_b.filename, col_b.name)
                        if overlap_ratio is None or overlap_ratio < 0.5:
                            continue  # need strong value evidence since names differ

                        relationships.append({
                            "file_a": file_a.filename,
                            "column_a": col_a.name,
                            "file_b": file_b.filename,
                            "column_b": col_b.name,
                            "value_overlap_ratio": round(overlap_ratio, 3),
                            "confidence": "high" if overlap_ratio > 0.8 else "medium"
                        })
                        matched_pairs.add(key)

        return relationships
          
    def profile_df_with_ydata(self, filename, df) -> FileProfile:
        """Profile a single file."""
        col_profiles = []

        for col_name in df.columns:
            series = df[col_name]
            dtype = str(series.dtype)
            unique_count = int(series.nunique())
            null_count = int(series.isnull().sum())
            null_percent = (null_count / len(df)) * 100 if len(df) else 0.0

            # Detect role
            role = detect_column_role(col_name, dtype, unique_count, len(df))

            # Numeric stats if available
            min_val = max_val = mean_val = None
            if pd.api.types.is_numeric_dtype(series):
                non_null = series.dropna()
                if not non_null.empty:
                    min_val = non_null.min()
                    max_val = non_null.max()
                    mean_val = float(non_null.mean())

            # Top values give the LLM the column's actual semantics, which raw stats
            # can't convey. Only for genuinely low-cardinality categorical columns:
            # detect_column_role also labels leftover high-cardinality text (e.g. a
            # "name" column) "categorical", where a top-5 is noise, not signal.
            top_values = None
            if role == "categorical" and unique_count <= 30:
                counts = series.value_counts().head(5)
                top_values = [
                    {"value": str(value), "count": int(count)}
                    for value, count in counts.items()
                ]

            col_profiles.append(ColumnProfile(
                name=col_name,
                dtype=dtype,
                unique_values=unique_count,
                null_count=null_count,
                null_percent=round(null_percent, 2),
                role=role,
                min_value=str(min_val) if min_val is not None else None,
                max_value=str(max_val) if max_val is not None else None,
                mean_value=mean_val,
                top_values=top_values
            ))

        # Sample rows
        n_sample = min(5, len(df))
        if n_sample > 0:
            sample_indices = [round(i * (len(df) - 1) / max(n_sample - 1, 1)) for i in range(n_sample)]
            sample_indices = list(dict.fromkeys(sample_indices))
            sample_rows = df.iloc[sample_indices].to_dict('records')
        else:
            sample_rows = []

        # Quality flags
        quality_flags = detect_quality_flags(df)

        return FileProfile(
            filename=filename,
            row_count=len(df),
            columns=col_profiles,
            quality_flags=quality_flags,
            sample_rows=sample_rows
        )

def detect_column_role(col_name: str, dtype: str, unique_count: int, total_rows: int) -> str:
    """Infer column role from name and stats."""
    col_lower = col_name.lower()
    
    # Primary key detection
    if 'id' in col_lower or 'num' in col_lower:
        if unique_count == total_rows or unique_count > total_rows * 0.95:
            return "primary_key"
    
    # Temporal detection
    if any(term in col_lower for term in ['date', 'year', 'month', 'day', 'time']):
        return "temporal"
    
    # Categorical (low cardinality)
    if unique_count < 20 and dtype == 'object':
        return "categorical"
    
    # Measure (numeric)
    if dtype in ['int64', 'float64']:
        return "measure"
    
    return "categorical"

def detect_quality_flags(df: pd.DataFrame) -> List[str]:
    """Detect data quality issues."""
    flags = []
    
    if df.isnull().sum().sum() == 0:
        flags.append("no_nulls")
    
    if df.isnull().sum().sum() / (df.shape[0] * df.shape[1]) > 0.5:
        flags.append("high_missing_data")
    
    for col in df.select_dtypes(include=['int64', 'float64']).columns:
        Q1, Q3 = df[col].quantile([0.25, 0.75])
        IQR = Q3 - Q1
        outliers = df[(df[col] < Q1 - 1.5*IQR) | (df[col] > Q3 + 1.5*IQR)]
        if len(outliers) > 0.05 * len(df):  # >5% outliers
            flags.append(f"outliers_in_{col}")
    
    return flags
