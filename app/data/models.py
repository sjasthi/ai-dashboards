from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional, Union

from .recommendation_requester import RecommendationRequester

_REPORT_PATTERNS = tuple(RecommendationRequester.REPORT_PATTERNS.keys())

class FilterCondition(BaseModel):
    column: str
    condition: str  # e.g. "not_null", "== value", "> 10"

class DeriveColumn(BaseModel):
    new_name: str
    source_column: str
    method: str  # "regex_extract", "bin", "quantile"
    pattern: Optional[str] = None
    bins: List[float] = []
    bin_labels: List[str] = []
    quantiles: List[float] = []

class JoinKeyPair(BaseModel):
    left: str
    right: str

class Operation(BaseModel):
    operation_type: Literal["filter", "derive", "groupby", "sort_limit", "join"]
    files_involved: List[str]  # which CSV/Excel files
    filter_conditions: List[FilterCondition] = []
    derive_column: Optional[DeriveColumn] = None
    groupby_columns: List[str] = []
    aggregations: dict = {}  # {"column": "sum", "other_col": "mean"}
    sort_by: Optional[str] = None
    ascending: bool = True
    limit: Optional[int] = None
    join_keys: List[Union[str, JoinKeyPair]] = []
    join_type: Optional[str] = None

class OutputColumn(BaseModel):
    name: str
    type: str

class PlotlyConfig(BaseModel):
    chart_type: str  # "bar", "line", "scatter", "pie", "box", etc.
    x_axis: str  # column name
    y_axis: Optional[str] = None  # omitted for a raw histogram (see chart_builder.py) - Plotly bins the raw x values itself
    title: str
    secondary_y: Optional[str] = None

class Justification(BaseModel):
    column: str = ""
    profile_evidence: str = ""

class ReportRecommendation(BaseModel):
    rank: int
    report_name: str
    question_answered: str
    pattern_used: str  # validated against RecommendationRequester.REPORT_PATTERNS below
    justification: Justification
    required_operations: List[Operation]
    expected_output_schema: List[OutputColumn] = []
    plotly_config: PlotlyConfig
    rationale_bullets: List[str] = []
    data_quality_warning: Optional[str] = None

    @field_validator("pattern_used")
    @classmethod
    def pattern_used_must_be_known(cls, v: str) -> str:
        if v not in _REPORT_PATTERNS:
            raise ValueError(f"pattern_used must be one of {_REPORT_PATTERNS}, got {v!r}")
        return v

class RecommendationsResponse(BaseModel):
    recommendations: List[ReportRecommendation] = Field(min_length=1, max_length=4)
