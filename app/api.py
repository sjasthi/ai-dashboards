"""
FastAPI server for AI Dashboard backend.

This is a mock API layer that your frontend can communicate with.
Later, replace mock responses with real calls to AI_Engine.py and other modules.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
# MOCK DATA & SESSIONS
# ============================================================================

SESSIONS = {}  # Store uploaded file metadata by session_id


def generate_session_id():
    """Generate a unique session ID."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "message": "API is running"}


@app.post("/api/analyze-full")
async def analyze_files_full(files: list[UploadFile] = File(...)):
    """
    Full analysis workflow:
    1. Accept files
    2. Create file summaries
    3. Generate recommendation prompt
    4. Send to AI
    5. Return results and place on analysis page
    
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
                "rows": 500  # Placeholder
            })
        
        if not file_paths:
            raise HTTPException(status_code=400, detail="No valid files provided")
        
        # ---- INTEGRATE YOUR CODE HERE ----
        # Step 1: Load files with DataLoader
        file_profiles = []
        prompt = "Mock prompt (AI_Engine integration pending)"
        recommendations = {
            "summary": "Mock analysis - integrate your AI_Engine code",
            "key_insights": [f"Processed {len(file_paths)} file(s)", "Files received and ready for analysis"],
            "recommendations": ["Connect your data pipeline to process files"]
        }
        
        if DATA_MODULES_AVAILABLE:
          try:
            print(f"[API] Loading {len(file_paths)} files...")
            
            # Load files
            loader = DataLoader()
            loader.add_files(file_paths)
            
            print(f"[API] Generating summaries...")
            
            # Generate summaries
            summary_gen = SummaryGenerator()
            file_profiles = summary_gen.profile_all_files(loader)
            
            print(f"[API] Building recommendation prompt...")
            
            # Build recommendation prompt
            requester = RecommendationRequester()
            prompt = requester.build_request_prompt(file_profiles)
            
            print(f"[API] Sending to AI Engine...")
            
            # Send to AI and get recommendations
            ai_response = ai_engine.send_prompt(prompt)
            
            # Extract JSON from the AI response robustly:
            # LLMs often add markdown fences, preamble, or trailing text.
            # Find the outermost { ... } block.
            if isinstance(ai_response, str):
                start = ai_response.find('{')
                end   = ai_response.rfind('}')
                if start == -1 or end == -1 or end < start:
                    raise ValueError(f"No JSON object found in AI response: {ai_response[:200]}")
                recommendations = json.loads(ai_response[start:end + 1])
            
            print(f"[API] Analysis complete!")
            
          except Exception as e:
            # Runtime error in data pipeline
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
            "analysis": recommendations
        }
        
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


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """
    Upload one or more CSV/Excel files.
    
    Returns:
        {
            "session_id": "20260704_120530",
            "status": "processing",
            "files": [
                {"name": "file1.csv", "size": 12345, "rows": 500}
            ]
        }
    """
    try:
        session_id = generate_session_id()
        file_metadata = []
        
        for file in files:
            # Mock parsing — in reality you'd use pandas/openpyxl
            # to get real row/sheet counts
            filename = file.filename or "unknown"
            size = len(await file.read())
            
            # Mock: random rows between 500-5000
            import random
            rows = random.randint(500, 5000)
            
            file_metadata.append({
                "name": filename,
                "size": size,
                "rows": rows
            })
        
        # Store session info
        SESSIONS[session_id] = {
            "files": file_metadata,
            "status": "processing",
            "created_at": datetime.now().isoformat(),
            "analysis": None
        }
        
        return {
            "session_id": session_id,
            "status": "processing",
            "files": file_metadata,
            "message": f"Received {len(files)} file(s). Analysis starting..."
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/analyze")
def analyze_data(session_id: str, report_type: Optional[str] = "A"):
    """
    Trigger analysis for a session and report type.
    
    Args:
        session_id: Session ID from upload
        report_type: 'A', 'B', or 'C'
    
    Returns:
        {
            "session_id": "20260704_120530",
            "report_type": "A",
            "status": "complete",
            "analysis": {...},
            "charts": [...]
        }
    """
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Mock analysis result
    mock_analysis = {
        "summary": f"Analysis of {len(SESSIONS[session_id]['files'])} file(s) using report type {report_type}",
        "key_insights": [
            "Data contains 3 key categories",
            "Outliers detected in 2 columns",
            "Strong correlation between variables X and Y"
        ],
        "data_quality": {
            "completeness": "95%",
            "duplicates": "2.1%",
            "anomalies": "0.5%"
        }
    }
    
    mock_charts = [
        {"type": "bar", "title": "Distribution by Category", "id": "chart_1"},
        {"type": "line", "title": "Trend Over Time", "id": "chart_2"},
        {"type": "scatter", "title": "Correlation Analysis", "id": "chart_3"}
    ]
    
    SESSIONS[session_id]["status"] = "complete"
    SESSIONS[session_id]["analysis"] = mock_analysis
    
    return {
        "session_id": session_id,
        "report_type": report_type,
        "status": "complete",
        "analysis": mock_analysis,
        "charts": mock_charts
    }


@app.get("/api/results/{session_id}")
def get_results(session_id: str):
    """
    Fetch analysis results for a session.
    
    Returns:
        {
            "session_id": "20260704_120530",
            "status": "complete",
            "analysis": {...},
            "charts": [...]
        }
    """
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = SESSIONS[session_id]
    
    return {
        "session_id": session_id,
        "status": session["status"],
        "analysis": session["analysis"],
        "charts": [] if session["analysis"] is None else [
            {"type": "bar", "title": "Distribution by Category", "id": "chart_1"},
            {"type": "line", "title": "Trend Over Time", "id": "chart_2"},
            {"type": "scatter", "title": "Correlation Analysis", "id": "chart_3"}
        ]
    }


@app.get("/api/export/{session_id}")
def export_report(session_id: str, format: str = "json"):
    """
    Export analysis as PDF, HTML, or JSON.
    
    Args:
        session_id: Session ID
        format: 'json', 'html', or 'pdf'
    
    Returns mock file data
    """
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Mock response
    return {
        "session_id": session_id,
        "format": format,
        "status": "ready",
        "download_url": f"/downloads/{session_id}/report.{format}",
        "message": f"Report exported as {format.upper()}"
    }


@app.get("/api/sessions")
def list_sessions():
    """
    List all stored sessions.
    Useful for Reports page.
    """
    sessions_list = [
        {
            "session_id": sid,
            "created_at": sess.get("created_at"),
            "file_count": len(sess.get("files", [])),
            "status": sess.get("status"),
            "total_rows": sum(f.get("rows", 0) for f in sess.get("files", []))
        }
        for sid, sess in SESSIONS.items()
    ]
    return {"sessions": sessions_list, "total_count": len(sessions_list)}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete a session."""
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    
    del SESSIONS[session_id]
    return {"message": f"Session {session_id} deleted"}


# ============================================================================
# RUN
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
