"""
FastAPI server for AI Dashboard backend.

Every endpoint runs the real data pipeline. If the pipeline modules fail to
import at startup, the analysis endpoint returns 503 rather than substituting
placeholder data - fabricated results presented as a real analysis are worse
than an outage, because nothing on screen tells the user they aren't real.
"""

from fastapi import (
    Depends, FastAPI, File, Form, Header, UploadFile, HTTPException, Response,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Literal, Optional
import json
import os
import secrets
import sys
import tempfile
import shutil
import time
from datetime import datetime
from uuid import uuid4

# Ensure the project root is on sys.path so data modules can be imported
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Try to import data pipeline modules at startup
try:
    from app.data.data_loader import DataLoader
    from app.data.workbook_probe import inspect_file
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

# Telemetry gets its own guarded import for the same reason export does: usage
# counting is not worth taking the analysis pipeline down for. Calls go through the
# _track / _record_* helpers below, which absorb anything telemetry throws, so the
# feature can fail at import time, at call time, or inside itself without a user
# ever noticing.
try:
    from app.data import telemetry
    TELEMETRY_AVAILABLE = True
except ImportError as e:
    TELEMETRY_AVAILABLE = False
    print(f"[API] Telemetry not available: {e} — usage stats will be empty")

# Ceiling on how many rows of a report are kept in SESSIONS, and the hard limit on
# what an export may ask for. One constant for both so they cannot drift: anything
# stored beyond what export can request is memory nothing will ever read.
MAX_STORED_ROWS = 5000

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
    appendix_row_limit: int = Field(MAX_APPENDIX_ROWS, ge=0, le=MAX_STORED_ROWS)


class EmailExportRequest(ExportRequest):
    """As ExportRequest, plus who to send it to."""
    recipients: list[str] = Field(..., min_length=1, max_length=10)
    message: Optional[str] = None


class ClientEventRequest(BaseModel):
    """One interface event reported by the browser.

    Bounded on every axis, because this is the only endpoint that writes to the
    telemetry database from unvalidated client input: an unbounded name or props
    payload would let anyone fill the disk. Unrecognised names are still accepted
    but get prefixed by telemetry.log_event, so a frontend typo is visible in the
    data instead of silently lost.
    """
    event: str = Field(..., min_length=1, max_length=64)
    props: dict = Field(default_factory=dict)
    session_id: Optional[str] = Field(default=None, max_length=64)

# ============================================================================
# TELEMETRY PLUMBING
# ============================================================================

# Cap on how much of a client-supplied identifier is stored. The header comes from
# the browser and is never trusted for anything but counting, but an unbounded
# string still has no business reaching the database.
MAX_CLIENT_ID_LEN = 64


def client_id_header(x_client_id: Optional[str] = Header(default=None)) -> Optional[str]:
    """The caller's anonymous browser id, if it sent one.

    FastAPI maps the parameter name to the `X-Client-Id` header. Absent is normal
    and fine: older clients, curl and the tests do not send it, and a request
    without one is still counted, just not attributed to a person.
    """
    if not x_client_id:
        return None
    trimmed = x_client_id.strip()[:MAX_CLIENT_ID_LEN]
    return trimmed or None


def _telemetry_guard(what, call):
    """Run a telemetry call, absorbing anything it throws.

    Belt and braces: telemetry's own functions already swallow their runtime
    errors, but that only protects against failures *inside* their try blocks. A
    mistyped keyword argument here raises TypeError at call time, before any of
    that runs -- and without this guard it would turn a perfectly good report into
    a 500. Instrumentation must not be load-bearing, and the only way to guarantee
    that is to refuse to trust it at the boundary too.
    """
    if not TELEMETRY_AVAILABLE:
        return False
    try:
        return call()
    except Exception as exc:
        print(f"[API] telemetry {what} failed, continuing: "
              f"{type(exc).__name__}: {exc}")
        return False


def _track(event, client_id=None, session_id=None, **props):
    """Log one event if telemetry is importable. Never raises."""
    return _telemetry_guard(
        f"log_event({event})",
        lambda: telemetry.log_event(event, client_id=client_id,
                                    session_id=session_id, props=props),
    )


def _record_file(**kwargs):
    """One files row. Never raises."""
    return _telemetry_guard("record_file", lambda: telemetry.record_file(**kwargs))


def _record_report(**kwargs):
    """One reports row. Never raises."""
    return _telemetry_guard("record_report", lambda: telemetry.record_report(**kwargs))


def _ext_of(filename):
    """Lowercased extension including the dot, for grouping uploads by type."""
    return os.path.splitext(filename or "")[1].lower() or None


# ============================================================================
# SESSIONS
# ============================================================================

SESSIONS = {}  # Store uploaded file metadata by session_id


def generate_session_id():
    """Generate a unique session ID.

    The random suffix is not decoration. This used to be the timestamp alone, at
    second resolution, and `SESSIONS[session_id] = ...` below overwrites without
    checking - so two users starting an upload in the same second shared a key and
    the second one silently replaced the first. The first user's browser kept the
    id it was given and then built every report from the other user's data.

    The timestamp prefix stays because the id doubles as the session_data/<id>/
    directory name and as part of the export filename, and sorting by name is how
    you find a run. Nothing parses it back into a datetime.
    """
    return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"


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
@app.post("/api/inspect")
async def inspect_uploaded_files(
    files: list[UploadFile] = File(...),
    client_id: Optional[str] = Depends(client_id_header),
):
    """
    [REAL]
    Describe uploaded spreadsheets without analysing them, so the upload screen
    can list a workbook's sheets and let the user deselect the ones they don't
    want before committing to a full run.

    Reads only worksheet dimensions, not cell data - see workbook_probe.

    Returns:
        {"files": [
            {"name": "sales.xlsx", "size": 2411008, "kind": "excel", "rows": 12480,
             "sheets": [{"name": "Orders", "rows": 8200, "columns": 12, "empty": false}]}
        ]}
    """
    if not DATA_MODULES_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Analysis pipeline unavailable — the server could not load its data modules.",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        results = []

        for file in files:
            if not file.filename:
                continue

            content = await file.read()
            # Unlike /api/analyze-full an empty file isn't fatal here: the upload
            # screen shows the problem next to the file rather than rejecting the
            # whole batch.
            if len(content) == 0:
                results.append({
                    "name": file.filename, "size": 0, "kind": "unknown",
                    "sheets": [], "rows": None, "columns": None,
                    "error": "This file is empty.",
                })
                continue

            filepath = os.path.join(temp_dir, file.filename)
            with open(filepath, 'wb') as f:
                f.write(content)

            results.append(inspect_file(filepath, file.filename, len(content)))

        # Counted here rather than only at analysis time, because the gap between
        # the two is itself the interesting number: files inspected but never
        # analysed are uploads the user thought better of, or could not use.
        _track(
            "files_inspected",
            client_id=client_id,
            file_count=len(results),
            ext_mix=sorted({_ext_of(r["name"]) for r in results if r.get("name")}),
            total_bytes=sum(r.get("size") or 0 for r in results),
            kinds=sorted({r.get("kind") for r in results if r.get("kind")}),
            multi_sheet_count=sum(1 for r in results if len(r.get("sheets") or []) > 1),
            empty_sheet_count=sum(
                1 for r in results for s in (r.get("sheets") or []) if s.get("empty")
            ),
            error_count=sum(1 for r in results if r.get("error")),
        )

        return {"files": results}

    except HTTPException:
        raise

    except Exception as e:
        print(f"[API] Inspect failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not inspect the uploaded files: {e}")

    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)


# ----------------------------------------------------------------------------
# [REAL] Used by Uploaddashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/analyze-full")
def analyze_files_full(
    files: list[UploadFile] = File(...),
    selections: Optional[str] = Form(None),
    client_id: Optional[str] = Depends(client_id_header),
):
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

    `selections` is an optional JSON object, {"sales.xlsx": ["Orders", "Items"]},
    naming which worksheets of each workbook to analyse. Omitting it means
    "every sheet", so older clients keep working unchanged; sending something
    unparseable is a 400, not a fallback to "every sheet" (see below).

    Deliberately a sync `def`, not `async def`. Every step below blocks: file
    writes, pandas profiling, and a synchronous LLM SDK call. On the event loop
    that froze the entire server for the full ~7s - no health checks, no report
    generation for anyone else, and no way to answer a progress poll about this
    very request. As a sync def, Starlette runs it in the threadpool instead,
    which is what /api/generate-report already does.

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
    started_at = time.perf_counter()
    # Filled in by AI_Engine.send_prompt with the provider that actually answered,
    # which is not necessarily the configured one.
    llm_attribution = {}

    # Reject a malformed selection rather than falling back to "analyze
    # everything", which would hand the LLM sheets the user had unchecked. Only an
    # absent field means "no preference".
    sheet_selections = None
    if selections is not None:
        try:
            sheet_selections = json.loads(selections)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="selections is not valid JSON")
        if not isinstance(sheet_selections, dict) or not all(
            isinstance(v, list) for v in sheet_selections.values()
        ):
            raise HTTPException(
                status_code=400,
                detail="selections must map each filename to a list of sheet names",
            )

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
            # .file.read() rather than `await file.read()`: this handler is sync
            # so it can run off the event loop, and the underlying spooled file
            # reads the same bytes without one.
            content = file.file.read()
            
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

        _track(
            "analysis_started",
            client_id=client_id,
            session_id=session_id,
            file_count=len(file_paths),
            ext_mix=sorted({_ext_of(m["name"]) for m in file_metadata}),
            total_bytes=sum(m["size"] for m in file_metadata),
            # Whether the user actually narrowed anything is the measure of whether
            # sheet selection earns its complexity.
            has_selections=sheet_selections is not None,
            sheets_selected=(
                sum(len(v) for v in sheet_selections.values())
                if sheet_selections else None
            ),
        )

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
            loader.add_files(file_paths, sheet_selections)
            session_tables = loader.tables()

            if not session_tables:
                # Every sheet was deselected, or every one was empty. Fail rather
                # than send the LLM an empty dataset list to invent against.
                raise HTTPException(
                    status_code=400,
                    detail="No worksheets selected — nothing to analyze.",
                )

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
                attribution=llm_attribution,
            )

            # Backfill the real row/column counts now that profiling has produced
            # them. file_metadata is what the frontend actually renders, and it was
            # previously reporting a hardcoded 500 rows for every file regardless of
            # what had been uploaded.
            # Group by originating upload rather than matching profile names to
            # filenames: a multi-sheet workbook yields profiles called
            # "Orders (sales).xlsx", which never equals "sales.xlsx", so an exact
            # match left rows/columns null for every such file. loader.origins
            # holds the mapping because the name can't be parsed back reliably.
            rows_by_source = {}
            cols_by_source = {}
            for profile in file_profiles:
                source = loader.origins.get(profile.filename, profile.filename)
                rows_by_source[source] = rows_by_source.get(source, 0) + profile.row_count
                cols_by_source[source] = max(
                    cols_by_source.get(source, 0), len(profile.columns)
                )
            for meta in file_metadata:
                if meta["name"] in rows_by_source:
                    meta["rows"] = rows_by_source[meta["name"]]
                    meta["columns"] = cols_by_source[meta["name"]]

            print(f"[API] Analysis complete!")

            # One row per uploaded file. Counts come from the profiles, grouped by
            # originating upload, so a multi-sheet workbook is one file row with
            # its sheets' totals rather than one row per sheet.
            # Skipped wholesale when telemetry is unavailable: the grouping below
            # is real work, and there would be nowhere to put the result.
            if TELEMETRY_AVAILABLE:
                sheets_by_source = {}
                for profile in file_profiles:
                    source = loader.origins.get(profile.filename, profile.filename)
                    sheets_by_source[source] = sheets_by_source.get(source, 0) + 1
                for meta in file_metadata:
                    _record_file(
                        session_id=session_id, client_id=client_id,
                        name=meta["name"], ext=_ext_of(meta["name"]),
                        size_bytes=meta["size"], kind="excel"
                        if _ext_of(meta["name"]) in (".xlsx", ".xls") else "csv",
                        sheet_count=sheets_by_source.get(meta["name"]),
                        sheets_selected=(
                            len((sheet_selections or {}).get(meta["name"], []))
                            or None
                        ),
                        rows=meta.get("rows"), columns=meta.get("columns"),
                        load_ok=True,
                    )

            recs = (recommendations or {}).get("recommendations") or []
            _track(
                "analysis_completed",
                client_id=client_id,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                file_count=len(file_paths),
                table_count=len(session_tables),
                total_rows=sum(p.row_count for p in file_profiles),
                relationships_detected=len(relationships or []),
                prompt_chars=len(prompt or ""),
                recommendation_count=len(recs),
                patterns=[r.get("pattern_used") for r in recs],
                chart_types=[
                    (r.get("plotly_config") or {}).get("chart_type") for r in recs
                ],
                # Which provider really answered, plus whether a fallback fired.
                # AI_BACKEND would record intent and be wrong on every failover.
                **{f"llm_{k}": v for k, v in llm_attribution.items()},
            )

        except HTTPException:
            raise

        except Exception as e:
            # Surface a real error instead of silently returning mock data dressed up
            # as a real analysis - showing fabricated recommendations as if they were
            # generated from the user's files is misleading. The console line gives the
            # developer the underlying cause; the 502 gives the UI a message to render
            # (Uploaddashboard already displays errBody.detail).
            print(f"[API] Analysis failed: {type(e).__name__}: {e}")
            # The error *class* is recorded, never the message: exception text can
            # quote file contents, and that would put user data in the analytics DB
            # by the back door.
            _track(
                "analysis_failed",
                client_id=client_id,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                error_type=type(e).__name__,
                stage="pipeline",
                file_count=len(file_paths),
                ext_mix=sorted({_ext_of(m["name"]) for m in file_metadata}),
                **{f"llm_{k}": v for k, v in llm_attribution.items()},
            )
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

# How many report rows travel to the browser for the table view. A larger slice (up
# to MAX_STORED_ROWS) stays in SESSIONS for the export appendix; a 50k-row report
# should not become a 50k-row JSON payload.
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
def generate_report_endpoint(
    request: GenerateReportRequest,
    client_id: Optional[str] = Depends(client_id_header),
):
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

    # Already built for this session - hand back what was stored rather than
    # replaying the whole pandas pipeline. The browser prefetches every report in
    # the background, so a duplicate request is a normal event (a user clicking a
    # report that is already queued) rather than an anomaly.
    #
    # `stored` is the response payload plus a "data" key, so dropping that key
    # reproduces the original response exactly - including its generated_at, which
    # should say when the report was built, not when it was last asked for.
    cached = (session.get("reports") or {}).get(report_type)
    if cached:
        print(f"[API] Returning cached report {report_type} for session {session_id}")
        # Logged as an event but deliberately NOT as a reports row: the report was
        # already counted when it was built, and counting it again would inflate
        # "reports built" every time the prefetcher raced a user's click.
        _track("report_generated", client_id=client_id, session_id=session_id,
               letter=report_type, is_cache_hit=True)
        return {k: v for k, v in cached.items() if k != "data"}

    report_started_at = time.perf_counter()

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
        #
        # Capped at MAX_STORED_ROWS: the only reader is the export appendix, whose
        # appendix_row_limit is validated against the same constant, so rows past
        # it are unreachable. Without the cap a long report on a large upload sits
        # in memory forever, and prefetching means three of them per session
        # instead of one.
        stored = {
            **payload,
            "generated_at": generated_at,
            "data": report_df.head(MAX_STORED_ROWS).to_dict(orient="records"),
        }
        SESSIONS[session_id].setdefault("reports", {})[report_type] = stored
        # Legacy single slot, same object rather than a copy. Kept because
        # tests/test_generate_report_api.py still reads it.
        SESSIONS[session_id]["report"] = stored

        build_ms = int((time.perf_counter() - report_started_at) * 1000)
        chart_type = ((selected_rec or {}).get("plotly_config") or {}).get("chart_type")
        # "pattern_used" is the schema field (models.Recommendation), not "pattern".
        # Reading the wrong key here fails silently -- every row records NULL and the
        # pattern breakdown is quietly empty forever.
        pattern = (selected_rec or {}).get("pattern_used")
        rows_returned = len(payload.get("rows") or [])
        is_truncated = len(report_df) > rows_returned
        has_warning = bool((payload.get("stats") or {}).get("schema_warning"))

        _record_report(
                session_id=session_id, client_id=client_id, letter=report_type,
                pattern=pattern, chart_type=chart_type,
                rows_returned=rows_returned, is_truncated=is_truncated,
                build_ms=build_ms, ok=True, has_schema_warning=has_warning,
            )
        _track("report_generated", client_id=client_id, session_id=session_id,
               letter=report_type, pattern=pattern, chart_type=chart_type,
               rows_returned=rows_returned, is_truncated=is_truncated,
               build_ms=build_ms, has_schema_warning=has_warning,
               is_cache_hit=False)

        # Keep a replayable copy, if the deployment asked for one. Saved here
        # rather than from the browser because `stored` holds up to
        # MAX_STORED_ROWS rows while the client only ever receives
        # MAX_ROWS_RETURNED of them -- saving client-side would silently keep the
        # smaller version. telemetry.save_report is a no-op unless
        # SAVE_REPORT_HISTORY is set.
        _telemetry_guard("save_report", lambda: telemetry.save_report(
            session_id=session_id, client_id=client_id, letter=report_type,
            name=(selected_rec or {}).get("report_name"),
            bundle={
                "bundle_version": telemetry.BUNDLE_VERSION,
                "saved_at": datetime.now().isoformat(),
                "session_id": session_id,
                "report_letter": report_type,
                "report_name": (selected_rec or {}).get("report_name"),
                # The exact /api/generate-report response, plus the wider row set.
                "report": payload,
                "rows_stored": stored["data"],
                # Costs nothing to include and future-proofs a "rebuild from
                # source" path -- which is what scripts/replay_report.py does.
                "recommendations": recommendations,
                # The JSON-safe per-file metadata the frontend renders, not the
                # FileProfile objects, which do not serialise.
                "file_profiles": session.get("files") or [],
            },
        ))

        return payload

    except HTTPException as e:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a
        # 500. Still recorded: a 422 means the AI proposed a report that could not
        # be built from the data, which is exactly the failure worth tracking.
        _record_report(
            session_id=session_id, client_id=client_id, letter=report_type,
            build_ms=int((time.perf_counter() - report_started_at) * 1000),
            ok=False, error_type=f"HTTP{e.status_code}",
        )
        _track("report_failed", client_id=client_id, session_id=session_id,
               letter=report_type, error_type=f"HTTP{e.status_code}",
               status_code=e.status_code)
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        _record_report(
            session_id=session_id, client_id=client_id, letter=report_type,
            build_ms=int((time.perf_counter() - report_started_at) * 1000),
            ok=False, error_type=type(e).__name__,
        )
        _track("report_failed", client_id=client_id, session_id=session_id,
               letter=report_type, error_type=type(e).__name__)
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
def export_reports(
    session_id: str,
    request: ExportRequest,
    client_id: Optional[str] = Depends(client_id_header),
):
    """[REAL] Download the selected reports as one PDF or HTML file.

    One report selected gives a single-report document; two or more give one
    combined comparative document, since comparing them is the reason to export
    several at once.

    A POST rather than a GET because the browser-rendered chart PNGs travel in the
    body - too large for a query string.
    """
    session, letters = _resolve_export(session_id, request.report_types)
    body, filename = _render_export(session, letters, request)

    _track("report_exported", client_id=client_id, session_id=session_id,
           format=request.format, letters=letters, letter_count=len(letters),
           is_combined=len(letters) > 1,
           has_appendix=request.include_appendix,
           chart_image_count=len(request.chart_images or {}),
           bytes_out=len(body))

    return Response(
        content=body,
        media_type=_MEDIA_TYPES[request.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------------------
# [REAL] Used by Reportsdashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/export/{session_id}/email")
def email_reports(
    session_id: str,
    request: EmailExportRequest,
    client_id: Optional[str] = Depends(client_id_header),
):
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

    # recipient_count only. Email addresses are personal data with no analytical
    # value here -- "how often is email used" is answerable without them, and
    # storing them would make this database something it should never become.
    _track("report_emailed", client_id=client_id, session_id=session_id,
           format=request.format, letters=letters, letter_count=len(letters),
           recipient_count=len(recipients),
           has_custom_message=bool(request.message))

    return {
        "status": "sent",
        "recipients": recipients,
        "report_types": letters,
        "format": request.format,
        "filename": filename,
    }


# ============================================================================
# USAGE TRACKING — client-reported events, and the aggregate counters
# ============================================================================

@app.post("/api/events")
def record_client_event(
    request: ClientEventRequest,
    client_id: Optional[str] = Depends(client_id_header),
):
    """[REAL] Record one interface event the backend cannot observe for itself.

    Things like "the user opened compare-all" or "switched tabs" leave no trace on
    the server, so the browser reports them here.

    Always answers 200, even for an event name it does not recognise or when
    telemetry is unavailable. This endpoint exists to collect a nice-to-have, and
    a 4xx would only teach the frontend to handle an error that does not matter --
    the client sends these fire-and-forget and does not read the response.
    """
    recorded = _telemetry_guard(
        f"log_event({request.event})",
        lambda: telemetry.log_event(
            request.event, client_id=client_id, session_id=request.session_id,
            props=request.props,
        ),
    )
    return {"status": "recorded" if recorded else "ignored"}


@app.get("/api/stats")
def usage_stats():
    """[REAL] Aggregate usage counters for the home page.

    Public and unauthenticated, because everything here is a count -- no filenames,
    no column names, no cell values, and nothing scoped to an individual. Anything
    that could identify a person or reveal what they uploaded lives behind the
    admin endpoints instead.

    Returns zeroed counters with available=false rather than an error when nothing
    has been recorded yet, so the home page renders on a fresh install.
    """
    empty = {
        "users": 0, "sessions": 0, "files_processed": 0, "reports_built": 0,
        "ext_breakdown": {}, "pattern_breakdown": {}, "daily": [],
        "available": False,
    }
    # Guarded like every other telemetry call: the home page showing empty tiles is
    # a far better outcome than the home page failing to load.
    return _telemetry_guard("stats", telemetry.stats) or empty


# ============================================================================
# ADMIN — developer-only access to saved reports and the raw event log
#
# Not a user feature. Nothing in the UI links here, the home page never calls
# these, and they are not scoped per client: the only gate is a shared secret.
# ============================================================================

def _admin_token():
    """The configured admin secret, or None. Read per call, like the other flags."""
    return (os.getenv("ADMIN_TOKEN") or "").strip() or None


def require_admin(x_admin_token: Optional[str] = Header(default=None)):
    """Gate for /api/admin/*.

    404 rather than 401 when ADMIN_TOKEN is unset. An unconfigured deployment
    should not advertise that these routes exist -- a 401 confirms the endpoint is
    real and invites someone to go looking for the token.

    A wrong token against a configured server does get 401, because there the
    route's existence is not the secret.
    """
    configured = _admin_token()
    if not configured:
        raise HTTPException(status_code=404, detail="Not Found")

    supplied = (x_admin_token or "").strip()
    # Constant-time compare: these are short-lived dev secrets, but a length-
    # dependent early exit is a free thing to avoid.
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return True


@app.get("/api/admin/reports")
def admin_list_reports(limit: int = 100, _: bool = Depends(require_admin)):
    """[DEV] Saved report bundles, newest first, without their payloads."""
    if not TELEMETRY_AVAILABLE:
        return {"reports": [], "history_enabled": False}
    return {
        "reports": telemetry.list_saved_reports(limit),
        # Surfaced so an empty list is distinguishable from a switched-off feature.
        "history_enabled": telemetry.save_report_history_enabled(),
    }


@app.get("/api/admin/reports/{report_id}")
def admin_get_report(report_id: int, _: bool = Depends(require_admin)):
    """[DEV] One full bundle, enough to re-render the report with no LLM call."""
    if not TELEMETRY_AVAILABLE:
        raise HTTPException(status_code=404, detail="Report not found")
    bundle = telemetry.get_saved_report(report_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return bundle


@app.get("/api/admin/events")
def admin_events(limit: int = 200, _: bool = Depends(require_admin)):
    """[DEV] The raw event log, including props the public /api/stats aggregates away."""
    if not TELEMETRY_AVAILABLE:
        return {"events": []}
    return {"events": telemetry.recent_events(limit)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
