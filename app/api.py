"""
FastAPI server for AI Dashboard backend.

Some endpoints call the real data pipeline; others return mock data.
See the per-endpoint [REAL] / [MOCK] / [REAL + MOCK FALLBACK] / [UNUSED]
tags on each endpoint below.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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
    from app.data.report_builder import generate_report, report_type_to_index, resolve_plotly_axes
    from app.data.chart_builder import build_chart_figure
    from app.data.report_stats import build_report_stats
    from app.data.export_builder import build_export
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
                "rows": 500,  # TODO: REVIEW - [MOCK] placeholder row count — not computed from real data even when pipeline succeeds
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

            # Build recommendation prompt
            requester = RecommendationRequester()
            prompt = requester.build_request_prompt(file_profiles, relationships)

            print(f"[API] Sending to AI Engine...")

            # Send to AI, validate against the recommendations schema, and
            # retry with correction feedback if it doesn't validate
            valid_filenames = {p.filename for p in file_profiles}
            recommendations = ai_engine.get_validated_recommendations(
                prompt, valid_filenames, session_id=session_id, tables=session_tables,
                correction_prompt=requester.build_correction_prompt(file_profiles, relationships),
                max_retries=2
            )

            print(f"[API] Analysis complete!")

          except Exception as e:
            # [MOCK FALLBACK] real pipeline failed — `fallback` values above stand
            print(f"[API] Error during analysis: {type(e).__name__}: {e}")
            print(f"[API] Returning mock data for {len(file_paths)} file(s)")

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
        # (name/size/[MOCK] placeholder rows) — the real per-column `file_profiles`
        # computed above is only stored in SESSIONS, never returned to the caller.
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

        # Real stats computed from the actual report data (peak/trend/anomalies),
        # replacing the LLM's pre-execution guesses (question_answered /
        # data_quality_warning / rationale_bullets) that the frontend used to fall
        # back on. Uses the same resolved axes_config as the chart above, so it
        # analyzes exactly the columns being plotted.
        try:
            stats = build_report_stats(report_df, axes_config)
        except Exception as e:
            print(f"[API] Warning: Failed to compute report stats: {type(e).__name__}: {e}")
            stats = {"available": False, "top_insight_text": None, "anomaly_text": None,
                      "recommendation_text": None, "anomalies": []}

        # Store report in session
        SESSIONS[session_id]["report"] = {
            "type": report_type,
            "generated_at": datetime.now().isoformat(),
            "rows": len(report_df),
            "columns": list(report_df.columns),
            "data": report_df.to_dict(orient="records"),  # Store as list of dicts for JSON
            "chart": chart,
            "stats": stats
        }

        return {
            "session_id": session_id,
            "report_type": report_type,
            "status": "generated",
            "report_rows": len(report_df),
            "columns": list(report_df.columns),
            "report_name": selected_rec.get("report_name") if selected_rec else None,
            "chart": chart,
            "stats": stats,
            "message": f"Report generated successfully with {len(report_df)} rows"
        }
    
    except HTTPException:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a 500
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")


# ============================================================================
# EXPORT — [REAL] Builds Summary Report / Full Analysis / Recommendations as
# a real PDF or HTML file from the session's already-generated report + stats.
# No AI call is made here, so this can't fail due to Ollama/Groq/Gemini issues.
# ============================================================================

@app.get("/api/export/{session_id}")
def export_report(session_id: str, export_type: str = "summary", format: str = "pdf"):
    """
    Args:
        session_id: session returned by /api/analyze-full
        export_type: 'summary' | 'full' | 'recommendations'
        format: 'pdf' | 'html'

    Returns the file directly (Content-Disposition: attachment), so hitting
    this URL in a browser tab or via <a href> triggers a download.
    """
    print(f"\n[API] /api/export called: session_id={session_id}, export_type={export_type}, format={format}")

    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    if not DATA_MODULES_AVAILABLE:
        raise HTTPException(status_code=503, detail="Export is unavailable because the data modules failed to load")

    session = SESSIONS[session_id]

    try:
        content_bytes, mime_type, filename = build_export(session, export_type, format)
    except ValueError as e:
        # build_export raises ValueError for bad export_type/format, or if no
        # report has been generated yet - both are the caller's fault, not a
        # server error.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[API] Error building export: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error building export: {str(e)}")

    return Response(
        content=content_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# TODO: Review & Remove legacy code
# # ============================================================================
# # ENDPOINTS — UNUSED / LEGACY (not called by current frontend)
# # ============================================================================

# # ----------------------------------------------------------------------------
# # [MOCK, UNUSED] Only called by legacy app/web/src/js/api.js (pre-React)
# # ----------------------------------------------------------------------------
# @app.post("/api/upload")
# async def upload_files(files: list[UploadFile] = File(...)):
#     """
#     [MOCK, UNUSED]
#     Upload one or more CSV/Excel files.

#     Returns:
#         {
#             "session_id": "20260704_120530",
#             "status": "processing",
#             "files": [
#                 {"name": "file1.csv", "size": 12345, "rows": 500}
#             ]
#         }
#     """
#     try:
#         session_id = generate_session_id()
#         file_metadata = []

#         for file in files:
#             # Mock parsing — in reality you'd use pandas/openpyxl
#             # to get real row/sheet counts
#             filename = file.filename or "unknown"
#             size = len(await file.read())

#             # Mock: random rows between 500-5000
#             import random
#             rows = random.randint(500, 5000)

#             file_metadata.append({
#                 "name": filename,
#                 "size": size,
#                 "rows": rows
#             })

#         # Store session info
#         SESSIONS[session_id] = {
#             "files": file_metadata,
#             "status": "processing",
#             "created_at": datetime.now().isoformat(),
#             "analysis": None
#         }

#         return {
#             "session_id": session_id,
#             "status": "processing",
#             "files": file_metadata,
#             "message": f"Received {len(files)} file(s). Analysis starting..."
#         }

#     except Exception as e:
#         raise HTTPException(status_code=400, detail=str(e))


# # ----------------------------------------------------------------------------
# # [MOCK, UNUSED] Superseded by /api/analyze-full
# # ----------------------------------------------------------------------------
# @app.post("/api/analyze")
# def analyze_data(session_id: str, report_type: Optional[str] = "A"):
#     """
#     [MOCK, UNUSED]
#     Trigger analysis for a session and report type.

#     Args:
#         session_id: Session ID from upload
#         report_type: 'A', 'B', or 'C'

#     Returns:
#         {
#             "session_id": "20260704_120530",
#             "report_type": "A",
#             "status": "complete",
#             "analysis": {...},
#             "charts": [...]
#         }
#     """
#     if session_id not in SESSIONS:
#         raise HTTPException(status_code=404, detail="Session not found")

#     # Mock analysis result
#     mock_analysis = {
#         "summary": f"Analysis of {len(SESSIONS[session_id]['files'])} file(s) using report type {report_type}",
#         "key_insights": [
#             "Data contains 3 key categories",
#             "Outliers detected in 2 columns",
#             "Strong correlation between variables X and Y"
#         ],
#         "data_quality": {
#             "completeness": "95%",
#             "duplicates": "2.1%",
#             "anomalies": "0.5%"
#         }
#     }

#     mock_charts = [
#         {"type": "bar", "title": "Distribution by Category", "id": "chart_1"},
#         {"type": "line", "title": "Trend Over Time", "id": "chart_2"},
#         {"type": "scatter", "title": "Correlation Analysis", "id": "chart_3"}
#     ]

#     SESSIONS[session_id]["status"] = "complete"
#     SESSIONS[session_id]["analysis"] = mock_analysis

#     return {
#         "session_id": session_id,
#         "report_type": report_type,
#         "status": "complete",
#         "analysis": mock_analysis,
#         "charts": mock_charts
#     }


# # ----------------------------------------------------------------------------
# # [REAL + MOCK FALLBACK, UNUSED] Real session lookup, hardcoded fake charts
# # ----------------------------------------------------------------------------
# @app.get("/api/results/{session_id}")
# def get_results(session_id: str):
#     """
#     [REAL + MOCK FALLBACK, UNUSED]
#     Fetch analysis results for a session.

#     Returns:
#         {
#             "session_id": "20260704_120530",
#             "status": "complete",
#             "analysis": {...},
#             "charts": [...]
#         }
#     """
#     if session_id not in SESSIONS:
#         raise HTTPException(status_code=404, detail="Session not found")
    
#     session = SESSIONS[session_id]
    
#     return {
#         "session_id": session_id,
#         "status": session["status"],
#         "analysis": session["analysis"],
#         "charts": [] if session["analysis"] is None else [
#             {"type": "bar", "title": "Distribution by Category", "id": "chart_1"},
#             {"type": "line", "title": "Trend Over Time", "id": "chart_2"},
#             {"type": "scatter", "title": "Correlation Analysis", "id": "chart_3"}
#         ]
#     }


# # ----------------------------------------------------------------------------
# # [MOCK, UNUSED]
# # ----------------------------------------------------------------------------
# @app.get("/api/export/{session_id}")
# def export_report(session_id: str, format: str = "json"):
#     """
#     [MOCK, UNUSED]
#     Export analysis as PDF, HTML, or JSON.

#     Args:
#         session_id: Session ID
#         format: 'json', 'html', or 'pdf'

#     Returns mock file data
#     """
#     if session_id not in SESSIONS:
#         raise HTTPException(status_code=404, detail="Session not found")
    
#     # Mock response
#     return {
#         "session_id": session_id,
#         "format": format,
#         "status": "ready",
#         "download_url": f"/downloads/{session_id}/report.{format}",
#         "message": f"Report exported as {format.upper()}"
#     }


# # ----------------------------------------------------------------------------
# # [REAL, UNUSED]
# # ----------------------------------------------------------------------------
# @app.get("/api/sessions")
# def list_sessions():
#     """
#     [REAL, UNUSED]
#     List all stored sessions.
#     Useful for Reports page.
#     """
#     sessions_list = [
#         {
#             "session_id": sid,
#             "created_at": sess.get("created_at"),
#             "file_count": len(sess.get("files", [])),
#             "status": sess.get("status"),
#             "total_rows": sum(f.get("rows", 0) for f in sess.get("files", []))
#         }
#         for sid, sess in SESSIONS.items()
#     ]
#     return {"sessions": sessions_list, "total_count": len(sessions_list)}


# # ----------------------------------------------------------------------------
# # [REAL, UNUSED]
# # ----------------------------------------------------------------------------
# @app.delete("/api/sessions/{session_id}")
# def delete_session(session_id: str):
#     """[REAL, UNUSED] Delete a session."""
#     if session_id not in SESSIONS:
#         raise HTTPException(status_code=404, detail="Session not found")

#     del SESSIONS[session_id]
#     return {"message": f"Session {session_id} deleted"}


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
