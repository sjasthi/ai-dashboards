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
    from app.data.report_builder import generate_report, report_type_to_index, resolve_plotly_axes
    from app.data.chart_builder import build_chart_figure
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

        # Store report in session
        SESSIONS[session_id]["report"] = {
            "type": report_type,
            "generated_at": datetime.now().isoformat(),
            "rows": len(report_df),
            "columns": list(report_df.columns),
            "data": report_df.to_dict(orient="records"),  # Store as list of dicts for JSON
            "chart": chart
        }

        return {
            "session_id": session_id,
            "report_type": report_type,
            "status": "generated",
            "report_rows": len(report_df),
            "columns": list(report_df.columns),
            "report_name": selected_rec.get("report_name") if selected_rec else None,
            "chart": chart,
            "message": f"Report generated successfully with {len(report_df)} rows"
        }
    
    except HTTPException:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a 500
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
