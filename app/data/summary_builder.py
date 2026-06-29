import pandas as pd
from data_profiling import ProfileReport
import json
from typing import List, Dict
from dataclasses import dataclass, asdict
import warnings
import os

# Suppress the deprecation warning
warnings.filterwarnings('ignore', category=DeprecationWarning)

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

@dataclass
class FileProfile:
    filename: str
    row_count: int
    columns: List[ColumnProfile]
    quality_flags: List[str]
    sample_rows: List[Dict]

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
          
    def profile_df_with_ydata(self, filename, df) -> FileProfile:
        """Profile a single file."""
        # Generate ydata profile
        profile = ProfileReport(df, minimal=True)  # minimal=True for speed
        profile_dict = json.loads(profile.to_json())
        
        # Extract column profiles
        columns_data = profile_dict.get('variables', {})
        col_profiles = []
        
        for col_name, col_stats in columns_data.items():
            dtype = str(df[col_name].dtype)
            unique_count = col_stats.get('n_unique', df[col_name].nunique())
            null_count = col_stats.get('n_missing', df[col_name].isnull().sum())
            null_percent = (null_count / len(df)) * 100
            
            # Detect role
            role = detect_column_role(col_name, dtype, unique_count, len(df))
            
            # Extract numeric stats if available
            min_val = col_stats.get('min')
            max_val = col_stats.get('max')
            mean_val = col_stats.get('mean')
            
            col_profiles.append(ColumnProfile(
                name=col_name,
                dtype=dtype,
                unique_values=unique_count,
                null_count=null_count,
                null_percent=round(null_percent, 2),
                role=role,
                min_value=str(min_val) if min_val else None,
                max_value=str(max_val) if max_val else None,
                mean_value=mean_val
            ))
        
        # Sample rows
        sample_rows = df.head(3).to_dict('records')
        
        # Quality flags
        quality_flags = detect_quality_flags(df)
        
        return FileProfile(
            filename=filename,
            row_count=len(df),
            columns=col_profiles,
            quality_flags=quality_flags,
            sample_rows=sample_rows
        )
    
    def detect_relationships(self, profiles: List[FileProfile]) -> List[Dict]:
        """Detect joins across files."""
        # Cross-file analysis
        pass

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
