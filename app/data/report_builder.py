"""
Builds structured reports from AI recommendations using pandas/numpy.

Executes the required operations (groupby, aggregations) on actual data files
and returns the resulting DataFrame.
"""

import pandas as pd
import numpy as np
import os
from typing import Dict, Any, List, Optional


def generate_report(
    recommendations: Dict[str, Any],
    report_type: str = "A",
    file_paths: Optional[Dict[str, str]] = None
) -> pd.DataFrame:
    """
    Generate a structured report by executing AI-recommended operations on real data.
    
    Args:
        recommendations: Cleaned JSON response from AI_Engine with required_operations
        report_type: Type of report ('A', 'B', 'C', etc.) - determines presentation style
        file_paths: Dict mapping filename -> full filepath (e.g., {"starbucks.csv": "/path/to/file"})
    
    Returns:
        pandas DataFrame containing the actual report data (all types use same data, different presentation)
    """
    print(f"\n[ReportBuilder] Generating report type: {report_type}")
    print(f"[ReportBuilder] Recommendations keys: {recommendations.keys()}")
    
    # Extract recommendations list
    recs = recommendations.get("recommendations", [])
    
    if not recs:
        print("[ReportBuilder] WARNING: No recommendations found in response")
        return pd.DataFrame()
    
    # Execute operations to get actual data
    all_report_data = []
    
    for rec_idx, rec in enumerate(recs):
        operations = rec.get("required_operations", [])
        
        if not operations:
            print(f"[ReportBuilder] Recommendation {rec_idx + 1}: No operations specified")
            continue
        
        # Execute each operation
        for op_idx, operation in enumerate(operations):
            try:
                result_df = _execute_operation(operation, file_paths)
                
                # Add metadata columns
                result_df.insert(0, "recommendation_rank", rec.get("rank", 0))
                result_df.insert(1, "recommendation_name", rec.get("report_name", "Untitled"))
                result_df.insert(2, "operation_index", op_idx + 1)
                
                all_report_data.append(result_df)
                print(f"[ReportBuilder] ✓ Executed operation {op_idx + 1} for recommendation {rec_idx + 1}")
                
            except Exception as e:
                print(f"[ReportBuilder] ✗ Error executing operation {op_idx + 1}: {e}")
                continue
    
    # Combine all results
    if all_report_data:
        final_df = pd.concat(all_report_data, ignore_index=True)
    else:
        print("[ReportBuilder] WARNING: No operations executed successfully")
        final_df = pd.DataFrame()
    
    # ==================== TEMPORARY OUTPUT ====================
    print(f"\n{'='*80}")
    print(f"[TEMPORARY OUTPUT] Report Data (type {report_type}):")
    print(f"{'='*80}\n")
    print(final_df.head(15).to_string())
    print(f"\n{'='*80}")
    print(f"[TEMPORARY OUTPUT] Report shape: {final_df.shape} (rows, cols)")
    print(f"{'='*80}\n")
    
    return final_df


def _execute_operation(operation: Dict[str, Any], file_paths: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """
    Execute a single operation (groupby, filter, etc.) on actual data.
    
    Args:
        operation: Operation spec from required_operations
        file_paths: Dict mapping filename -> filepath
    
    Returns:
        DataFrame with operation results
    
    Raises:
        ValueError: If operation cannot be executed
    """
    operation_type = operation.get("operation_type", "").lower()
    files_involved = operation.get("files_involved", [])
    
    if not files_involved:
        raise ValueError("No files specified for operation")
    
    # Load data from first file (can extend for joins later)
    filename = files_involved[0]
    
    # Find file path
    filepath = _find_file_path(filename, file_paths)
    if not filepath:
        raise ValueError(f"File not found: {filename}")
    
    # Load data
    df = _load_file(filepath)
    
    # Execute operation based on type
    if operation_type == "groupby":
        return _execute_groupby(df, operation)
    elif operation_type == "filter":
        return _execute_filter(df, operation)
    elif operation_type == "sort":
        return _execute_sort(df, operation)
    else:
        # Default: return data as-is
        print(f"[ReportBuilder] Unknown operation type: {operation_type}, returning raw data")
        return df


def _execute_groupby(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute groupby operation with aggregations."""
    groupby_cols = operation.get("groupby_columns", [])
    aggregations = operation.get("aggregations", {})
    
    if not groupby_cols:
        raise ValueError("No groupby columns specified")
    
    # Filter to only valid columns
    valid_groupby = [col for col in groupby_cols if col in df.columns]
    if not valid_groupby:
        raise ValueError(f"No valid groupby columns found. Requested: {groupby_cols}, Available: {df.columns.tolist()}")
    
    # Build aggregation dict (map column names to agg functions)
    agg_dict = {}
    for col, func in aggregations.items():
        if col in df.columns:
            agg_dict[col] = func
    
    if not agg_dict:
        # No aggregations specified, just group and count
        result = df.groupby(valid_groupby, as_index=False).size().rename(columns={"size": "count"})
    else:
        result = df.groupby(valid_groupby, as_index=False).agg(agg_dict)
    
    return result


def _execute_filter(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute filter operation (placeholder - extend as needed)."""
    filters = operation.get("filters", {})
    result = df.copy()
    
    # Apply each filter
    for col, condition in filters.items():
        if col in result.columns:
            # Simple equality filter for now
            result = result[result[col] == condition]
    
    return result


def _execute_sort(df: pd.DataFrame, operation: Dict[str, Any]) -> pd.DataFrame:
    """Execute sort operation."""
    sort_by = operation.get("sort_columns", [])
    ascending = operation.get("ascending", True)
    
    valid_sort = [col for col in sort_by if col in df.columns]
    if valid_sort:
        return df.sort_values(valid_sort, ascending=ascending).reset_index(drop=True)
    return df


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


def _load_file(filepath: str) -> pd.DataFrame:
    """Load a CSV or Excel file into a DataFrame."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    
    if filepath.endswith('.xlsx') or filepath.endswith('.xls'):
        return pd.read_excel(filepath)
    else:
        # Assume CSV
        return pd.read_csv(filepath)