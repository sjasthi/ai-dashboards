# Implementation Plan — Test Plan, Usage Tracking, and Saved Reports

## Context

Three requirements, driven by a professor review and by gaps the codebase currently has:

1. **A test plan.** The upload pipeline is the least-tested part of the app: grepping `tests/` for `inspect`, `analyze-full`, `data_loader`, `workbook_probe`, or `DataLoader` returns **zero hits**. All 4 existing test files start from a hand-built `SESSIONS` dict, so nothing exercises real file reading. The professor wants `.csv` / `.xls` / `.xlsx` covered, with emphasis on a 2x2 Excel matrix (1 vs 2 workbooks x 1 vs multiple sheets), and wants it demonstrated live.
2. **Usage tracking.** There is no persistence at all — `SESSIONS` is a module-level dict at [api.py:124](../app/api.py#L124) that is lost on restart. We need user / file / report counts plus behavioural metadata, for a home page that does not exist yet.
3. **Loading previous reports — a developer tool.** Reports vanish on refresh, and there is no way to inspect what the pipeline produced for a past run. A saved report must be re-openable without re-uploading and without another LLM call. This is for debugging and QA only: it is not exposed to users and does not appear on the home page.

The enabling discovery: **the report payload is already pure JSON and re-rendering needs no server.** `/api/generate-report` returns `chart` (a plain Plotly figure dict), `stats` (scalars/strings/lists), and `rows` (via pandas' JSON writer). [Plot.jsx:11-36](../app/web/src/components/ui/Plot.jsx#L11-L36) draws it with `window.Plotly.newPlot(node, chart.data, buildLayout(...))` — client-side only. So saving the JSON is sufficient to view a report again offline.

**No new runtime dependencies.** `sqlite3` is stdlib; `openpyxl`/`xlrd`/`pandas` are already installed. One new **dev-only** dependency, `xlwt`, for generating `.xls` fixtures (see §1.2).

### Decisions already made
- Test corpus: hand-checked golden cases with exact assertions (manifest-driven, so more files can be added by editing data, not code).
- "Users" = anonymous browser UUID in `localStorage`, sent as `X-Client-Id`.
- **Loading previous reports is a developer tool, not a user feature.** The home page does not list reports. Report history is persisted behind a `SAVE_REPORT_HISTORY` flag and read only through a token-gated admin endpoint plus a dev-only frontend route.
- Home page is user-facing: it outlines what the app can do and shows aggregate usage counters.

### One flagged concern
The professor asked for "a very large range of excel files"; golden-only cases give **breadth of shape** (~30 cases across extensions, sheet topology, encodings, naming hazards, failure modes) but not high N. Phase 1 keeps the case table in a JSON manifest with a `--record` mode, so growing the corpus later is "drop files in a folder, re-record, review the diff" — no code changes. Call this out to the professor as the deliberate trade: verified expectations over unverified volume.

---

## Execution — small chunks with safe stop points

Rate limits are expected to interrupt this work. Every chunk below is sized to leave the repo in a **working, committable state**: tests green, app runnable, no half-applied edit. Stop and report after each one; a hard stop then lands between chunks rather than mid-file.

Note: remaining rate-limit budget is not visible from inside the session, so pacing is by chunk boundary, not by a token estimate. If a usage warning appears, finish the current chunk and stop.

| # | Chunk | Ends green when |
|---|---|---|
| 1 | Phase 0 — copy plan to `docs/` | File exists, links resolve |
| 2 | `tests/conftest.py`, `scripts/make_test_fixtures.py`, manifest with the **4 required matrix cases only** | `pytest` passes |
| 3 | Expand manifest: extensions, mixed batches, sheet selection | `pytest` passes |
| 4 | Expand manifest: naming/structure hazards, encodings, failure modes, scale | `pytest` passes |
| 5 | `tests/loading_checks.py` + `tests/test_file_loading.py` | Unit level green |
| 6 | `tests/test_upload_api.py` | API level green |
| 7 | `scripts/run_test_plan.py` + HTML report | Demo runs end to end |
| 8 | `docs/TEST_PLAN.md` | **Phase 1 complete — major stop point** |
| 9 | `app/data/telemetry.py` + schema + its own unit test (no wiring) | `pytest` passes |
| 10 | `clientId.js`, `X-Client-Id` plumbing, CORS check | App still works |
| 11 | Instrument `/api/inspect` + `/api/analyze-full`, **including provider attribution in `AI_Engine.send_prompt`** | Events land in `usage.db` |
| 12 | Instrument `/api/generate-report`, export, `POST /api/events` | Events land |
| 13 | `GET /api/stats` + tests | **Phase 2 complete — major stop point** |
| 14 | `Homedashboard.jsx` — capabilities content, static | Renders as landing tab |
| 15 | Stat tiles wired to `/api/stats` (load `dataviz` skill first) | **Phase 3a complete** |
| 16 | `saved_reports` table, `SAVE_REPORT_HISTORY`, auto-save insert | Rows appear when flag on |
| 17 | Admin endpoints + token gate + tests | Verified with curl |
| 18 | `DevReportBrowser.jsx` + dev gating + production-bundle absence check | **Phase 3b complete** |

---

## Phase 1 — Test plan and harness

### 1.1 `docs/TEST_PLAN.md`
IEEE-829 derived, trimmed to what a capstone actually needs. Sections:

| Section | Content |
|---|---|
| Identifier & scope | Version, date, what is/isn't covered |
| References | `project_file_structure.md`, this plan |
| Test items | `/api/inspect`, `/api/analyze-full`, `DataLoader`, `workbook_probe` |
| Features tested | Extension dispatch, multi-sheet expansion, sheet selection, table naming, encoding fallback, probe/pandas row agreement, failure handling |
| Features **not** tested | LLM recommendation quality (non-deterministic — stubbed), report math (already covered by `test_report_stats.py`), email delivery |
| Approach | 3 levels: unit (`DataLoader`/`inspect_file`), API (`TestClient`, LLM stubbed), manual UI |
| Pass/fail criteria | Per case: exact table names, row/column counts, sheet lists, HTTP status |
| Environment | Windows 11, Python version, pandas 2.3.3 / openpyxl 3.1.5 / xlrd 2.0.2, Chrome |
| Test data | The fixture corpus + `datasets/excel tests/` |
| Deliverables | This doc, `manifest.json`, automated suites, `test_results/test_report.html` |
| Risks | Client-supplied `client_id`, single-worker server, no CI |
| Demo script | The 3-part walkthrough in §1.5 |

### 1.2 Fixture corpus
`scripts/make_test_fixtures.py` — deterministic generator, `openpyxl` for `.xlsx` and **`xlwt` for `.xls`** (add `xlwt` to `requirements-dev.txt`; dev-only, so no runtime risk). Fixtures are tiny and committed under `tests/fixtures/excel/` so the suite runs on a fresh clone with no setup.

**Why write `.xls` rather than commit binaries:** the `.xls` read path is separate code. `.xlsx` goes through `_probe_xlsx`/openpyxl ([workbook_probe.py:53-67](../app/data/workbook_probe.py#L53-L67)); `.xls` goes through `_probe_xls`/xlrd with `on_demand`, `unload_sheet`, `release_resources` ([workbook_probe.py:70-85](../app/data/workbook_probe.py#L70-L85)). They share only `_extent`, which carries a format-dependent assumption on one line — openpyxl yields `None` for an empty cell, xlrd yields `''` ([workbook_probe.py:45](../app/data/workbook_probe.py#L45)). Generating `.xls` runs the **whole structural matrix below against both readers** instead of giving xlrd a single happy-path file, and it keeps opaque binaries out of git.

Two guards, because `xlwt` is unmaintained (last release 1.3.0, ~2017) and is not Excel:
- Wrap the generated `.xls` cases in `pytest.importorskip("xlwt")`, so the suite still runs green if the library won't install elsewhere. **Smoke-test that `xlwt` imports and writes on the current Python before building on it.**
- **Keep one real Excel-saved `.xls` committed** (trim a small copy of `datasets/excel tests/xls test - vgchartz-2024.xls`) and use it for the formatting-only-trailing-rows case. That bug depends on how the *writing* application fills the dimension record, so a real Excel file is the better oracle there; a generated one risks testing xlwt's quirks instead.

Note BIFF8 caps at 65,536 rows / 256 columns, so the scale case cannot grow past that in `.xls`.

Cases, grouped — each with exact expected values:

**Required 2x2 matrix** (the professor's emphasis; also mapped onto the real files in `datasets/excel tests/`, which already match these four cells)
1. 1 workbook, 1 sheet
2. 2 workbooks, 1 sheet each
3. 1 workbook, multiple sheets
4. 2 workbooks, multiple sheets

**Extension coverage** — `.csv`, `.xls`, `.xlsx` alone; mixed batches `csv+xlsx`, `xls+xlsx`, `csv+xls+xlsx`

**Sheet selection** — all sheets; a subset; exactly one sheet of many (asserts the name *collapses* to the bare filename per [data_loader.py:69](../app/data/data_loader.py#L69)); none selected (expect 400 from [api.py:350](../app/api.py#L350)); malformed `selections` JSON (400); non-list values (400)

**Naming and structure hazards** — these target real code paths, and each runs in **both** `.xlsx` and `.xls` so the openpyxl and xlrd branches are covered symmetrically:
- Sheet name containing parentheses (the `"{sheet} ({stem}){ext}"` format at [data_loader.py:69](../app/data/data_loader.py#L69) is ambiguous; `origins` at [data_loader.py:13](../app/data/data_loader.py#L13) is the documented mitigation — assert `origins` maps back correctly)
- Same sheet name in two different workbooks (uniqueness via stem)
- 31-character sheet name (Excel's limit)
- Unicode / accented sheet names and headers
- Headers with leading/trailing whitespace (stripped per [data_loader.py:60](../app/data/data_loader.py#L60))
- Numeric and blank headers (must **not** be stripped, must not crash)
- Empty sheet among populated ones (skipped)
- Header-only sheet (`empty: true` from [workbook_probe.py:100](../app/data/workbook_probe.py#L100))
- **Formatting-only trailing rows** — the exact failure the `workbook_probe` docstring at [workbook_probe.py:11-21](../app/data/workbook_probe.py#L11-L21) exists to prevent. Assert `inspect_file` rows **equal** `DataLoader` loaded rows. This is the highest-value case in the suite.
- Single column, single data row
- Whitespace-only cell (counts as data — [workbook_probe.py:38](../app/data/workbook_probe.py#L38))

**CSV encodings** — utf-8, utf-8 with BOM, utf-16, cp1252 (asserts the fallback ladder at [data_loader.py:75](../app/data/data_loader.py#L75))

**Failure modes** — zero-byte file (`/api/inspect` returns a per-file `error` and keeps going, [api.py:197](../app/api.py#L197); `/api/analyze-full` returns 400, [api.py:316](../app/api.py#L316)); CSV renamed `.xlsx` (falls back to CSV read, [data_loader.py:40](../app/data/data_loader.py#L40)); truncated/corrupt `.xlsx` (`kind: "unknown"` + `error`, never a 500)

**Scale** — one generated 50k-row x 12-col workbook, gitignored and built on demand, asserting it loads and that probe/loader agree.

### 1.3 Shared check module — write the logic once
`tests/loading_checks.py` — plain importable module (not a test file) holding:
- `load_manifest()` reading `tests/fixtures/manifest.json`
- `run_case(case)` → a result dict (`expected`, `actual`, `passed`, `duration_ms`, `error`)

Both pytest and the demo runner import this, so there is exactly one definition of what each case asserts.

`tests/fixtures/manifest.json` — the case table: id, description, files, `selections`, expected table names, expected rows/columns per table, expected `inspect` sheet lists, expected HTTP status.

### 1.4 Test suites
- `tests/conftest.py` — **new file**; `tests/` currently has no conftest yet [test_export_api.py:28](../tests/test_export_api.py#L28) does `from tests.test_generate_report_api import ...` and relies on rootdir insertion. Adding one fixes that fragility and hosts shared fixtures: `fixture_dir`, `client` (`TestClient`), `cases`.
- `tests/test_file_loading.py` — `@pytest.mark.parametrize` over the manifest, unit level: `DataLoader.add_files` + `tables()` + `origins`, and `workbook_probe.inspect_file`. Includes the probe-vs-loader agreement assertion.
- `tests/test_upload_api.py` — parametrized, API level against `/api/inspect` and `/api/analyze-full`. Stub the LLM by monkeypatching `api.ai_engine.get_validated_recommendations` (mirror the existing monkeypatch pattern in [test_generate_report_api.py:67](../tests/test_generate_report_api.py#L67)) so the suite is fast and deterministic.

### 1.5 Demonstration
`scripts/run_test_plan.py` — iterates `loading_checks.run_case`, prints a live table, and writes a **self-contained** `test_results/test_report.html`: summary header (cases run, passed, duration, library versions) and a matrix of case ID / description / files / expected vs actual / pass-fail. `--record` rewrites the manifest's expected values from observed output so new files can be added and reviewed as a diff.

The live demo, scripted in `docs/TEST_PLAN.md`:
1. `pytest -v` — everything green, including the pre-existing 130+ tests.
2. `python scripts/run_test_plan.py` — open `test_results/test_report.html`, walk the four required matrix rows.
3. Manual UI checklist — upload each of the four real workbook sets from `datasets/excel tests/`, show the sheet checkboxes appearing (only rendered when `sheets.length > 1`, [Uploaddashboard.jsx:541](../app/web/src/components/Uploaddashboard.jsx#L541)), deselect a sheet, confirm the row count updates via `statsFor` ([Uploaddashboard.jsx:159](../app/web/src/components/Uploaddashboard.jsx#L159)), run the analysis.

---

## Phase 2 — Usage tracking

### 2.1 `app/data/telemetry.py` (new)
Stdlib `sqlite3`. DB path from env `TELEMETRY_DB`, default `usage.db` at repo root (add to `.gitignore`).

- `init_db()` — idempotent `CREATE TABLE IF NOT EXISTS`, `PRAGMA journal_mode=WAL`. Call from a FastAPI startup hook in [api.py](../app/api.py).
- `log_event(event, client_id, session_id, props: dict)` — short-lived connection per call (avoids all threadpool/`check_same_thread` problems), `busy_timeout=5000`, and **the entire body wrapped in try/except that swallows and logs**. Telemetry must never be able to fail a user request.
- `record_file(...)`, `record_report(...)`, `stats()`.

### 2.2 Schema
Hybrid: narrow typed tables for the three headline counters (fast, indexable) plus a flexible `events` log so adding a new event never needs a migration.

```sql
events(id, ts, client_id, session_id, event, schema_version, props)   -- props = JSON text
files(id, ts, session_id, client_id, ext, size_bytes, kind, sheet_count,
      sheets_selected, rows, columns, load_ok, error_type, name_hash)
reports(id, ts, session_id, client_id, letter, pattern, chart_type,
        rows_returned, is_truncated, build_ms, ok, error_type, has_schema_warning)
```
Indexes: `events(event)`, `events(ts)`, `files(session_id)`, `reports(session_id)`.

### 2.3 Client identity
- `app/web/src/clientId.js` (new) — `getClientId()` reads or creates `localStorage['aidash_client_id'] = crypto.randomUUID()`.
- Send `X-Client-Id` on every request from [api.js](../app/web/src/api.js) and [Uploaddashboard.jsx](../app/web/src/components/Uploaddashboard.jsx) (both hold their own copy of `API_BASE` — [api.js:3](../app/web/src/api.js#L3) and [Uploaddashboard.jsx:4](../app/web/src/components/Uploaddashboard.jsx#L4); consider collapsing that duplication while here).
- Backend: a small `client_id_header()` FastAPI dependency. **Verify `allow_headers` in the CORS block at [api.py:78-87](../app/api.py#L78-L87) admits `X-Client-Id`.**

### 2.4 Instrumentation points
Existing handlers, small diffs:

| Where | Event | Notable props |
|---|---|---|
| [api.py:159](../app/api.py#L159) `/api/inspect` | `files_inspected` | file count, ext mix, total bytes, per-file probe result + error class |
| [api.py:228](../app/api.py#L228) `/api/analyze-full` | `analysis_started`, `analysis_completed` / `analysis_failed` | ext mix, sheets available vs selected, table count, total rows/cols, relationships detected, prompt chars, **LLM provider + model actually used**, retry count, validation failures, groupby repairs applied, `duration_ms`, patterns recommended, chart types proposed. Plus one `files` row per upload. |
| [api.py:534](../app/api.py#L534) `/api/generate-report` | `report_generated` / `report_failed` | letter, pattern, chart type, rows returned, truncated, `duration_ms`, `is_cache_hit` (the cache hit is at [api.py:582-585](../app/api.py#L582-L585)), schema warning. Plus one `reports` row. |
| [api.py:820](../app/api.py#L820) / [:844](../app/api.py#L844) export | `report_exported`, `report_emailed` | format, letters, appendix on/off, **recipient count only — never addresses** |
| New `POST /api/events` | `report_viewed`, `compare_all_opened`, `data_table_toggled`, `tab_changed`, `report_retry_clicked` | Batched from the browser; cap array length and payload size, whitelist event names |

**Confirmed upstream change — provider attribution (chunk 11).** `send_prompt` loops Gemini → Groq → Groq-fallback → Ollama at [AI_Engine.py:99-109](../app/data/AI_Engine.py#L99-L109) and returns only the text, so today there is no way to know which provider actually answered — logging `AI_BACKEND` would record intent, not reality, and would be silently wrong every time a fallback fired.

Return the winning provider and model alongside the response (or record them on the engine instance) and carry them into the `analysis_completed` event. This also surfaces something currently invisible in normal operation: how often the primary provider is failing over. Keep the change additive so existing callers of `send_prompt` — including `get_validated_recommendations` at [AI_Engine.py:126-180](../app/data/AI_Engine.py#L126-L180) — are unaffected.

### 2.5 What metadata to store — and what not to
Following standard product-analytics conventions: `snake_case`, `object_verb` event names, `is_`/`has_` boolean prefixes, `_ms`/`_at` suffixes, and a `schema_version` on every event so a later change is distinguishable rather than silently mixed in.

Worth storing, grouped by the question it answers:
- **Adoption** — distinct `client_id`, sessions, first-seen/last-seen per client, returning vs new.
- **What data people bring** — extension mix, file size distribution, files per batch, sheets per workbook, how often multi-sheet workbooks appear, **how often users actually deselect a sheet** (this directly measures whether the new feature earns its complexity), row/column magnitudes, CSV encodings encountered.
- **Whether the pipeline works** — success/failure rate per endpoint with error *classes*, LLM provider actually used, retry and validation-failure counts, groupby repairs applied, `duration_ms` percentiles per stage.
- **What the AI produces** — pattern mix (RANKING/DISTRIBUTION/COMPOSITION/TREND/COMPARISON/OUTLIER, [recommendation_requester.py:26-36](../app/data/recommendation_requester.py#L26-L36)), chart-type mix, schema-warning rate.
- **Engagement** — reports viewed vs generated, funnel of upload → analysis → first report → export, time-to-first-report, compare-all usage, export format split, retries.

Deliberately **not** stored: cell values, ever. Filenames and column names are hashed (`sha256[:12]`) by default, with `TELEMETRY_STORE_NAMES=1` to store plaintext during development — shapes and counts answer every question above without holding anyone's data.

### 2.6 `GET /api/stats`
Returns `{users, sessions, files_processed, reports_built, ext_breakdown, pattern_breakdown, daily[]}` from 4-5 aggregate queries. At capstone scale a plain `COUNT` is instant — no caching layer, no counters table.

---

## Phase 3a — Home page (user-facing)

New `app/web/src/components/Homedashboard.jsx`; add `'home'` to the tabs array at [App.jsx:224](../app/web/src/App.jsx#L224) and make it the initial `activeTab` ([App.jsx:9](../app/web/src/App.jsx#L9)).

**No report list here.** The home page's only backend dependency is `GET /api/stats`.

Content:
- **What it is** — one-paragraph hero plus a 3-step "how it works": Upload → AI analysis → Reports.
- **Capabilities**, drawn from what the code actually supports: `.csv` / `.xls` / `.xlsx` with per-sheet selection for multi-sheet workbooks; the six report patterns from [recommendation_requester.py:26-36](../app/data/recommendation_requester.py#L26-L36); the six chart types from [chart_builder.py](../app/data/chart_builder.py) (bar, line, scatter, pie, histogram, box); automatic KPIs, outlier detection and data-quality warnings from [report_stats.py](../app/data/report_stats.py); relationship detection across files; PDF and standalone-HTML export plus email delivery.
- **Live stat tiles** from `/api/stats`: Users, Files processed, Reports built, Sessions — plus a small activity sparkline.
- CTA to the Upload tab.

**Load the `dataviz` skill before writing the stat tiles and sparkline** — it covers stat-tile and KPI-row layout, and the palette must match the Okabe-Ito `COLORWAY` already used at [chart_builder.py:70-73](../app/data/chart_builder.py#L70-L73). Note also that bar charts flip to horizontal past 14-character labels ([chart_builder.py:186-195](../app/data/chart_builder.py#L186-L195)), so any home-page bar chart layout has to follow the axis swap.

---

## Phase 3b — Developer report browser

**Not a user feature.** No entry point in the nav, nothing on the home page, and no user-facing endpoint. This is best understood as productising debug tooling the repo already has: [session_manager.py](../app/data/session_manager.py) writes `session_data/<id>/raw_response.txt` behind `SAVE_DEBUG_FILES`, and [scripts/replay_report.py](../scripts/replay_report.py) already replays a report from that JSON with no LLM call. This makes that trail queryable and viewable.

Because no user ever lists reports, the `client_id`-scoping design is dropped entirely — `client_id` remains a column for debugging attribution, but the only read path is token-gated. One gate, and no "localStorage scoping isn't really security" caveat to explain.

### 3b.1 Persistence
Gated on a new `SAVE_REPORT_HISTORY` env flag, following the `SAVE_DEBUG_FILES` precedent (note `report_builder._debug_files_enabled()` at [report_builder.py:21-34](../app/data/report_builder.py#L21-L34) re-reads its flag per call because of `load_dotenv` ordering — do the same here rather than reading it at import).

At [api.py:696-704](../app/api.py#L696-L704), where the report is already cached into `SESSIONS`, also insert a row. ~15 lines.

```sql
saved_reports(id, ts, client_id, session_id, letter, name, bundle_version, bundle)
```
Index on `saved_reports(ts)`.

**Save server-side, not from the browser.** The client only receives `MAX_ROWS_RETURNED = 500` rows ([api.py:466](../app/api.py#L466)), while `SESSIONS[sid]["reports"][letter]["data"]` holds up to `MAX_STORED_ROWS = 5000` ([api.py:69](../app/api.py#L69)).

### 3b.2 Bundle format
```json
{ "bundle_version": 1, "saved_at": "...", "app_version": "...",
  "session_id": "...", "report_letter": "A", "report_name": "...",
  "report": { /* the exact /api/generate-report response */ },
  "recommendations": { /* the full LLM RecommendationsResponse */ },
  "file_profiles": [ ... ],
  "source": { "files": [{ "name": "...", "ext": "...", "sheets_selected": [], "rows": 0, "columns": 0 }] } }
```

**Is capturing the LLM JSON + the built report sufficient? Yes, for viewing** — `report.chart` + `report.stats` + `report.rows` through the existing [Plot.jsx](../app/web/src/components/ui/Plot.jsx) and [chartLayout.js](../app/web/src/chartLayout.js) `buildLayout` re-renders the exact chart with no server call, no LLM call, and no access to the original spreadsheet. Including `recommendations` costs nothing and future-proofs a "rebuild from source data" path, which is exactly what `replay_report.py` does.

**CSV is the wrong container.** It can only carry the flat `rows`; the chart spec, KPI stats, provenance, and insight text are nested structures that would be lost. JSON for the bundle. (A plain "download this report's data as CSV" button is a separate, user-facing nicety — the Settings tab already advertises a "Raw CSV" option at [Settingsdashboard.jsx:59](../app/web/src/components/Settingsdashboard.jsx#L59) whose handler only calls `alert()`.)

### 3b.3 Endpoints — token-gated
`GET /api/admin/reports` (list: id, ts, session, letter, name), `GET /api/admin/reports/{id}` (full bundle), `GET /api/admin/stats` (full event log).

All require an `X-Admin-Token` header matching `ADMIN_TOKEN` from `.env`. **When `ADMIN_TOKEN` is unset the routes return 404**, not 401 — an unconfigured deployment doesn't advertise that they exist. Add `ADMIN_TOKEN` and `SAVE_REPORT_HISTORY` to `.env.example`.

Build this layer first: it is independently useful and testable with curl or FastAPI's `/docs`.

### 3b.4 Dev-only frontend route
`app/web/src/dev/DevReportBrowser.jsx`, mounted from `App.jsx` behind **two** independent gates:
- `import.meta.env.DEV` — Vite strips the branch from a production build, so the route does not exist in shipped output.
- `?dev=1` in the query string (`new URLSearchParams(location.search)` — no router needed).

Token entered once and kept in `sessionStorage`, sent as `X-Admin-Token`.

**Render from the component's own local state, not App's session state.** `<ReportsDashboard reports={{ A: bundle.report }} recommendations={bundle.recommendations} fileProfiles={bundle.file_profiles} ... />` with the letter remapped to the active slot. This is what makes the phase cheap, and it eliminates both hazards from the earlier design:

- The prefetch effect at [App.jsx:154-177](../app/web/src/App.jsx#L154-L177) fires on `sessionId` + `recommendations`; never setting them means it never runs. No `isRestored` flag, no guard, no App.jsx state surgery.
- `_resolve_export` ([api.py:724](../app/api.py#L724)) 404s on an unknown session, so `POST /api/sessions/restore` is not needed either — pass a prop that hides `ExportPanel` ([Reportsdashboard.jsx:576](../app/web/src/components/Reportsdashboard.jsx#L576)) in the dev viewer. Note `ExportPanel` polls `/api/export/{sid}/status` on mount ([Reportsdashboard.jsx:593-604](../app/web/src/components/Reportsdashboard.jsx#L593-L604)), so it must be hidden rather than merely disabled.

Also accept a local file: `<input type="file" accept=".json">` → validate `bundle_version` → same render path, so a bundle can be handed over without DB access. Pair it with a "download bundle" button reusing `triggerDownload` from [export.js:76-86](../app/web/src/export.js#L76-L86).

---

## Verification

**Phase 1**
- `pytest -v` — new suites pass alongside the existing ~130 tests.
- `python scripts/run_test_plan.py` — open `test_results/test_report.html`; every case green; confirm the four required matrix rows are present and labelled.
- Deliberately break something (e.g. comment out the header-strip at [data_loader.py:60](../app/data/data_loader.py#L60)) and confirm the relevant case goes red — proves the suite has teeth rather than asserting tautologies.
- Manually upload each set from `datasets/excel tests/` through the running UI per the §1.5 checklist.

**Phase 2**
- `curl http://localhost:8000/api/stats` before and after a full upload→report run; confirm all three counters increment.
- **Restart uvicorn and re-query** — counters must survive. This is the whole point of adding SQLite.
- `sqlite3 usage.db "select event, count(*) from events group by 1"` — confirm event names and props look right.
- Confirm no filename or column-name plaintext in the DB with `TELEMETRY_STORE_NAMES` unset.
- Force a failure (upload a corrupt `.xlsx`) and confirm the error class is logged **and** the user-facing response is unchanged.
- Temporarily make `log_event` raise and confirm requests still succeed — telemetry must be non-fatal.

**Phase 3a**
- Home is the landing tab; stat tiles populate from `/api/stats` and match the values `sqlite3` reports.
- With the backend stopped, the page still renders its capabilities content and degrades the tiles gracefully rather than blanking.
- **Confirm there is no path from the home page to a previous report** — no list, no link, no fetch of any admin endpoint. Check the Network tab.

**Phase 3b**
- `curl -H "X-Admin-Token: ..." localhost:8000/api/admin/reports` returns the list; with a wrong token, 401; with `ADMIN_TOKEN` unset in `.env`, **404**.
- With `SAVE_REPORT_HISTORY` off, generate a report and confirm `saved_reports` stays empty; turn it on and confirm a row appears.
- `?dev=1` in the Vite dev server → enter token → open a past report → chart, KPIs, insight rail, and data table all render.
- **Confirm no call to `/api/generate-report`** in the Network tab — not for the opened letter, and not for B or C from the prefetch loop.
- Confirm no export panel is present in the dev viewer, and therefore no `/api/export/{sid}/status` poll.
- `npm run build`, then grep the production bundle for a dev-route marker string (e.g. `DevReportBrowser`) and confirm it is **absent** — this is what makes "developer only" a build-time fact rather than a convention.
- Load a bundle from a local `.json` file with the DB empty and confirm it renders identically.
- Screenshots for the professor via **Puppeteer** (the chrome-devtools MCP does not attach in this environment).

---

## References

- [StickyMinds — IEEE 829-1998 test plan template](https://www.stickyminds.com/article/software-test-plan-template-ieee-829-1998-format-template)
- [Reqtest — How to write a test plan with the IEEE 829 standard](https://reqtest.com/en/knowledgebase/how-to-write-a-test-plan-2/)
- [PostHog — Product analytics best practices](https://posthog.com/docs/product-analytics/best-practices)
- [GA4 for SaaS — the event model, user properties, and common mistakes](https://www.nicelookingdata.com/blog/ga4-saas-tracking-guide)
