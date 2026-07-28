"""
FastAPI server for AI Dashboard backend.

Some endpoints call the real data pipeline; others return mock data.
See the per-endpoint [REAL] / [MOCK] / [REAL + MOCK FALLBACK] / [UNUSED]
tags on each endpoint below.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import json
import os
import sys
import tempfile
import shutil
from datetime import datetime
from typing import Optional

# Ensure the project root is on sys.path so data modules can be imported
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Try to import data pipeline modules at startup
try:
    from app.data.data_loader import DataLoader
    from app.data.summary_builder import SummaryGenerator
    from app.data.recommendation_requester import RecommendationRequester
    import app.data.AI_Engine as ai_engine
    from app.data.report_builder import (
        METADATA_COLUMNS, generate_report, report_type_to_index, resolve_plotly_axes,
    )
    from app.data.chart_builder import build_chart_figure
    from app.data.report_stats import build_report_stats
    DATA_MODULES_AVAILABLE = True
    print("[API] Data modules loaded successfully")
except ImportError as e:
    DATA_MODULES_AVAILABLE = False
    print(f"[API] Data modules not available: {e} — will use mock data")

app = FastAPI(
    title="AI Dashboard API",
    description="Backend API for AI-powered dashboard",
    version="0.1.0"
)

# Enable CORS so frontend (localhost:5173) can call backend (localhost:8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# REQUEST MODELS
# ============================================================================

class GenerateReportRequest(BaseModel):
    """Request body for generating a report."""
    session_id: str
    report_type: str = "A"

# ============================================================================
# SESSIONS
# ============================================================================

SESSIONS = {}  # Store uploaded file metadata by session_id


def generate_session_id():
    """Generate a unique session ID."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ============================================================================
# ANALYZE-FULL MOCK FALLBACK HELPERS
# ============================================================================

def _mock_analyze_full_fallback(file_paths):
    """[MOCK] Fallback values used when DATA_MODULES_AVAILABLE is False
    or the real pipeline raises during /api/analyze-full."""
    return {
        "file_profiles": [],
        "prompt": "Mock prompt (AI_Engine integration pending)",
        "recommendations": {
            "summary": "Mock analysis - integrate your AI_Engine code",
            "key_insights": [f"Processed {len(file_paths)} file(s)", "Files received and ready for analysis"],
            "recommendations": ["Connect your data pipeline to process files"],
        },
    }


# ============================================================================
# ENDPOINTS — ACTIVE (wired to current React frontend)
# ============================================================================

# ----------------------------------------------------------------------------
# [REAL]
# ----------------------------------------------------------------------------
@app.get("/health")
def health_check():
    """[REAL] Health check endpoint."""
    return {"status": "ok", "message": "API is running"}


# ----------------------------------------------------------------------------
# [REAL + MOCK FALLBACK] Used by Uploaddashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/analyze-full")
async def analyze_files_full(files: list[UploadFile] = File(...)):
    """
    [REAL + MOCK FALLBACK]
    Full analysis workflow:
    1. Accept files
    2. Create file summaries
    3. Generate recommendation prompt
    4. Send to AI
    5. Return results and place on analysis page

    Runs the real DataLoader -> SummaryGenerator -> RecommendationRequester ->
    AI_Engine pipeline when DATA_MODULES_AVAILABLE and no exception occurs;
    otherwise falls back to _mock_analyze_full_fallback() values.

    Returns:
        {
            "session_id": "20260704_120530",
            "status": "complete",
            "file_profiles": [...],
            "prompt": "...",
            "recommendations": {...},
            "analysis": {...}
        }
    """
    session_id = generate_session_id()
    temp_dir = None
    
    try:
        # Validate files
        if not files or len(files) == 0:
            raise HTTPException(status_code=400, detail="No files provided")
        
        # Save uploaded files to temp directory
        temp_dir = tempfile.mkdtemp()
        file_paths = []
        file_metadata = []
        
        for file in files:
            if not file.filename:
                continue
                
            filepath = os.path.join(temp_dir, file.filename)
            content = await file.read()
            
            if len(content) == 0:
                raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
            
            with open(filepath, 'wb') as f:
                f.write(content)
            
            file_paths.append(filepath)
            file_metadata.append({
                "name": file.filename,
                "size": len(content),
                "rows": None,  # filled in from the real FileProfile once profiling runs
            })

        if not file_paths:
            raise HTTPException(status_code=400, detail="No valid files provided")

        # [MOCK] default/fallback values — overwritten below if the real pipeline succeeds
        fallback = _mock_analyze_full_fallback(file_paths)
        file_profiles = fallback["file_profiles"]
        prompt = fallback["prompt"]
        recommendations = fallback["recommendations"]

        # Loaded DataFrames kept in memory for /api/generate-report. The uploaded
        # files themselves are deleted below, and worksheets never existed as files,
        # so this is the only way the report step can reach the user's data.
        session_tables = {}

        if DATA_MODULES_AVAILABLE:
          # ------------------------------------------------------------------
          # [REAL] DataLoader -> SummaryGenerator -> RecommendationRequester -> AI_Engine
          # ------------------------------------------------------------------
          try:
            print(f"[API] Loading {len(file_paths)} files...")

            # Load files
            loader = DataLoader()
            loader.add_files(file_paths)
            session_tables = loader.tables()

            print(f"[API] Generating summaries...")

            # Generate summaries
            summary_gen = SummaryGenerator()
            file_profiles = summary_gen.profile_all_files(loader)
            relationships = summary_gen.detect_relationships(file_profiles, loader)

            print(f"[API] Building recommendation prompt...")

            # Build recommendation prompt. The static instruction block goes in the
            # system message (a stable, cacheable prefix); only the per-dataset profiles
            # go in the user message.
            requester = RecommendationRequester()
            system_prompt = requester.build_system_prompt()
            prompt = requester.build_request_prompt(file_profiles, relationships)

            print(f"[API] Sending to AI Engine...")

            # Send to AI, validate against the recommendations schema, and
            # retry with correction feedback if it doesn't validate
            valid_filenames = {p.filename for p in file_profiles}
            recommendations = ai_engine.get_validated_recommendations(
                prompt, valid_filenames, session_id=session_id, tables=session_tables,
                correction_prompt=requester.build_correction_prompt(file_profiles, relationships),
                system_prompt=system_prompt,
            )

            # Backfill the real row/column counts now that profiling has produced
            # them. file_metadata is what the frontend actually renders, and it was
            # previously reporting a hardcoded 500 rows for every file regardless of
            # what had been uploaded.
            by_name = {p.filename: p for p in file_profiles}
            for meta in file_metadata:
                profile = by_name.get(meta["name"])
                if profile:
                    meta["rows"] = profile.row_count
                    meta["columns"] = len(profile.columns)

            print(f"[API] Analysis complete!")

          except Exception as e:
            # Surface a real error instead of silently returning mock data dressed up
            # as a real analysis - showing fabricated recommendations as if they were
            # generated from the user's files is misleading. The console line gives the
            # developer the underlying cause; the 502 gives the UI a message to render
            # (Uploaddashboard already displays errBody.detail).
            print(f"[API] Analysis failed: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=502,
                detail="AI analysis failed — check the server console and try again.",
            )

        # Store session info
        SESSIONS[session_id] = {
            "files": file_metadata,
            "status": "complete",
            "created_at": datetime.now().isoformat(),
            "file_profiles": file_profiles,
            "prompt": prompt,
            "recommendations": recommendations,
            "analysis": recommendations,
            "tables": session_tables
        }
        
        # NOTE: this "file_profiles" response field is actually `file_metadata`
        # (name/size/rows/columns). The full per-column `file_profiles` computed above
        # stays in SESSIONS; the report endpoint reads temporal granularity out of it.
        return {
            "session_id": session_id,
            "status": "complete",
            "file_profiles": file_metadata,
            "prompt": prompt,
            "recommendations": recommendations,
            "analysis": recommendations
        }

    except HTTPException:
        # Re-raise HTTP exceptions (validation errors)
        raise

    except Exception as e:
        print(f"[API] Unexpected error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


# ============================================================================
# REPORT RESPONSE HELPERS
# ============================================================================

# How many report rows travel to the browser for the table view. The full set stays
# in SESSIONS; a 50k-row report should not become a 50k-row JSON payload.
MAX_ROWS_RETURNED = 500


def _json_safe_records(df, limit):
    """Report rows as JSON-safe dicts.

    Routed through pandas' own JSON writer because a report DataFrame routinely holds
    Timestamps, numpy scalars, NaN and ordered Categoricals - none of which the
    default JSON encoder will accept.
    """
    import json as _json
    try:
        return _json.loads(df.head(limit).to_json(orient="records", date_format="iso"))
    except Exception as e:
        print(f"[API] Warning: could not serialize report rows: {type(e).__name__}: {e}")
        return []


def _axis_granularity(file_profiles, column):
    """The temporal granularity (daily/weekly/monthly/yearly) profiled for `column`.

    Lets report_stats format date labels at the dataset's own resolution - "Mar 2023"
    for a monthly report rather than "1 Mar 2023", which implies a precision the data
    doesn't have.
    """
    if not column or not file_profiles:
        return None
    for profile in file_profiles:
        for col in getattr(profile, "columns", []) or []:
            if col.name == column and getattr(col, "temporal_granularity", None):
                return col.temporal_granularity
    return None


def _describe_operations(operations):
    """One short human-readable line per pipeline step, for the provenance strip.

    The reader is being asked to trust these numbers; showing which filters and
    aggregations produced them is part of earning that.
    """
    described = []
    for op in operations or []:
        kind = op.get("operation_type")
        if kind == "filter":
            for cond in op.get("filter_conditions", []) or []:
                col = cond.get("column", "?")
                val = cond.get("condition", cond.get("value", ""))
                described.append(f"filter {col} {val}".strip())
        elif kind == "groupby":
            keys = ", ".join(op.get("groupby_columns", []) or [])
            aggs = ", ".join(
                f"{a.get('func')}({a.get('column')})" for a in op.get("aggregations", []) or []
            )
            described.append(f"group by {keys}" + (f" → {aggs}" if aggs else " → count"))
        elif kind == "derive":
            for d in op.get("derive_columns", []) or []:
                described.append(f"derive {d.get('new_column', '?')} ({d.get('method', '?')})")
        elif kind == "sort_limit":
            limit = op.get("limit")
            described.append(f"top {limit}" if limit else "sort")
        elif kind == "join":
            described.append(f"join {', '.join(op.get('files_involved', []) or [])}")
    return described


# ----------------------------------------------------------------------------
# [REAL] Used by Analysisdashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/generate-report")
def generate_report_endpoint(request: GenerateReportRequest):
    """
    [REAL]
    Generate a structured report from AI recommendations.

    Body:
        {
            "session_id": "20260704_120530",
            "report_type": "A"
        }

    Returns:
        {
            "session_id": "20260704_120530",
            "report_type": "A",
            "status": "generated",
            "report_rows": 15,
            "columns": ["rank", "report_name", ...],
            "message": "Report generated successfully"
        }
    """
    session_id = request.session_id
    report_type = request.report_type
    
    print(f"\n[API] /api/generate-report called")
    print(f"[API] Request: session_id={session_id}, report_type={report_type}")
    print(f"[API] Available sessions: {list(SESSIONS.keys())}")
    
    if session_id not in SESSIONS:
        print(f"[API] Session {session_id} not found!")
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    
    session = SESSIONS[session_id]
    recommendations = session.get("recommendations")
    
    if not recommendations:
        print(f"[API] No recommendations in session {session_id}")
        raise HTTPException(status_code=400, detail="No recommendations found in session")
    
    try:
        print(f"\n[API] Generating report for session {session_id}, type {report_type}")
        
        # Generate report using report_builder (executes operations on actual data).
        # Uploaded data comes from the session's in-memory tables; file_paths stays
        # empty so non-upload flows can still fall back to the datasets/ directory.
        report_df = generate_report(
            recommendations,
            report_type,
            file_paths={},
            session_id=session_id,
            tables=SESSIONS[session_id].get("tables")
        )

        # The report couldn't be built (bad recommendation, missing table, failed
        # operation). Surface why instead of returning a blank report as a success.
        report_error = report_df.attrs.get("error")
        if report_error:
            raise HTTPException(status_code=422, detail=f"Could not generate report: {report_error}")

        # Build the chart the AI recommended for this same report (its plotly_config)
        recs_list = recommendations.get("recommendations", [])
        rec_idx = report_type_to_index(report_type)
        selected_rec = recs_list[rec_idx] if rec_idx is not None and rec_idx < len(recs_list) else None

        # Both initialised up front: axes_config used to be bound only inside the
        # `if selected_rec:` branch, so anything reading it afterwards raised
        # NameError on a report with no matching recommendation.
        chart = None
        axes_config = None
        if selected_rec:
            try:
                axes_config = resolve_plotly_axes(
                    report_df,
                    selected_rec.get("plotly_config") or {},
                    selected_rec.get("required_operations", [])
                )
                chart = build_chart_figure(report_df, axes_config)
            except Exception as e:
                print(f"[API] Warning: Failed to build chart: {type(e).__name__}: {e}")

        # Real statistics, computed from the report's own rows. These replace the
        # model's pre-execution guesses (question_answered / data_quality_warning /
        # rationale_bullets), which were written before any data was aggregated and
        # were being displayed as though they were findings.
        schema_warning = report_df.attrs.get("schema_warning")
        stats = {"available": False}
        try:
            stats = build_report_stats(
                report_df,
                axes_config,
                pattern=(selected_rec or {}).get("pattern_used"),
                granularity=_axis_granularity(
                    session.get("file_profiles"), (axes_config or {}).get("x_axis")
                ),
                llm_caveat=(selected_rec or {}).get("data_quality_warning"),
                schema_warning=schema_warning,
            )
        except Exception as e:
            # A stats failure must not cost the user their report or their chart.
            print(f"[API] Warning: Failed to compute report stats: {type(e).__name__}: {e}")

        # The user's own columns, without the two bookkeeping columns report_builder
        # prepends. Counting those made every report claim two extra columns.
        data_columns = [c for c in report_df.columns if c not in METADATA_COLUMNS]
        rows = _json_safe_records(report_df, MAX_ROWS_RETURNED)
        generated_at = datetime.now().isoformat()

        payload = {
            "session_id": session_id,
            "report_type": report_type,
            "status": "generated",
            "report_name": selected_rec.get("report_name") if selected_rec else None,
            "report_rows": len(report_df),
            "columns": list(report_df.columns),
            "data_columns": data_columns,
            "chart": chart,
            # The chart type the recommendation asked for. Reading it off the built
            # figure instead reports Plotly's trace name, which labels every "line"
            # recommendation as a "scatter".
            "chart_type": ((selected_rec or {}).get("plotly_config") or {}).get("chart_type"),
            "pattern_used": (selected_rec or {}).get("pattern_used"),
            "question_answered": (selected_rec or {}).get("question_answered"),
            "rationale_bullets": (selected_rec or {}).get("rationale_bullets", []),
            "stats": stats,
            "rows": rows,
            "rows_truncated": len(report_df) > len(rows),
            "schema_warning": schema_warning,
            "generated_at": generated_at,
            "source_files": sorted({
                f for op in (selected_rec or {}).get("required_operations", []) or []
                for f in op.get("files_involved", []) or []
            }),
            "operations": _describe_operations((selected_rec or {}).get("required_operations")),
            "message": f"Report generated successfully with {len(report_df)} rows"
        }

        # Store the full report server-side; the response carries a capped slice.
        SESSIONS[session_id]["report"] = {
            **payload,
            "generated_at": generated_at,
            "data": report_df.to_dict(orient="records"),
        }

        return payload
    
    except HTTPException:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a 500
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
