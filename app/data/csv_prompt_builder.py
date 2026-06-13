import pandas as pd
import json
import os


def profile_df(filename, df, sample_rows=5):
    """
    Profiles a single dataframe and returns a summary dictionary.

    Args:
        filename (str): The name of the file associated with the dataframe.
        df (pd.DataFrame): The dataframe to profile.
        sample_rows (int): Number of sample rows to include in the profile.

    Returns:
        tuple: (profile dict, list of column names)
    """
    profile = {
        "filename": filename,
        "row_count": len(df),
        "columns": []
    }

    for col in df.columns:
        col_info = {
            "name": col,
            "dtype": str(df[col].dtype),
            "null_count": int(df[col].isnull().sum()),
            "null_pct": round(df[col].isnull().mean() * 100, 1),
        }

        if pd.api.types.is_numeric_dtype(df[col]):
            col_info["min"] = df[col].min()
            col_info["max"] = df[col].max()
            col_info["mean"] = round(df[col].mean(), 2)

        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            col_info["date_min"] = str(df[col].min())
            col_info["date_max"] = str(df[col].max())

        else:
            unique_vals = df[col].nunique()
            col_info["unique_count"] = unique_vals
            if unique_vals <= 20:
                col_info["unique_values"] = df[col].dropna().unique().tolist()

        profile["columns"].append(col_info)

    profile["sample_rows"] = df.head(sample_rows).astype(str).to_dict(orient="records")

    return profile, df.columns.tolist()


def find_shared_columns(all_profiles):
    """
    Identifies column names that appear in more than one file.

    Args:
        all_profiles (list): List of profile dicts from profile_df.

    Returns:
        dict: { column_name: [filenames that contain it] }
    """
    col_map = {}
    for p in all_profiles:
        for col in [c["name"] for c in p["columns"]]:
            col_map.setdefault(col, []).append(p["filename"])
    return {col: files for col, files in col_map.items() if len(files) > 1}


def build_prompt(data_loader, report_goal=""):
    """
    Builds an AI prompt from a DataLoader instance.

    Args:
        data_loader (DataLoader): A DataLoader instance via get_files(),
                                  returning a list of (file_path, df) tuples.
        report_goal (str): Optional description of what the report should answer.

    Returns:
        str: A formatted prompt string ready to send to an AI API.
    """
    all_profiles = []

    for file_path, df in data_loader.files:
        filename = os.path.basename(file_path)
        profile, _ = profile_df(filename, df)
        all_profiles.append(profile)

    shared = find_shared_columns(all_profiles)
    # TODO: Update prompt
    prompt = "You are a data analyst. I have the following CSV files I want to build a report from.\n\n"

    if report_goal:
        prompt += f"Report Goal: {report_goal}\n\n"

    for p in all_profiles:
        prompt += f"### File: {p['filename']} ({p['row_count']} rows)\n"
        prompt += "Columns:\n"
        for col in p["columns"]:
            line = f"  - {col['name']} ({col['dtype']})"
            if "min" in col:
                line += f" | min={col['min']}, max={col['max']}, mean={col['mean']}"
            if "date_min" in col:
                line += f" | range: {col['date_min']} to {col['date_max']}"
            if "unique_values" in col:
                line += f" | values: {col['unique_values']}"
            elif "unique_count" in col:
                line += f" | {col['unique_count']} unique values"
            if col["null_pct"] > 0:
                line += f" | {col['null_pct']}% null"
            prompt += line + "\n"

        prompt += f"\nSample rows:\n{json.dumps(p['sample_rows'], indent=2)}\n\n"

    if shared:
        prompt += "### Shared columns across files (potential join keys):\n"
        for col, files in shared.items():
            prompt += f"  - '{col}' appears in: {', '.join(files)}\n"
        prompt += "\n"
    # TODO: Update prompt
    prompt += (
        "Based on this data, suggest at least 3 meaningful ways to build a report. "
        "For each suggestion, describe: what question it answers, which files/columns to use, "
        "and what visualization or format would work best."
    )

    return prompt
