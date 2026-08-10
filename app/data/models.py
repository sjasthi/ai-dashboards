from pydantic import BaseModel, Field, field_validator
from typing import List, Literal, Optional

from .recommendation_requester import RecommendationRequester

_REPORT_PATTERNS = tuple(RecommendationRequester.REPORT_PATTERNS.keys())

class FilterCondition(BaseModel):
    column: str
    condition: str  # e.g. "not_null", "== value", "> 10"

class DeriveColumn(BaseModel):
    new_name: str
    source_column: str
    method: str  # "compute", "regex_extract", "bin", "quantile"
    pattern: Optional[str] = None
    bins: List[float] = []
    bin_labels: List[str] = []
    quantiles: List[float] = []
    # For method="compute": arithmetic on source_column (left operand) against
    # right_column or right_value. Closed enum so the response stays expressible
    # as a JSON Schema for provider-side structured decoding.
    operator: Optional[Literal["+", "-", "*", "/"]] = None
    right_column: Optional[str] = None
    right_value: Optional[float] = None

class JoinKeyPair(BaseModel):
    left: str
    right: str

class Aggregation(BaseModel):
    # "Aggregate this column with this function." Closed enum (not an open-ended
    # dict) so the response is expressible as a JSON Schema; func matches
    # chart_builder.AGG_SUFFIXES so "{column}_{func}" outputs humanize correctly.
    column: str
    func: Literal["sum", "mean", "count", "min", "max", "median", "std", "nunique"]

class Operation(BaseModel):
    operation_type: Literal["filter", "derive", "groupby", "sort_limit", "join"]
    files_involved: List[str]  # which CSV/Excel files
    filter_conditions: List[FilterCondition] = []
    derive_column: Optional[DeriveColumn] = None
    groupby_columns: List[str] = []
    aggregations: List[Aggregation] = []  # [{"column": "revenue", "func": "sum"}]
    sort_by: Optional[str] = None
    ascending: bool = True
    limit: Optional[int] = None
    join_keys: List[JoinKeyPair] = []  # [{"left": "customer_id", "right": "id"}]
    join_type: Optional[str] = None

    @field_validator("aggregations", mode="before")
    @classmethod
    def _accept_legacy_aggregations(cls, v):
        # Old responses/saved sessions used a {"column": "func"} dict. Accept and
        # normalize it to the list form so replaying them still validates.
        if isinstance(v, dict):
            return [{"column": col, "func": func} for col, func in v.items()]
        return v

    @field_validator("join_keys", mode="before")
    @classmethod
    def _accept_legacy_join_keys(cls, v):
        # Old responses used bare strings for same-named keys (e.g. ["customer_id"]).
        # Expand each to the {"left","right"} form so they still validate.
        if isinstance(v, list):
            return [{"left": k, "right": k} if isinstance(k, str) else k for k in v]
        return v

class OutputColumn(BaseModel):
    name: str
    type: str

class PlotlyConfig(BaseModel):
    chart_type: str  # "bar", "line", "scatter", "pie", "histogram", "box"
    x_axis: str  # column name
    y_axis: Optional[str] = None  # omitted for a raw histogram (see chart_builder.py) - Plotly bins the raw x values itself
    title: str
    secondary_y: Optional[str] = None
    x_axis_label: Optional[str] = None  # human-readable override for x_axis's column name; see chart_builder.py for validation
    y_axis_label: Optional[str] = None  # human-readable override for y_axis's column name; see chart_builder.py for validation

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
    # Plain-English narrative of what the dataset represents. Optional so a
    # response that omits it still validates (mirrors data_quality_warning);
    # model_dump(exclude_none=True) drops it from the output when absent.
    dataset_overview: Optional[str] = None
    # Exactly 3 report suggestions are expected on the Analysis page. Enforcing it
    # here (rather than min_length=1) means a response with too few recommendations
    # fails validation and is retried with correction feedback, instead of silently
    # rendering only 1-2 cards - see recommendation_requester Rule 7 / Step 5.
    recommendations: List[ReportRecommendation] = Field(min_length=3, max_length=3)
