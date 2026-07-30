# Plan: LLM-Driven Report Recommendation Strategy

**TL;DR:** Enhance CSV profiling to extract dataset-specific insights (schema, statistics, relationships, quality), send structured metadata to Qwen2.5, and parse its JSON response into executable pandas/numpy operations. The LLM will recommend 1–3 report types (from a curated taxonomy) with specific metrics, visualizations, and data operations needed for report generation.

---

## Steps

### Phase 1: CSV Summary Strategy

1. **Extend the existing `profile_df()` to capture dataset-level insights:**
   - Current: row count, dtypes, column stats, nulls, sample rows ✅
   - **Add:**
     - **Temporal metadata** – detect date/time columns; date ranges (min/max); frequency/granularity (daily, monthly, yearly)
     - **Relationship hints** – identify common column patterns (`_id`, foreign key candidates); cardinality (1:1, 1:many); suggest join paths across loaded files
     - **Data quality flags** – categorical entropy (diversity), numeric outlier counts (via IQR), suspicious patterns (all nulls, constant values)
     - **Domain hints** – infer dataset type (relational, time-series, denormalized, nutrition data) from structure and column names

2. **Create a `DatasetProfile` object** that aggregates all profiles + detected relationships:
   - Store individual file profiles (from `profile_df()`)
   - Add cross-file analysis: detected joins, data lineage, compatibility matrix
   - Example: LEGO dataset → "8 relational files, 5+ join paths; Starbucks → "3 files, single analysis table pattern"

### Phase 2: Summary Format (Prompt to LLM)

3. **Design a structured JSON summary** to send to Qwen2.5:
   ```json
   {
     "summary_metadata": {
       "dataset_count": 3,
       "dataset_type": ["relational", "time_series", "tabular"],
       "total_rows": 50000,
       "temporal_range": "2020-2026"
     },
     "files": [
       {
         "filename": "sets.csv",
         "row_count": 500,
         "columns": [
           {
             "name": "set_num",
             "dtype": "object",
             "unique_values": 500,
             "role": "primary_key"
           },
           {
             "name": "year",
             "dtype": "int64",
             "min": 1990, "max": 2024,
             "role": "temporal"
           }
         ],
         "sample_rows": [...],
         "quality_flags": ["no_nulls", "complete_temporal_coverage"]
       }
     ],
     "relationships": [
       {
         "from_file": "sets.csv",
         "to_file": "themes.csv",
         "join_key": "theme_id",
         "cardinality": "many_to_one"
       }
     ],
     "domain_insights": "LEGO product catalog with inventory and sales data spanning 30+ years"
   }
   ```

### Phase 3: LLM Response Template (What to Ask For)

4. **Define a JSON schema for LLM recommendations:**
   ```json
   {
     "recommendations": [
       {
         "rank": 1,
         "report_name": "Annual Revenue Trend Analysis",
         "report_type": "time_series_trend",
         "description": "Year-over-year revenue and unit sales growth",
         "required_operations": [
           {
             "operation": "groupby",
             "groupby_columns": ["year"],
             "aggregations": {"revenue": "sum", "units": "count"}
           },
           {
             "operation": "join",
             "join_files": ["sets.csv", "inventories.csv"],
             "join_key": "set_num"
           }
         ],
         "metrics": ["total_revenue", "avg_price_per_set", "yoy_growth_rate"],
         "visualization_type": "line_chart",
         "visualization_specs": {
           "x_axis": "year",
           "y_axis": "revenue",
           "secondary_y": "unit_count"
         }
       }
     ]
   }
   ```

5. **Create prompt template that requests structured JSON:**
   - Include instructions: "Return a JSON object (NOT markdown, NOT prose) with structure: `{"recommendations": [...]}`"
   - Specify constraints: "Maximum 3 recommendations, ranked by relevance"
   - Add validation hints: "For each operation, include all required parameters"

### Phase 4: Report Type Taxonomy

6. **Define available report types** (what LLM should recommend):
   - **Statistical Summary** – descriptive stats, distributions, quartiles by group
   - **Time-Series Trend** – aggregations over time, growth rates, seasonality
   - **Comparative Analysis** – side-by-side comparisons across categories/groups, ratio analysis
   - **Segmentation Report** – cohort analysis, clustering, group-level summaries
   - **Data Quality Report** – null patterns, outliers, completeness metrics, anomaly flags

7. **Create a registry of report templates** (map report type → standard pandas/numpy operations):
   - Each template specifies: required groupby columns, aggregation functions, join logic, visualization defaults
   - Example: `TimeSeriesTrend` template → `groupby([date_col]) → resample() → agg(['sum', 'mean'])`

### Phase 5: Implementation Integration

8. **Enhance `build_prompt()` to:**
   - Call extended `profile_df()` for each file
   - Build `DatasetProfile` with relationships
   - Serialize to JSON summary
   - Inject system prompt: "You are a data analyst recommending report types. Return a JSON object..."
   - Inject JSON schema as inline guidance (not external, to avoid token waste)

9. **Create `parse_llm_response()` function:**
   - Validate JSON structure against schema
   - Verify operations reference existing files/columns
   - Transform LLM operations into executable pandas methods
   - Error handling: fallback to defaults if LLM returns invalid JSON

10. **Add `get_report_recommendations()` function** (fill the existing TODO):
    - Orchestrates: profile → prompt → LLM call → parse → return structured recommendations
    - Returns list of `Report` objects (dataclass) with name, type, operations, viz specs

---

## Relevant Files

- `app/data/prompt_builder.py` – Enhance `profile_df()` + add `DatasetProfile`, new `build_prompt()` params, `parse_llm_response()`
- `app/data/AI_Engine.py` – Already has `send_prompt()`; validate it enforces JSON-only responses
- `app/data/data_loader.py` – Consider adding metadata cache (relationships) after first load
- `app/data_main.py` – Implement `get_report_recommendations()` using the pipeline

---

## Verification

1. **Unit test `profile_df()` on each dataset:**
   - Verify temporal columns detected (e.g., `year` in LEGO, none in Starbucks)
   - Verify relationships detected (e.g., LEGO: 5+ joins; School: 2 joins; Starbucks: 0)
   - Verify quality flags populated correctly

2. **Manual test LLM prompt + response:**
   - Run `build_prompt()` → copy output to manual Ollama/Qwen2.5 call
   - Verify LLM returns valid JSON (not markdown/prose)
   - Verify response validates against schema

3. **Integration test `parse_llm_response()`:**
   - Feed sample LLM responses (both valid + edge cases)
   - Verify operations map to actual file/column names
   - Verify pandas/numpy translation is correct

4. **End-to-end pipeline test:**
   - Load each dataset → get recommendations → verify 1-3 returned, ranked, actionable

---

## Decisions

- **Summary format: Structured JSON** – enables LLM to understand schema, cardinality, and temporal structure without prose interpretation
- **LLM response: Strict JSON (no markdown/prose)** – simplifies parsing and enables deterministic downstream execution
- **Report types: Start with 5 common types** (Statistical, Time-Series, Comparative, Segmentation, Data Quality) – extensible registry for future additions
- **Token efficiency:** Compress sample rows (3-5 per file), include only essential stats; use column roles (`primary_key`, `temporal`, `measure`) instead of long descriptions
- **No actual report generation in Phase 1** – focus on *recommendations* only; report building (pandas/numpy code generation) is a separate downstream phase

---

## Report Types Available (Starting Set)

| Type | Use Cases | Datasets | Future? |
|------|-----------|----------|---------|
| **Statistical Summary** | Distribution, quartiles, correlation by group | All (Starbucks ideal) | ✅ |
| **Time-Series Trend** | Revenue/growth over time, seasonality | LEGO, School enrollment | ✅ |
| **Comparative Analysis** | A/B comparison, ratios, category breakdowns | All | ✅ |
| **Segmentation** | Cohort analysis, clustering, customer groups | LEGO (by theme), School (by grade) | ✅ |
| **Data Quality** | Nulls, outliers, anomalies, completeness | All (diagnostic focus) | ✅ |

### Potential Future Additions:
- **Predictive Model Recommendation** – detect forecasting opportunities (e.g., LEGO trends → demand forecast)
- **Anomaly Detection Report** – identify unusual patterns, outlier spikes, data drift
- **Network/Relationship Analysis** – visualize entity relationships (e.g., LEGO part categories → theme connections)
- **Feature Engineering** – recommend derived columns (e.g., price_per_piece, set_age, complexity scores)

---

## Next Steps for Implementation

- **Before coding:** Confirm this summary strategy and LLM response schema align with your pandas/numpy report generation approach
- **Phase 1 tasks:** Extended `profile_df()` + `DatasetProfile` class + JSON serialization
- **Phase 2 tasks:** Prompt template refinement + LLM integration test
- **Phase 3 tasks:** Response parser + report registry + `get_report_recommendations()`
