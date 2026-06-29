from pydantic import BaseModel
from typing import List

class Operation(BaseModel):
    operation_type: str  # "groupby", "filter", "join", "sort"
    files_involved: List[str]  # which CSV files
    groupby_columns: List[str] = []
    aggregations: dict = {}  # {"column": "sum", "other_col": "mean"}
    filter_conditions: dict = {}  # optional

class PlotlyConfig(BaseModel):
    chart_type: str  # "bar", "line", "scatter", "pie", "box", etc.
    x_axis: str  # column name
    y_axis: str  # column name
    title: str
    secondary_y: str = None  # optional

class ReportRecommendation(BaseModel):
    rank: int
    report_name: str
    question_answered: str
    required_operations: List[Operation]
    plotly_config: PlotlyConfig
    data_preparation_notes: str = ""

class RecommendationsResponse(BaseModel):
    recommendations: List[ReportRecommendation]