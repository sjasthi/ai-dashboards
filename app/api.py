"""
FastAPI server for AI Dashboard backend.

Every endpoint runs the real data pipeline. If the pipeline modules fail to
import at startup, the analysis endpoint returns 503 rather than substituting
placeholder data - fabricated results presented as a real analysis are worse
than an outage, because nothing on screen tells the user they aren't real.
"""

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Response, Header, Depends
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

# Usage tracking gets its own guarded import for the same reason export does: it is
# unrelated to the analysis pipeline, and a broken telemetry module must not take
# uploads down with it. Every call site is additionally wrapped by telemetry's own
# swallowing try/except, so this flag is the outer of two independent guards.
try:
    from app.data import telemetry
    TELEMETRY_AVAILABLE = True
except ImportError as e:
    TELEMETRY_AVAILABLE = False
    print(f"[API] Telemetry not available: {e} — usage tracking is off")

# Session snapshots (the developer report browser) get their own guarded import for
# the third time for the third reason: saving a session's source workbook is a
# developer convenience, and a failure to import it must never stop an analysis the
# user actually asked for. Like telemetry, the call site adds a second try/except.
try:
    from app.data import session_store
    SESSION_STORE_AVAILABLE = True
except ImportError as e:
    SESSION_STORE_AVAILABLE = False
    print(f"[API] Session store not available: {e} — report history is off")

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


class BrowserEvent(BaseModel):
    """One interaction the browser reports that the server cannot observe itself."""
    event: str = Field(..., max_length=64)
    session_id: Optional[str] = Field(None, max_length=64)
    props: dict = Field(default_factory=dict)


class BrowserEventBatch(BaseModel):
    """A batch of browser events.

    Capped at 50: this endpoint takes unauthenticated input from anyone who can
    reach the server, so the batch size, each event name's length, and the set of
    accepted names are all bounded. Without a cap, one request could append
    unlimited rows to the usage database.
    """
    events: list[BrowserEvent] = Field(..., min_length=1, max_length=50)

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
# USAGE TRACKING
# ============================================================================
# Thin wrappers so no handler below has to think about whether telemetry is
# importable, and so "telemetry must never fail a user request" is enforced in one
# place rather than at every call site. telemetry.py already swallows its own
# exceptions; these add the TELEMETRY_AVAILABLE guard on top.

def client_id(x_client_id: Optional[str] = Header(None)):
    """The browser's anonymous id, from the X-Client-Id header.

    None whenever the frontend hasn't sent one - an older cached bundle, a curl
    call, or a browser where localStorage is unavailable. That is a normal case,
    not an error: the event is still logged, it just doesn't count toward the
    distinct-user total.
    """
    return x_client_id


def track_props(event, client, session, props):
    """Log one event whose props are already a dict.

    The dict-taking form exists for POST /api/events, whose props come from the
    browser. Routing those through track()'s **kwargs would let a prop named
    "client" or "session" collide with the parameter of the same name and raise
    TypeError at the call site - before telemetry's own swallowing guard, so it
    would 500 the request. Untrusted keys never become keyword arguments.

    The try/except is deliberately a *second* guard: telemetry.log_event already
    swallows everything internally. One guard is not enough for a rule as absolute
    as "telemetry must never fail a user request" - it makes every upload in the
    app depend on a try/except in another module staying correct forever. This one
    holds even if that one is broken, and the argument-evaluation that happens at
    the call sites below (dict comprehensions over probe results, sums over
    profiles) is only covered here.
    """
    if not TELEMETRY_AVAILABLE:
        return
    try:
        telemetry.log_event(event, client_id=client, session_id=session, props=props or {})
    except Exception as e:
        print(f"[API] Telemetry failed for {event!r}: {type(e).__name__}: {e}")


def track(event, client=None, session=None, **props):
    """Log one event. Never raises, never returns anything worth checking.

    Keyword form for the handlers in this file, where the prop names are literals
    written here and cannot collide.
    """
    track_props(event, client, session, props)


def track_file(**kwargs):
    if not TELEMETRY_AVAILABLE:
        return
    try:
        telemetry.record_file(**kwargs)
    except Exception as e:
        print(f"[API] Telemetry file record failed: {type(e).__name__}: {e}")


def track_report(**kwargs):
    if not TELEMETRY_AVAILABLE:
        return
    try:
        telemetry.record_report(**kwargs)
    except Exception as e:
        print(f"[API] Telemetry report record failed: {type(e).__name__}: {e}")


def save_snapshot(**kwargs):
    """Persist one session's source files and manifest. Never raises.

    Shaped like track_file/track_report above and for the same reason: report
    history is a developer convenience layered onto a user request, so the two
    independent guards (the import flag, then this try/except) mean neither a
    missing module nor a full disk can turn a completed analysis into a 500.

    The flag is checked here rather than at the call site so the handler reads as
    one unconditional line and the "read the env per call" rule lives in one place.
    """
    if not SESSION_STORE_AVAILABLE or not session_store.history_enabled():
        return
    try:
        session_store.save_session_snapshot(**kwargs)
        print(f"[API] Saved session snapshot for {kwargs.get('session_id')}")
    except Exception as e:
        print(f"[API] Session snapshot failed: {type(e).__name__}: {e}")


def _ext_of(filename):
    """Lowercase extension including the dot, or None. api.py never computed this
    before - workbook_probe did it internally - but the extension mix is the single
    most useful thing to know about what people upload."""
    if not filename:
        return None
    return os.path.splitext(filename)[1].lower() or None


def _tally(values):
    """{'.csv': 2, '.xlsx': 1} — count occurrences, skipping None."""
    counts = {}
    for value in values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _ext_mix(filenames):
    return _tally(_ext_of(name) for name in filenames)


def _elapsed_ms(started):
    return int((time.perf_counter() - started) * 1000)


# Event names POST /api/events will accept. A whitelist rather than free text
# because the endpoint is unauthenticated: without one, anyone reaching the server
# could write arbitrary event names into the usage database and make every
# breakdown meaningless. Adding a browser event means adding it here.
BROWSER_EVENTS = frozenset({
    # One per browser session, sent by the home page. The server cannot see an
    # arrival for itself - a visitor who only reads the landing page never touches
    # another endpoint - so this is the only record that anyone showed up.
    "visit_started",
    # Fired once per browser session, the first time an analysis succeeds. This is
    # what promotes a visitor to a user. The server cannot infer it: it sees the
    # analysis, but not whether this browser had already run one earlier in the
    # same sitting.
    "user_activated",
    "report_viewed",
    "report_retry_clicked",
    "compare_all_opened",
    "data_table_toggled",
    "tab_changed",
    "export_panel_opened",
})

# Props are the browser's, so their size is bounded too - a report letter and a tab
# name need very little room.
MAX_EVENT_PROPS = 20
MAX_PROP_CHARS = 200


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
# [REAL] Usage tracking
# ----------------------------------------------------------------------------
@app.get("/api/stats")
def usage_stats():
    """[REAL] Aggregate usage counters for the home page.

    Public and unauthenticated because it is aggregate-only - counts and
    breakdowns, no per-session or per-file detail, nothing traceable to a person.
    The full event log is a separate, token-gated concern.

    Returns zeros rather than an error when nothing has been recorded yet, which is
    the state on a fresh clone and on first boot. A home page that fails because
    nobody has used the app is worse than one showing four zeros.
    """
    if not TELEMETRY_AVAILABLE:
        return telemetry_unavailable_stats()
    try:
        return telemetry.stats()
    except Exception as e:
        # Same second-guard reasoning as track_props: telemetry.stats() already
        # falls back internally, and the home page still must not break if it stops.
        print(f"[API] Stats unavailable: {type(e).__name__}: {e}")
        return telemetry_unavailable_stats()


def telemetry_unavailable_stats():
    """Same shape as a real response, so the frontend has one contract to code
    against whether or not telemetry imported."""
    return {
        "visits": 0, "users": 0, "clients": 0, "sessions": 0, "files_processed": 0,
        "reports_built": 0, "ext_breakdown": {}, "pattern_breakdown": {}, "daily": [],
        "available": False,
    }


@app.post("/api/events")
def record_browser_events(
    batch: BrowserEventBatch,
    client: Optional[str] = Depends(client_id),
):
    """[REAL] Record interactions the server cannot see for itself.

    Which report a user actually looked at, which tab they switched to, whether
    they opened the comparison view - all of it happens entirely in the browser.
    Generated-but-never-viewed is only visible from here.

    Unknown event names are dropped, not rejected: a stale bundle sending an event
    name that has since been renamed should lose that one event, not have its whole
    batch fail. The response reports both counts so the drop is visible rather than
    silent.
    """
    accepted = 0
    rejected = []

    for item in batch.events:
        if item.event not in BROWSER_EVENTS:
            rejected.append(item.event)
            continue
        props = {
            k: (v[:MAX_PROP_CHARS] if isinstance(v, str) else v)
            for k, v in list((item.props or {}).items())[:MAX_EVENT_PROPS]
        }
        track_props(item.event, client, item.session_id, props)
        accepted += 1

    return {"accepted": accepted, "rejected": sorted(set(rejected))}


# ----------------------------------------------------------------------------
# [REAL] Used by Uploaddashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/inspect")
async def inspect_uploaded_files(
    files: list[UploadFile] = File(...),
    client: Optional[str] = Depends(client_id),
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
    started = time.perf_counter()
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

        # `submitted` and `inspected` differ whenever a file arrives with no
        # filename: the loop above skips those, so len(files) != len(results) and
        # reporting only one of the two would quietly lose them.
        track(
            "files_inspected", client=client,
            submitted=len(files),
            inspected=len(results),
            ext_mix=_ext_mix(f.filename for f in files),
            total_bytes=sum(r.get("size") or 0 for r in results),
            kinds=_tally(r.get("kind") for r in results),
            sheet_counts=[len(r.get("sheets") or []) for r in results],
            multisheet_files=sum(1 for r in results if len(r.get("sheets") or []) > 1),
            empty_sheets=sum(1 for r in results for s in (r.get("sheets") or []) if s.get("empty")),
            failed=sum(1 for r in results if r.get("error")),
            duration_ms=_elapsed_ms(started),
        )
        return {"files": results}

    except HTTPException:
        raise

    except Exception as e:
        print(f"[API] Inspect failed: {type(e).__name__}: {e}")
        track("inspect_failed", client=client, error_type=type(e).__name__,
              submitted=len(files or []), duration_ms=_elapsed_ms(started))
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
    client: Optional[str] = Depends(client_id),
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
    naming which worksheets of each workbook to analyse. Omitted or unparseable
    means "every sheet", so older clients keep working unchanged.

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
    started = time.perf_counter()
    llm_meta = {}

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

        track(
            "analysis_started", client=client, session=session_id,
            file_count=len(file_paths),
            ext_mix=_ext_mix(m["name"] for m in file_metadata),
            total_bytes=sum(m["size"] for m in file_metadata),
            # None means the client sent no `selections` at all, i.e. "every sheet".
            # Distinguishing that from an explicit selection is the whole point:
            # it's what tells you whether the sheet-picker is actually being used.
            has_selections=sheet_selections is not None,
            sheets_selected=sum(len(v) for v in (sheet_selections or {}).values()) or None,
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
            llm_started = time.perf_counter()
            recommendations = ai_engine.get_validated_recommendations(
                prompt, valid_filenames, session_id=session_id, tables=session_tables,
                correction_prompt=requester.build_correction_prompt(file_profiles, relationships),
                system_prompt=system_prompt,
                # Filled in by AI_Engine with which provider actually answered. Read
                # with .get() below, never indexed: the test suite stubs this
                # function with a fake that takes **kwargs and never populates it.
                meta=llm_meta,
            )
            llm_ms = _elapsed_ms(llm_started)

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

            recs = (recommendations or {}).get("recommendations", []) or []
            track(
                "analysis_completed", client=client, session=session_id,
                file_count=len(file_paths),
                ext_mix=_ext_mix(m["name"] for m in file_metadata),
                table_count=len(session_tables),
                total_rows=sum(p.row_count for p in file_profiles),
                total_columns=sum(len(p.columns) for p in file_profiles),
                relationships=len(relationships),
                system_prompt_chars=len(system_prompt or ""),
                prompt_chars=len(prompt or ""),
                # .get() throughout: a stubbed engine leaves llm_meta empty, and a
                # missing key here must not turn a successful analysis into a 500.
                llm_provider=llm_meta.get("provider"),
                llm_model=llm_meta.get("model"),
                llm_attempts=llm_meta.get("llm_attempts"),
                llm_provider_failures=llm_meta.get("provider_failures"),
                llm_used_fallback=llm_meta.get("used_fallback"),
                validation_failures=llm_meta.get("validation_failures"),
                llm_ms=llm_ms,
                recommendation_count=len(recs),
                patterns=_tally(r.get("pattern_used") for r in recs),
                chart_types=_tally((r.get("plotly_config") or {}).get("chart_type") for r in recs),
                duration_ms=_elapsed_ms(started),
            )

            # One row per uploaded file, so "what data do people bring" is a query
            # over shapes rather than a reconstruction from event props.
            # sheet_count is the workbook's own size and sheets_selected is how
            # much of it the picker asked for, so the pair reads as "3 of 5". They
            # come from different places on purpose: the count is only knowable
            # after the file is opened, and the selection only from the client.
            # Absent selections leave it null - "the user chose nothing" and "the
            # client never offered a choice" are not the same answer.
            selected_for = {k: len(v) for k, v in (sheet_selections or {}).items()}
            for meta in file_metadata:
                source = loader.sources.get(meta["name"], {})
                track_file(
                    session_id=session_id, client_id=client, name=meta["name"],
                    ext=_ext_of(meta["name"]), size_bytes=meta["size"],
                    rows=meta.get("rows"), columns=meta.get("columns"),
                    kind=source.get("kind"),
                    sheet_count=source.get("sheet_count"),
                    sheets_selected=selected_for.get(meta["name"]),
                    load_ok=meta.get("rows") is not None,
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
            # The error *class* only - the message can carry a filename or a column
            # name, and those are hashed everywhere else in this file.
            track(
                "analysis_failed", client=client, session=session_id,
                error_type=type(e).__name__, stage="pipeline",
                file_count=len(file_paths),
                ext_mix=_ext_mix(m["name"] for m in file_metadata),
                llm_provider=llm_meta.get("provider"),
                llm_attempts=llm_meta.get("llm_attempts"),
                validation_failures=llm_meta.get("validation_failures"),
                duration_ms=_elapsed_ms(started),
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

        # Persist the snapshot HERE, not later: the `finally` below deletes temp_dir,
        # and once shutil.rmtree has run the uploaded files are gone for good. This
        # is also the one point where every value the manifest needs is in scope.
        # No-op unless SAVE_REPORT_HISTORY is on.
        save_snapshot(
            session_id=session_id,
            client_id=client,
            file_paths=file_paths,
            sheet_selections=sheet_selections,
            recommendations=recommendations,
            file_profiles=file_profiles,
            file_metadata=file_metadata,
        )

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


def _build_report(session, session_id, report_type):
    """Everything after the cache check: generate_report → chart → stats → payload.

    Lifted verbatim out of /api/generate-report so that a replay of a saved session
    goes down the *same* code path a live request does. A second render path would
    drift from this one within a release, and then the developer report browser
    would be showing something the app never produces.

    `session` is the SESSIONS entry (or one rehydrated from disk by
    `rehydrate_session`) - this function only reads "recommendations", "tables" and
    "file_profiles" from it, which is exactly what makes replay possible.

    Returns (stored, diagnostics):
        stored       the response payload plus a "data" key holding up to
                     MAX_STORED_ROWS raw rows for the export appendix. The
                     response is `stored` minus that key.
        diagnostics  chart/stats failure class names, for telemetry only. They are
                     returned alongside rather than folded into the payload because
                     they are observations about the build, not part of the
                     response contract the frontend renders.

    Raises HTTPException(422) when the report itself could not be built.
    """
    recommendations = session.get("recommendations")

    # Generate report using report_builder (executes operations on actual data).
    # Uploaded data comes from the session's in-memory tables; file_paths stays
    # empty so non-upload flows can still fall back to the datasets/ directory.
    report_df = generate_report(
        recommendations,
        report_type,
        file_paths={},
        session_id=session_id,
        tables=session.get("tables"),
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
    # Both failures below are swallowed so a user keeps their report. That makes
    # them invisible in normal operation - nobody reads the console - so each
    # one is counted instead. A rising chart-failure rate is exactly the kind of
    # regression this whole phase exists to surface.
    chart_failed = None
    stats_failed = None
    if selected_rec:
        try:
            axes_config = resolve_plotly_axes(
                report_df,
                selected_rec.get("plotly_config") or {},
                selected_rec.get("required_operations", [])
            )
            chart = build_chart_figure(report_df, axes_config)
        except Exception as e:
            chart_failed = type(e).__name__
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
        stats_failed = type(e).__name__
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
    # Capped at MAX_STORED_ROWS: the only reader is the export appendix, whose
    # appendix_row_limit is validated against the same constant, so rows past
    # it are unreachable. Without the cap a long report on a large upload sits
    # in memory forever, and prefetching means three of them per session
    # instead of one.
    #
    # "data" holds RAW records - NaN and NaT included, unlike "rows", which went
    # through pandas' JSON writer. It is safe only because its readers are all
    # server-side; returning it over HTTP raises ValueError in Starlette's
    # allow_nan=False encoder on any report with one missing cell.
    stored = {
        **payload,
        "generated_at": generated_at,
        "data": report_df.head(MAX_STORED_ROWS).to_dict(orient="records"),
    }
    return stored, {"chart_error_type": chart_failed, "stats_error_type": stats_failed}


def _response_of(stored):
    """The HTTP response for a built report: everything but the server-side rows."""
    return {k: v for k, v in stored.items() if k != "data"}


# ----------------------------------------------------------------------------
# [REAL] Used by Analysisdashboard.jsx
# ----------------------------------------------------------------------------
@app.post("/api/generate-report")
def generate_report_endpoint(
    request: GenerateReportRequest,
    client: Optional[str] = Depends(client_id),
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
        # An event, deliberately not a `reports` row. The browser prefetches every
        # letter, so cache hits outnumber real builds - counting them as reports
        # would inflate the "reports built" counter and drag every build_ms and
        # pattern statistic toward whatever gets re-requested most.
        track("report_generated", client=client, session=session_id,
              letter=report_type, is_cache_hit=True)
        return _response_of(cached)

    started = time.perf_counter()
    try:
        print(f"\n[API] Generating report for session {session_id}, type {report_type}")

        stored, diagnostics = _build_report(session, session_id, report_type)
        payload = _response_of(stored)
        rows = payload["rows"]
        data_columns = payload["data_columns"]
        chart = payload["chart"]
        schema_warning = payload["schema_warning"]

        # Keyed by report type, because export needs every report the user has
        # generated, not just the last one. A single slot here meant generating A
        # then B left only B on the server, while the browser still showed both -
        # so a combined export of A and B was impossible to fulfil.
        SESSIONS[session_id].setdefault("reports", {})[report_type] = stored
        # Legacy single slot, same object rather than a copy. Kept because
        # tests/test_generate_report_api.py still reads it.
        SESSIONS[session_id]["report"] = stored

        build_ms = _elapsed_ms(started)
        track(
            "report_generated", client=client, session=session_id,
            letter=report_type, is_cache_hit=False,
            pattern=payload["pattern_used"], chart_type=payload["chart_type"],
            report_rows=payload["report_rows"], rows_returned=len(rows),
            rows_truncated=payload["rows_truncated"],
            column_count=len(data_columns),
            has_chart=chart is not None,
            **diagnostics,
            has_schema_warning=bool(schema_warning),
            operation_count=len(payload["operations"] or []),
            duration_ms=build_ms,
        )
        track_report(
            session_id=session_id, client_id=client, letter=report_type,
            pattern=payload["pattern_used"], chart_type=payload["chart_type"],
            rows_returned=len(rows), is_truncated=payload["rows_truncated"],
            build_ms=build_ms, ok=True, has_schema_warning=bool(schema_warning),
        )

        return payload

    except HTTPException as e:
        # Already a deliberate HTTP error (e.g. the 422 above) - don't rewrap as a 500
        track("report_failed", client=client, session=session_id, letter=report_type,
              status_code=e.status_code, error_type="HTTPException",
              duration_ms=_elapsed_ms(started))
        track_report(session_id=session_id, client_id=client, letter=report_type,
                     build_ms=_elapsed_ms(started), ok=False,
                     error_type=f"HTTP{e.status_code}")
        raise

    except Exception as e:
        print(f"[API] Error generating report: {type(e).__name__}: {e}")
        track("report_failed", client=client, session=session_id, letter=report_type,
              error_type=type(e).__name__, duration_ms=_elapsed_ms(started))
        track_report(session_id=session_id, client_id=client, letter=report_type,
                     build_ms=_elapsed_ms(started), ok=False, error_type=type(e).__name__)
        raise HTTPException(status_code=500, detail=f"Error generating report: {str(e)}")


# ============================================================================
# ADMIN — the developer report browser (Phase 3b)
# ============================================================================
# Token-gated, developer-only routes for inspecting and re-opening past sessions.
# Nothing here is reachable from the user-facing app: there is no nav entry and the
# frontend that calls them is stripped from production builds.
#
# The central idea is that a report is *derived*, so it is never stored. What gets
# stored is the source workbook (app/data/session_store.py); a replay rebuilds the
# report from it by calling the same _build_report() the live endpoint calls. That
# costs zero LLM quota, and it means a past session re-rendered after a refactor
# shows what today's code does with that input - a regression check, not an archive.


def admin_token(x_admin_token: Optional[str] = Header(None)):
    """Authorise an admin request, or refuse it.

    Shaped like client_id() above - a plain function reading Header(None), wired in
    with Depends - so it composes the same way the rest of this file's handlers do.

    An unset ADMIN_TOKEN answers 404, not 401, deliberately: a deployment that never
    configured these routes should not advertise that they exist. A wrong token when
    one *is* configured gets 401, because at that point the caller already knows.

    compare_digest rather than == so the comparison doesn't leak the token's length
    or matching prefix through timing.

    Read per call, and read from os.environ rather than by calling load_dotenv()
    here. .env reaches the environment as a side effect of importing AI_Engine at
    startup, so if the data modules failed to import, ADMIN_TOKEN looks unset and
    every admin route 404s - fail-closed, which is the right direction for a gate.
    Calling load_dotenv() per request the way emailer does would fix that, but it
    would also re-populate variables the test suite deliberately deletes, making
    the gate's behaviour depend on whose .env the suite is running against.
    """
    configured = (os.getenv("ADMIN_TOKEN") or "").strip()
    if not configured:
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_admin_token or not secrets.compare_digest(str(x_admin_token), configured):
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return x_admin_token


def _require_session_store():
    """503 when the store failed to import, matching how export refuses."""
    if not SESSION_STORE_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Session history isn't available on this server — "
                   "app/data/session_store.py failed to import. Check the startup log.",
        )


def rehydrate_session(session_id):
    """Rebuild a SESSIONS entry from disk. No LLM call.

    Only three things are needed to make a session real again: the recommendations
    (stored verbatim in the manifest), the tables (rebuilt by re-reading the saved
    workbooks through DataLoader), and the file profiles.

    Profiles are *re-derived* rather than read back from the manifest, even though
    the manifest holds a copy. The manifest's are plain JSON, and the one consumer
    on this path - _axis_granularity - walks `profile.columns[].temporal_granularity`
    as attributes, so dicts would silently yield no granularity and every date axis
    would replay at day resolution. Re-profiling is deterministic and involves no
    model call, so the honest fix is to recompute rather than to half-restore. The
    manifest copy stays for inspection.

    Raises:
        HTTPException: 404 if the session was never saved, 410 if its source files
            have since been deleted, 422 if the saved workbooks no longer load.
    """
    manifest = session_store.load_manifest(session_id)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=f"No saved session {session_id} — it was never saved "
                   f"(SAVE_REPORT_HISTORY off) or has been deleted.",
        )

    paths = session_store.source_paths(session_id)
    if not paths:
        # The directory is the source of truth (see session_store's docstring): a
        # manifest with no source/ is expired, not broken. 410 says so precisely.
        raise HTTPException(
            status_code=410,
            detail=f"Session {session_id} is no longer replayable — its saved source "
                   f"files have been deleted. The manifest remains.",
        )

    loader = DataLoader()
    # sheet_selections is passed through, not defaulted: it decides which worksheets
    # exist and therefore what the table keys are, and the recommendations reference
    # those keys. Replaying without it loads sheets the original run excluded.
    loader.add_files(paths, manifest.get("sheet_selections"))
    tables = loader.tables()
    if not tables:
        raise HTTPException(
            status_code=422,
            detail=f"Session {session_id} loaded no tables — the saved files may be "
                   f"corrupt, or the sheet selection no longer matches them.",
        )

    profiles = SummaryGenerator().profile_all_files(loader)

    return {
        "files": manifest.get("files") or [],
        "status": "complete",
        "created_at": manifest.get("saved_at"),
        "file_profiles": profiles,
        "recommendations": manifest.get("recommendations"),
        "analysis": manifest.get("recommendations"),
        "tables": tables,
        # Marks this entry as reconstructed rather than uploaded. Nothing branches on
        # it today; it exists so that a session in SESSIONS can be told apart from a
        # live one when debugging.
        "rehydrated": True,
    }


@app.get("/api/admin/sessions")
def admin_list_sessions(limit: int = 200, _token: str = Depends(admin_token)):
    """Every saved session, newest first.

    Cheap: it reads manifests, never opens a workbook. `replayable` reflects whether
    the source files are still on disk, so a hand-pruned session_data/ renders as a
    disabled row instead of failing the whole listing.
    """
    _require_session_store()
    return {
        "sessions": session_store.list_sessions(limit=limit),
        "history_enabled": session_store.history_enabled(),
        "live_sessions": sorted(SESSIONS.keys()),
    }


@app.get("/api/admin/sessions/{session_id}/reports/{letter}")
def admin_replay_report(session_id: str, letter: str, _token: str = Depends(admin_token)):
    """Rebuild one report from a saved session and return it.

    Rehydrates *into* SESSIONS, which makes the session genuinely exist again rather
    than existing only for the duration of this call. That is what lets export work
    on a replayed session: _resolve_export finds it by the same key.

    A session already in memory (live, or replayed a moment ago) is reused, so
    replaying B after A does not re-read the workbook from disk.
    """
    _require_session_store()
    letter = (letter or "").strip().upper()

    session = SESSIONS.get(session_id)
    if session is None:
        session = rehydrate_session(session_id)
        SESSIONS[session_id] = session

    if not session.get("recommendations"):
        raise HTTPException(status_code=400, detail="No recommendations found in session")

    cached = (session.get("reports") or {}).get(letter)
    if cached:
        return _response_of(cached)

    # HTTPException (a 422 from a recommendation that no longer executes) is left to
    # propagate untouched - D6's whole point is that a replay failing where the live
    # run succeeded is a finding, and the viewer must show it rather than a blank.
    stored, _diagnostics = _build_report(session, session_id, letter)
    session.setdefault("reports", {})[letter] = stored
    return _response_of(stored)


@app.delete("/api/admin/sessions/{session_id}")
def admin_delete_session(session_id: str, _token: str = Depends(admin_token)):
    """Forget one saved session: its manifest and the workbooks it retained.

    Retention is otherwise entirely manual, so the deletion story should at least be
    reachable from the same interface that shows what is being retained.
    """
    _require_session_store()
    deleted = session_store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No saved session {session_id}")
    SESSIONS.pop(session_id, None)
    return {"session_id": session_id, "deleted": True}


@app.get("/api/admin/stats")
def admin_stats(limit: int = 100, _token: str = Depends(admin_token)):
    """The usage database's raw event log, not just the aggregates /api/stats shows.

    Reads go through telemetry's own accessors rather than raw SQL: they parse the
    `props` JSON column back into dicts and swallow their own failures, so a caller
    gets usable rows instead of opaque blobs.
    """
    if not TELEMETRY_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Telemetry isn't available on this server — check the startup log.",
        )
    return {
        "summary": telemetry.stats(),
        "events": telemetry.recent_events(limit=limit),
        "files": telemetry.recent_files(limit=limit),
        "reports": telemetry.recent_reports(limit=limit),
    }


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
    client: Optional[str] = Depends(client_id),
):
    """[REAL] Download the selected reports as one PDF or HTML file.

    One report selected gives a single-report document; two or more give one
    combined comparative document, since comparing them is the reason to export
    several at once.

    A POST rather than a GET because the browser-rendered chart PNGs travel in the
    body - too large for a query string.
    """
    started = time.perf_counter()
    session, letters = _resolve_export(session_id, request.report_types)
    body, filename = _render_export(session, letters, request)

    track(
        "report_exported", client=client, session=session_id,
        format=request.format, letters=letters, letter_count=len(letters),
        include_appendix=request.include_appendix,
        appendix_row_limit=request.appendix_row_limit,
        # How many of the selected reports the browser actually supplied a chart
        # image for. Short of letter_count means charts are silently missing from
        # the document the user just downloaded.
        chart_images=len({k.upper() for k in (request.chart_images or {})} & set(letters)),
        bytes_out=len(body),
        duration_ms=_elapsed_ms(started),
    )

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
    client: Optional[str] = Depends(client_id),
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
        # e.kind is already a failure taxonomy the emailer maintains - reuse it
        # rather than inventing a second one that can drift from it.
        track("email_failed", client=client, session=session_id,
              format=request.format, letter_count=len(letters),
              recipient_count=len(recipients), error_type=f"EmailSendFailed.{e.kind}")
        status = {"auth": 502, "recipients": 400, "unreachable": 504}.get(e.kind, 502)
        raise HTTPException(status_code=status, detail=str(e))

    track(
        "report_emailed", client=client, session=session_id,
        format=request.format, letters=letters, letter_count=len(letters),
        # The count only. Addresses are personal data and answer no question the
        # home page asks - "how many people get sent a report" does.
        recipient_count=len(recipients),
        has_message=bool(request.message),
        bytes_out=len(body),
    )

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
