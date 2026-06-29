import json
from typing import List
from data.summary_builder import FileProfile
# from pathlib import Path

class RecommendationRequester:
    """Handles request for report recommendations from LLM."""
    
    def __init__(self):
        self.response_schema = self._get_schema()
    
    def _get_schema(self) -> dict:
        """Define the exact JSON structure LLM should return."""
        return {
            "type": "object",
            "properties": {
                "recommendations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rank": {"type": "integer"},
                            "report_name": {"type": "string"},
                            "question_answered": {"type": "string"},
                            "required_operations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "operation_type": {"type": "string"},
                                        "files_involved": {"type": "array", "items": {"type": "string"}},
                                        "groupby_columns": {"type": "array", "items": {"type": "string"}},
                                        "aggregations": {"type": "object"}
                                    }
                                }
                            },
                            "plotly_config": {
                                "type": "object",
                                "properties": {
                                    "chart_type": {"type": "string"},
                                    "x_axis": {"type": "string"},
                                    "y_axis": {"type": "string"},
                                    "title": {"type": "string"}
                                }
                            }
                        }
                    }
                }
            }
        }
    
    def build_request_prompt(self, file_profiles: List[FileProfile]) -> str:
        """Build LLM prompt with explicit JSON schema instructions."""
        
        # Serialize summaries to JSON
        summaries_json = json.dumps(
            [self._profile_to_dict(p) for p in file_profiles],
            indent=2,
            default=str
        )
        
        prompt = f"""You are a data analyst expert. I have the following CSV files and their profiles:

{summaries_json}

Your task: Suggest 3 meaningful reports from this data.

IMPORTANT: You MUST return a valid JSON object (not markdown, not prose) with this exact structure:

{{
  "recommendations": [
    {{
      "rank": 1,
      "report_name": "Report Name Here",
      "question_answered": "What business question does this answer?",
      "required_operations": [
        {{
          "operation_type": "groupby",
          "files_involved": ["file1.csv"],
          "groupby_columns": ["column_name"],
          "aggregations": {{"revenue": "sum", "count": "count"}}
        }}
      ],
      "plotly_config": {{
        "chart_type": "bar",
        "x_axis": "column_name",
        "y_axis": "aggregated_value",
        "title": "Chart Title"
      }},
      "data_preparation_notes": "[Y/N] State if any special preparation is needed"
    }}
  ]
}}

For each recommendation:
1. Specify which CSV files to use
2. List the pandas/numpy operations needed
3. Define the plotly visualization (chart_type, axes, title)
4. Rank by relevance/insight value

Return ONLY valid JSON. No markdown formatting, no code blocks, no explanations outside the JSON.
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
                    "min": col.min_value,
                    "max": col.max_value,
                    "mean": col.mean_value
                }
                for col in profile.columns
            ],
            "quality_flags": profile.quality_flags
        }
    
    # def save_prompt(self, prompt: str, filename: str = "request.txt", dir: str = "session_data/logs") -> None:
    #     """Save prompt to log file for debugging/inspection."""
    #     log_dir = Path(dir)
    #     log_dir.mkdir(parents=True, exist_ok=True)
    #     log_file = log_dir / filename
    #     with open(log_file, 'w') as f:
    #         f.write(prompt)
        
    #     print(f"✓ Prompt saved: {log_file}")