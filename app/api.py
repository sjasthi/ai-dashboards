"""
FastAPI server for AI Dashboard backend.

Every endpoint runs the real data pipeline. If the pipeline modules fail to
import at startup, the analysis endpoint returns 503 rather than substituting
placeholder data - fabricated results presented as a real analysis are worse
than an outage, because nothing on screen tells the user they aren't real.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal, Optional
import os
import sys
import tempfile
import shutil
from datetime import datetime

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
    # The server still boots so /health answers and the import error is visible
    # in one place; /api/analyze-full refuses with 503 instead of faking a result.
    DATA_MODULES_AVAILABLE = False
    print(f"[API] Data modules not available: {e} — /api/analyze-full will return 503")

# Export gets its own flag rather than joining the block above. Its dependencies
# (xhtml2pdf, jinja2, pillow) are unrelated to the analysis pipeline, and a broken
# PDF library must not take /api/analyze-full down with it.
try:
    from app.data.export_builder import (
        render_export_html, render_export_pdf, export_filename, ExportRenderError,
        MAX_APPENDIX_ROWS,
    )
    from app.data.emailer import (
        send_report_email, smtp_configured, smtp_config_error, validate_recipients,
        EmailNotConfigured, EmailSendFailed,
    )
    EXPORT_AVAILABLE = True
    print("[API] Export modules loaded successfully")
except ImportError as e:
    EXPORT_AVAILABLE = False
    MAX_APPENDIX_ROWS = 200
    print(f"[API] Export modules not available: {e} — /api/export/* will return 503")

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
    # Content-Disposition isn't a CORS-safelisted response header, so without this
    # the frontend reads null for the filename of every export it downloads.
    expose_headers=["Content-Disposition"],
)

# ============================================================================
# REQUEST MODELS
# ============================================================================

class GenerateReportRequest(BaseModel):
    """Request body for generating a report."""
    session_id: str
    report_type: str = "A"


class ExportRequest(BaseModel):
    """Request body for downloading one or more generated reports.

    chart_images maps a report letter to a base64 PNG data URL rendered by the
    browser. They come from the client because all of the chart's theming lives in
    the frontend's chartLayout.js - rendering server-side would produce a chart
    that doesn't match the one the user just looked at. Anything unusable here is
    dropped and the document says the chart is missing; it is never fatal.
    """
    report_types: list[str] = Field(..., min_length=1, max_length=8)
    format: Literal["pdf", "html"] = "pdf"
    chart_images: dict[str, str] = Field(default_factory=dict)
    include_appendix: bool = True
    appendix_row_limit: int = Field(MAX_APPENDIX_ROWS, ge=0, le=5000)


class EmailExportRequest(ExportRequest):
    """As ExportRequest, plus who to send it to."""
    recipients: list[str] = Field(..., min_length=1, max_length=10)
    message: Optional[str] = None

# ============================================================================
# SESSIONS
# ============================================================================

SESSIONS = {}  # Store uploaded file metadata by session_id


def generate_session_id():
    """Generate a unique session ID."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
# [REAL] Used by Uploaddashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/analyze-full")
async def analyze_files_full(files: list[UploadFile] = File(...)):
    """
    [REAL]
    Full analysis workflow:
    1. Accept files
    2. Create file summaries
    3. Generate recommendation prompt
    4. Send to AI
    5. Return results and place on analysis page

    Runs the real DataLoader -> SummaryGenerator -> RecommendationRequester ->
    AI_Engine pipeline. Returns 503 if those modules failed to import and 502 if
    the pipeline raises - never placeholder data.

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
    if not DATA_MODULES_AVAILABLE:
        # Checked before any file I/O: without the pipeline there is nothing this
        # endpoint can honestly return, so fail before touching the upload.
        raise HTTPException(
            status_code=503,
            detail="Analysis pipeline unavailable — the server could not load its data modules.",
        )

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

        # Loaded DataFrames kept in memory for /api/generate-report. The uploaded
        # files themselves are deleted below, and worksheets never existed as files,
        # so this is the only way the report step can reach the user's data.
        session_tables = {}

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

        except HTTPException:
            raise

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
        #
        # Keyed by report type, because export needs every report the user has
        # generated, not just the last one. A single slot here meant generating A
        # then B left only B on the server, while the browser still showed both -
        # so a combined export of A and B was impossible to fulfil.
        stored = {
            **payload,
            "generated_at": generated_at,
            "data": report_df.to_dict(orient="records"),
        }
        SESSIONS[session_id].setdefault("reports", {})[report_type] = stored
        # Legacy single slot, same object rather than a copy. Kept because
        # tests/test_generate_report_api.py still reads it.
        SESSIONS[session_id]["report"] = stored

        return payload
    
    except HTTPException:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a 500
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")

# ============================================================================
# EXPORT — download / email generated reports
# ============================================================================

_MEDIA_TYPES = {"pdf": "application/pdf", "html": "text/html; charset=utf-8"}
_ATTACHMENT_MIMETYPES = {"pdf": ("application", "pdf"), "html": ("text", "html")}


def _resolve_export(session_id: str, report_types: list[str]):
    """Validate an export request and return (session, letters).

    Letters are uppercased, de-duplicated and sorted so that the same selection
    always produces the same filename and the same section order.

    Raises:
        HTTPException: 503 if the export dependencies are missing, 404 for an
            unknown session, 400 for a report the user hasn't generated yet.
    """
    if not EXPORT_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Export isn't available on this server — the PDF/HTML modules "
                   "failed to import. Check the API startup log.",
        )

    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    session = SESSIONS[session_id]
    generated = session.get("reports") or {}

    letters = sorted({str(t).strip().upper() for t in report_types if str(t).strip()})
    if not letters:
        raise HTTPException(status_code=400, detail="No reports were selected.")

    missing = [letter for letter in letters if letter not in generated]
    if missing:
        which = ", ".join(missing)
        raise HTTPException(
            status_code=400,
            detail=f"Report {which} hasn't been generated yet — open it on the "
                   f"Reports page first, then export.",
        )

    return session, letters


def _render_export(session, letters, request: ExportRequest):
    """Render the selected reports, returning (bytes, filename)."""
    # Only the selected reports' images are of any use, and a stray key would
    # otherwise ride along into the template context.
    images = {k.upper(): v for k, v in (request.chart_images or {}).items()
              if k.upper() in letters}

    kwargs = dict(
        chart_images=images,
        include_appendix=request.include_appendix,
        appendix_row_limit=request.appendix_row_limit,
    )

    try:
        if request.format == "pdf":
            body = render_export_pdf(session, letters, **kwargs)
        else:
            body = render_export_html(session, letters, **kwargs).encode("utf-8")
    except ExportRenderError as e:
        raise HTTPException(
            status_code=500, detail=f"Could not build the {request.format.upper()} export: {e}"
        )
    except Exception as e:
        print(f"[API] Export render failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Could not build the {request.format.upper()} export: "
                   f"{type(e).__name__}: {e}",
        )

    return body, export_filename(session, letters, request.format)


# ----------------------------------------------------------------------------
# [REAL] Used by Reportsdashboard.jsx
# ----------------------------------------------------------------------------
@app.get("/api/export/{session_id}/status")
def export_status(session_id: str):
    """[REAL] What this session can currently export.

    The UI asks before rendering the panel so it can disable the email row with the
    reason showing, rather than accepting an address and failing afterwards.
    """
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    return {
        "session_id": session_id,
        "export_available": EXPORT_AVAILABLE,
        "generated": sorted((SESSIONS[session_id].get("reports") or {}).keys()),
        "email_configured": bool(EXPORT_AVAILABLE and smtp_configured()),
    }


# ----------------------------------------------------------------------------
# [REAL] Used by Reportsdashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/export/{session_id}")
def export_reports(session_id: str, request: ExportRequest):
    """[REAL] Download the selected reports as one PDF or HTML file.

    One report selected gives a single-report document; two or more give one
    combined comparative document, since comparing them is the reason to export
    several at once.

    A POST rather than a GET because the browser-rendered chart PNGs travel in the
    body - too large for a query string.
    """
    session, letters = _resolve_export(session_id, request.report_types)
    body, filename = _render_export(session, letters, request)

    return Response(
        content=body,
        media_type=_MEDIA_TYPES[request.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------------------
# [REAL] Used by Reportsdashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/export/{session_id}/email")
def email_reports(session_id: str, request: EmailExportRequest):
    """[REAL] Email the selected reports as a file attachment.

    Every failure below maps to its own status code and a message the user can act
    on. The one outcome that must never happen is a 200 with nothing delivered -
    the user has no reason to check, and finds out days later.
    """
    session, letters = _resolve_export(session_id, request.report_types)

    # Address syntax is checked before the document is built: no point spending a
    # PDF render on a request that can't be delivered.
    try:
        recipients = validate_recipients(request.recipients)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Checked here as well as inside send_report_email so the render below is not
    # spent on a request that can't be delivered. The message comes from the
    # emailer so the two checks can't disagree.
    config_error = smtp_config_error()
    if config_error:
        raise HTTPException(status_code=503, detail=config_error)

    body, filename = _render_export(session, letters, request)

    names = ", ".join(letters)
    plural = "reports" if len(letters) > 1 else "report"
    lines = [
        f"Attached is the AI-Dashboard {plural} you exported ({names}).",
        f"Session {session_id}.",
    ]
    if request.message:
        lines += ["", request.message]
    lines += [
        "",
        "Statistics in the attachment are labelled with where they came from: "
        "'computed' means calculated from the report's own rows, 'AI note' means "
        "the model's own words, which were not checked against the data.",
    ]

    try:
        send_report_email(
            recipients=recipients,
            subject=f"AI-Dashboard {plural} {names} · session {session_id}",
            body_text="\n".join(lines),
            attachment=body,
            filename=filename,
            mimetype=_ATTACHMENT_MIMETYPES[request.format],
        )
    except EmailNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except EmailSendFailed as e:
        status = {"auth": 502, "recipients": 400, "unreachable": 504}.get(e.kind, 502)
        raise HTTPException(status_code=status, detail=str(e))

    return {
        "status": "sent",
        "recipients": recipients,
        "report_types": letters,
        "format": request.format,
        "filename": filename,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
