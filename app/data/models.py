from pydantic import BaseModel
from typing import List, Optional

class FilterCondition(BaseModel):
    column: str
    condition: str  # e.g. "not_null", "== value", "> 10"

class DeriveColumn(BaseModel):
    new_name: str
    source_column: str
    method: str  # "regex_extract", "bin", etc.
    pattern: Optional[str] = None
    bins: List[float] = []
    bin_labels: List[str] = []

class Operation(BaseModel):
    operation_type: str  # "filter", "derive", "groupby", "sort_limit", "join"
    files_involved: List[str]  # which CSV/Excel files
    filter_conditions: List[FilterCondition] = []
    derive_column: Optional[DeriveColumn] = None
    groupby_columns: List[str] = []
    aggregations: dict = {}  # {"column": "sum", "other_col": "mean"}
    sort_by: Optional[str] = None
    ascending: bool = True
    limit: Optional[int] = None
    join_keys: List[str] = []
    join_type: Optional[str] = None

class OutputColumn(BaseModel):
    name: str
    type: str

class PlotlyConfig(BaseModel):
    chart_type: str  # "bar", "line", "scatter", "pie", "box", etc.
    x_axis: str  # column name
    y_axis: str  # column name
    title: str
    secondary_y: Optional[str] = None

class Justification(BaseModel):
    column: str = ""
    profile_evidence: str = ""

class ReportRecommendation(BaseModel):
    rank: int
    report_name: str
    question_answered: str
    pattern_used: str  # one of RecommendationRequester.REPORT_PATTERNS keys
    justification: Justification
    required_operations: List[Operation]
    expected_output_schema: List[OutputColumn] = []
    plotly_config: PlotlyConfig
    rationale_bullets: List[str] = []
    data_quality_warning: Optional[str] = None

class RecommendationsResponse(BaseModel):
    recommendations: List[ReportRecommendation]
