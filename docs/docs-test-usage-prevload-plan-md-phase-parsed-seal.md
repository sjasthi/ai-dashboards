# Phase 3b — Developer report browser

> **Handoff document.** Phases 1, 2 and 3a are complete and committed. This is written to be read cold in a fresh session — everything needed to start is below.
>
> **Revised 2026-08-03.** The design changed from *storing report output* to *regenerating it from the persisted source*. See [Superseded](#superseded) for what the earlier draft said and why it was wrong.

## Context

Repo: `c:\Users\pmclu\Documents\2026 Summer\capstone\ai-dashboards` — a capstone app that turns uploaded spreadsheets into AI-recommended reports. Branch `final-push`, last commit `1fc40cb updated usage tracking`.

The governing spec is `docs/Test_Usage_PrevLoad_PLAN.md` (committed). Phase 3b is §3b and chunks 17–19 of its execution table. **This document supersedes §3b.2's bundle format.**

**The problem 3b solves:** reports vanish on refresh, and there is no way to inspect what the pipeline produced for a past run. `SESSIONS` at [api.py:154](app/api.py#L154) is an in-memory dict lost on restart. A saved report must be re-openable without re-uploading and **without another LLM call**.

**This is a developer tool, not a user feature.** No nav entry, nothing on the home page, no user-facing endpoint. Best understood as productising debug tooling that already exists: [scripts/replay_report.py](scripts/replay_report.py) already replays a report from saved JSON with no LLM call, and [session_manager.py](app/data/session_manager.py) writes `session_data/<id>/` artifacts behind `SAVE_DEBUG_FILES`.

### The enabling facts

Three properties of the existing code make regeneration cheap. All three were verified, not assumed:

1. **The pipeline is deterministic.** No `datetime.now()`, `date.today()` or `time.time()` anywhere in `report_builder`, `chart_builder` or `report_stats`. The only wall-clock read on the report path is `generated_at` in the endpoint itself ([api.py:997](app/api.py#L997)). Same bytes in, same DataFrame out.
2. **`generate_report` already takes a `tables` dict** ([report_builder.py:55-61](app/data/report_builder.py#L55-L61)) — exactly what the live endpoint passes it ([api.py:934](app/api.py#L934)). Rehydration means rebuilding that dict and nothing else.
3. **Table keys are basename-only.** `DataLoader.tables()` keys on `os.path.basename(name)` ([data_loader.py:114](app/data/data_loader.py#L114)), and worksheets become `"<sheet> (<stem>).<ext>"`. So a persisted source file can live at any path **as long as the original filename is preserved exactly** — the recommendations reference those keys.

---

## State of play

| | |
|---|---|
| Tests | **380 passed, 3 skipped, ~90s** — `python -m pytest -q` |
| Frontend build | `cd app/web && npm run build` — clean |
| Phase 1 | Test harness, 106-file corpus, `scripts/run_test_plan.py` |
| Phase 2 | `usage.db` telemetry, all endpoints instrumented, `GET /api/stats`, `scripts/show_usage.py` |
| Phase 3a | Home tab (landing), three counters, how-it-works steps |

The 3 skips are the empty `tests/data/inbox/` cases — normal.

### Environment gotchas (all cost time to rediscover)

- **`sqlite3` CLI is not installed** (no winget/scoop; choco available). Use `python scripts/show_usage.py` or a `python -c` one-liner.
- **`.env` line 29 has `TELEMETRY_STORE_NAMES=1` active**, so filenames are stored in plaintext on this machine. `tests/conftest.py` deletes it for the suite.
- **Browser checks use Puppeteer** — the chrome-devtools MCP never attaches here. Chrome is in `~/.cache/puppeteer`; install the `puppeteer` package in the scratchpad, not the repo.
- **Vite dev server binds `::1` only** — use `http://localhost:5173`, not `127.0.0.1`.
- **Killing uvicorn on Windows is unreliable.** `Stop-Process` matched on command line silently misses it, and an orphaned multiprocessing worker keeps serving port 8000 after its parent dies. Kill the port's owning process, then *verify the port is free* before concluding the backend is down.
- **`/api/analyze-full` spends real LLM quota.** Stub it in tests with the `stub_llm` fixture; budget deliberately for manual runs.

---

## Design decisions

### D1. Regenerate the report; do not store its rows

**The blocker this works around:** the uploaded spreadsheet does not survive the request. `/api/analyze-full` writes it to `tempfile.mkdtemp()` ([api.py:550](app/api.py#L550)) and the `finally` block deletes the directory ([api.py:781-782](app/api.py#L781-L782)). The code says so at [api.py:592-594](app/api.py#L592-L594):

> Loaded DataFrames kept in memory for /api/generate-report. **The uploaded files themselves are deleted below**, and worksheets never existed as files, so this is the only way the report step can reach the user's data.

So regeneration is not a free win — it requires **persisting the upload**, which is a different privacy commitment, not a smaller one. Be honest about the trade:

| | Store the bundle | Persist the source |
|---|---|---|
| What is retained | 500–5000 rows of report *output* | the whole workbook: every sheet, every row, every column |
| Provenance | a derived extract the app invented | the file the user handed us |
| Size | verbose JSON, ~MB, ×3 per session (A/B/C prefetch) | one zipped `.xlsx`, often smaller |
| Columns retained | only what a report touched | everything, including untouched columns |

**Decision: persist the source.** The deciding argument is not size — it's that a workbook is a thing the user knowingly gave us and can reason about deleting, whereas a bundle is a second, invisible copy in a shape they never saw. Regeneration also makes the retained artifact *auditable*: `session_data/<id>/source/` is a file you can open.

Consequence: **no cell values are written to any database.** `usage.db`'s *"No cell values, ever"* contract ([telemetry.py](app/data/telemetry.py)) is preserved without needing a second database to protect it.

### D2. Persist at the session level, on the filesystem

Reports are *derived*, so they are not the unit of storage. The earlier draft stored one row per report — three rows per session from the A/B/C prefetch, each carrying a duplicate copy of the same `recommendations` and `file_profiles`.

```
session_data/<session_id>/
  source/<original filename>.xlsx    # exact filename preserved — see enabling fact 3
  manifest.json
```

`manifest.json`:

```json
{ "manifest_version": 1, "saved_at": "...", "app_version": "...", "pandas_version": "...",
  "session_id": "...", "client_id": "...",
  "recommendations": { /* the full LLM RecommendationsResponse */ },
  "sheet_selections": { "book.xlsx": ["Sheet1"] },
  "file_profiles": [ ... ],
  "files": [ { "name": "...", "size": 0, "rows": 0, "columns": 0 } ] }
```

**`sheet_selections` is mandatory,** not optional metadata: `add_files(paths, selections)` ([data_loader.py:19](app/data/data_loader.py#L19)) decides which worksheets exist and therefore what the table keys are. Replaying without it loads sheets the original run excluded.

**`pandas_version` is cheap insurance.** A pandas upgrade can shift dtypes or group ordering; recording the version makes a puzzling replay diff diagnosable in one look instead of an afternoon.

**No SQLite, at least not yet.** `session_data/<id>/` already exists as a concept and already holds the debug artifacts. Listing is a `scandir`, deletion is one `rm -rf <id>`, and there are no megabyte blobs in a database. `report_history.py` and `reports.db` are **not being built** — the privacy argument that justified a separate database only existed because the old design put cell values in one. Revisit only if listing or filtering by `client_id` gets slow, which it will not at capstone scale.

Treat the **directory** as the source of truth. A manifest whose `source/` is missing is *expired*, and the listing must render it as unreplayable rather than raising.

### D3. `SAVE_REPORT_HISTORY` read per call, never at import
Copy [report_builder.py:21-34](app/data/report_builder.py#L21-L34) `_debug_files_enabled()`, whose docstring explains why: this module is imported before `load_dotenv()` runs, so a module-level read misses `.env` entirely. It also makes `monkeypatch.setenv` work in tests. **Do not** copy `AI_Engine.py`'s import-time constants — they cannot be changed after import.

Note `api.py` currently has **zero** `os.getenv` calls; the `ADMIN_TOKEN` read will be the first.

### D4. `ADMIN_TOKEN` unset → **404, not 401**
An unconfigured deployment should not advertise that the routes exist. Wrong token with `ADMIN_TOKEN` set → 401.

### D5. Extract `_build_report()` and share the live code path

Do **not** build a second render path. Everything after the cache check in `/api/generate-report` — `generate_report` → `resolve_plotly_axes` → `build_chart_figure` → `build_report_stats` → payload — is [api.py:929-1044](app/api.py#L929-L1044) and is exactly what a replay needs. Lift it verbatim:

```python
def _build_report(session, session_id, report_type):
    """Everything after the cache check: generate_report -> chart -> stats -> payload.
    Returns `stored` (the response payload plus its "data" key)."""
    # api.py:929-1044, moved unchanged
```

```python
def rehydrate_session(session_id):
    """Rebuild a SESSIONS entry from disk. No LLM call."""
    manifest = json.loads((SESSION_DIR / session_id / "manifest.json").read_text(encoding="utf-8"))
    loader = DataLoader()
    loader.add_files(
        sorted(str(p) for p in (SESSION_DIR / session_id / "source").iterdir()),
        manifest.get("sheet_selections"),
    )
    return {
        "files": manifest["files"],
        "status": "complete",
        "file_profiles": manifest["file_profiles"],
        "recommendations": manifest["recommendations"],
        "analysis": manifest["recommendations"],
        "tables": loader.tables(),
    }
```

The admin replay endpoint is then `rehydrate_session(id)` → `_build_report(...)` → return. This is a pure refactor of live code; the existing 380 tests are the safety net.

**Rehydrate *into* `SESSIONS`.** Doing so makes the session genuinely exist again, which dissolves the previous draft's worst frontend hazard: `_resolve_export` ([api.py:1100](app/api.py#L1100)) finds the session, so `ExportPanel` **works** rather than needing to be hidden to stop its on-mount poll of `/api/export/{sid}/status` ([Reportsdashboard.jsx:593-604](app/web/src/components/Reportsdashboard.jsx#L593-L604)).

Still watch the prefetch effect at [App.jsx:157-180](app/web/src/App.jsx#L157-L180): it fires on `sessionId` + `recommendations`. The dev viewer should hold its own local state so App's state is never set and the effect never runs.

### D6. Reproduction, not archival — chosen deliberately

A stored bundle shows **what the user saw last March**. A regenerated report shows **what today's code does with last March's input**. These are different products and the difference is not a detail.

**We are choosing reproduction**, because this is a developer tool. A past session that now renders differently after a refactor is a *finding*, not a viewer bug — the accumulated `session_data/` becomes a free regression corpus. The cost is that this cannot serve as an archive of what a user was shown, and the failure mode is worse: a replay can raise 422 where a bundle always rendered. **The viewer must surface that error, not a blank panel.**

Note what falls out for free: the doc's stated problem is *"reports vanish on refresh"*, and storing bundles never actually fixed that for the user — a rehydrating session does. `?session=<id>` restores the real app. Whether to expose that is a separate decision, out of scope for 3b.

---

## Chunks

Each ends green and committable. Build 17 and 17b first — they are independently useful and testable with curl.

### Chunk 17 — persist the session snapshot
**New:** `app/data/session_store.py`, `tests/test_session_store.py`.
**Modified:** [api.py](app/api.py) (guarded import + call), `.gitignore`, `.env.example`, `tests/conftest.py`.

`save_session_snapshot(session_id, client_id, file_paths, sheet_selections, recommendations, file_profiles, file_metadata)` — copies the uploads and writes the manifest.

**Call it inside the `try`, before the `finally` at [api.py:779-782](app/api.py#L779-L782) deletes the temp dir.** Placing it after `SESSIONS[session_id] = {...}` at [api.py:748-757](app/api.py#L748-L757) is the natural spot: every value the manifest needs is in scope there. Getting this wrong is unrecoverable — once `shutil.rmtree` runs, the source is gone.

Follow the guarded-import idiom already used three times in api.py (`DATA_MODULES_AVAILABLE` / `EXPORT_AVAILABLE` / `TELEMETRY_AVAILABLE` at [71-76](app/api.py#L71-L76)), and wrap the call in a `try/except` the way `track_props` does at [api.py:192](app/api.py#L192) — two independent guards, because history must never fail an analysis.

Behind `SAVE_REPORT_HISTORY`, read per call (D3).

Green when: with the flag on, `session_data/<id>/source/` and `manifest.json` appear and the filename matches the upload byte for byte; with it off, neither does; `pytest` passes.

### Chunk 17b — extract `_build_report()`
**Modified:** [api.py](app/api.py) only.

Pure refactor per D5. No behaviour change, no new tests — the 380 existing ones are the proof.

Green when: `pytest -q` is unchanged at 380 passed.

### Chunk 18 — admin endpoints
`GET /api/admin/sessions` (list: id, saved_at, client, filenames, report names, replayable), `GET /api/admin/sessions/{id}/reports/{letter}` (rehydrate + build), `GET /api/admin/stats` (full event log).

Gate with an `X-Admin-Token` dependency shaped exactly like `client_id` at [api.py:181](app/api.py#L181) — a plain function reading `Header(None)`, wired via `Depends`. Six handlers already use that pattern (e.g. [api.py:861-864](app/api.py#L861-L864)).

**Reuse, don't rewrite:** `GET /api/admin/stats` should call `telemetry.recent_events()` / `recent_files()` / `recent_reports()` ([telemetry.py:368-397](app/data/telemetry.py#L368)). They already parse the `props` JSON column back into dicts and swallow failures — raw SQL would hand back opaque blobs. `recent_events`' own docstring names this endpoint.

Return the same `stored` shape the live endpoint returns, minus `data`, so the frontend has one contract to render.

Green when: verified with curl — list returns, replay of a past session returns a chart, a session with `source/` deleted lists as unreplayable rather than 500ing, wrong token 401, unset `ADMIN_TOKEN` 404.

### Chunk 19 — dev-only frontend
`app/web/src/dev/DevReportBrowser.jsx` behind **two** independent gates:
- `import.meta.env.DEV` — Vite strips the branch from production builds.
- `?dev=1` in the query string (`new URLSearchParams(location.search)` — no router needed).

**This is greenfield: `import.meta.env` has zero occurrences in the codebase today.** There is no existing dev-gating mechanism. Hook into the inline tabs array at [App.jsx:227](app/web/src/App.jsx#L227) (it is a JSX literal, not a hoisted constant).

Token entered once, kept in `sessionStorage`, sent as `X-Admin-Token`. None of the existing helpers in [api.js](app/web/src/api.js) attach that header — they all spread `clientIdHeaders()` — so this needs its own small fetch helper.

Render a 422 from replay as a visible error with the failing report's letter and message (D6). Keep the local-file path: `<input type="file" accept=".json">` → validate the payload shape → same render path, so a report can be handed over without server access. Pair with a download button reusing `triggerDownload` from [export.js:75-86](app/web/src/export.js#L75-L86).

Green when: the production-bundle grep below comes back empty.

---

## Reuse rather than rebuild

| Need | Already exists |
|---|---|
| Per-call env flag | `report_builder._debug_files_enabled()` — [report_builder.py:21-34](app/data/report_builder.py#L21-L34) |
| Session directory creation | `SessionManager` — [session_manager.py](app/data/session_manager.py) |
| Rebuilding tables from files | `DataLoader.add_files` / `.tables()` — [data_loader.py:19](app/data/data_loader.py#L19), [114](app/data/data_loader.py#L114) |
| The whole report build | `_build_report()` after Chunk 17b — [api.py:929-1044](app/api.py#L929-L1044) |
| Never-fails wrapper | `track_props()` — [api.py:192](app/api.py#L192) |
| Admin log reads | `telemetry.recent_events/files/reports` — [telemetry.py:368-397](app/data/telemetry.py#L368) |
| Header dependency | `client_id()` — [api.py:181](app/api.py#L181) |
| Chart rendering | `Plot.jsx` — needs only `{ chart, stats }` |
| Blob download | `triggerDownload` — [export.js:75](app/web/src/export.js#L75) |
| Test session builder | `make_session()` — [test_generate_report_api.py:67-88](tests/test_generate_report_api.py#L67-L88) |
| LLM stub | `stub_llm` — [test_upload_api.py:52-86](tests/test_upload_api.py#L52-L86) |
| Session cleanup | `clean_sessions` autouse — [test_generate_report_api.py:26-30](tests/test_generate_report_api.py#L26-L30) |

Add an autouse fixture to [tests/conftest.py](tests/conftest.py) pointing the session-store root at `tmp_path` and deleting `SAVE_REPORT_HISTORY`, beside the existing `isolated_telemetry_db` — same "whose machine is the suite on" reasoning. Without it the suite writes real `session_data/` directories on the developer's disk.

---

## Verification

1. `pytest -q` — 380 existing pass plus the new ones.
2. With `SAVE_REPORT_HISTORY` **off**, run an analysis → no `session_data/<id>/source/`. Turn it **on** → source file and manifest appear, filename byte-identical to the upload.
3. **Replay equivalence.** Generate report A live, capture the response; restart the server (clearing `SESSIONS`); replay the same session and letter through the admin endpoint. `rows`, `stats` and `chart` must match — only `generated_at` may differ.
4. `curl -H "X-Admin-Token: ..." localhost:8000/api/admin/sessions` returns the list; **wrong token → 401**; **`ADMIN_TOKEN` unset in `.env` → 404**.
5. **Multi-sheet workbook replay.** A session whose recommendations reference `"<sheet> (<stem>).xlsx"` table keys must replay correctly — this is what proves the filename-preservation constraint (enabling fact 3) and `sheet_selections` round-trip.
6. `?dev=1` on the Vite dev server → enter token → open a past report → chart, KPIs, insight rail and data table all render.
7. **Confirm no call to `/api/analyze-full`** in the Network tab — replay must cost zero LLM quota. Also confirm no prefetch loop for B and C.
8. Delete a session's `source/` directory → it lists as unreplayable and opening it shows an error, not a 500 and not a blank panel.
9. `npm run build`, then grep the production bundle for `DevReportBrowser` and confirm it is **absent**. This is what makes "developer only" a build-time fact rather than a convention.
10. Confirm `usage.db` still contains **no cell values**, and that no *new* database was created.
11. Screenshots for the professor via Puppeteer.

---

## Superseded

The earlier draft of this document stored the report payload as a JSON bundle in a new `reports.db` via a new `app/data/report_history.py`. That design is **withdrawn**. Neither the module nor the database was ever built, so there is nothing to migrate.

Why it was withdrawn:

- **It was self-contradictory about what it stored.** §3b.2's bundle format said `"report": /* the exact /api/generate-report response */`, whose `rows` is capped at `MAX_ROWS_RETURNED = 500` ([api.py:996](app/api.py#L996)). But its D5 justified saving server-side *precisely because* `SESSIONS` holds up to `MAX_STORED_ROWS = 5000` ([api.py:1043](app/api.py#L1043)). Both could not be true, and the insert point had both objects in scope.
- **Bundling the 5000-row `data` key would have 500'd the admin endpoint.** `rows` is built by `_json_safe_records` ([api.py:795](app/api.py#L795)), which routes through pandas' JSON writer so `NaN` becomes `null`. `data` is a raw `to_dict(orient="records")` and keeps `NaN`/`NaT`. Starlette 1.3.1 renders with `allow_nan=False`, so returning it raises `ValueError: Out of range float values are not JSON compliant` — on any report with one missing cell. Confirmed empirically. `data` had been safe only because its single reader, the export appendix ([export_builder.py:364](app/data/export_builder.py#L364)), is server-side.
- **It never solved the stated problem.** Storing rows does not make a report survive a refresh for the *user*; only a rehydratable session does.
- **It stored user cell values in a database**, which forced the whole "separate database to protect telemetry's no-cell-values contract" argument. Regeneration removes the premise.

For the record, the row caps are unchanged and still relevant to what the viewer renders: 500 to the browser ([api.py:996](app/api.py#L996)), 5000 into `SESSIONS` ([api.py:1043](app/api.py#L1043)), 200 default into the export appendix ([export_builder.py:48](app/data/export_builder.py#L48)). All three are `.head(n)` — silent truncation, never an exception, at any input size. `report_rows` ([api.py:1004](app/api.py#L1004)) always carries the true count.

---

## Still open

- **Deprecate [scripts/replay_report.py](scripts/replay_report.py) once Chunk 18 lands.** Its two substantial pieces — `WORKSHEET_RE` ([line 39](scripts/replay_report.py#L39)) and `resolve_source_files` ([108-140](scripts/replay_report.py#L108-L140)) — exist *only* to reverse-engineer which workbook a worksheet name came from, because the file was never saved. D1 removes that problem entirely, so nothing in the script is worth carrying forward. **Keep it working until a real session has been replayed end to end through the admin endpoint** — until then it is the only no-LLM path in the repo. Delete it in the same commit that proves the replacement works.
- **Retention.** `session_data/` now accumulates real user workbooks with no pruning. Before this is ever pointed at anything but a dev machine it needs a retention policy and a documented deletion story. Out of scope for 3b; do not let it stay out of scope indefinitely.
- **Frontend event emitters.** `POST /api/events` accepts `report_viewed`, `tab_changed`, `compare_all_opened`, `data_table_toggled`, `report_retry_clicked`, `export_panel_opened` — all whitelisted and tested, but **nothing in the browser sends them yet**. `visit_started` and `user_activated` are the only two actually wired. Adding the rest is small and independent of 3b. The whitelist is a closed set at [api.py:274-290](app/api.py#L274-L290); an unlisted name is silently dropped.
- **Deferred test coverage.** `docs/Test_Usage_PrevLoad_PLAN.md` §1.7 lists what Phase 1 deliberately skipped. The highest-value item left is **formatting-only trailing rows in `.xls`** — a mutation confined to `_probe_xls` still passes the whole suite. One hand-saved `.xls` closes it.
