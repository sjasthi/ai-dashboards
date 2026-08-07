# Progress Update — July 27 to August 3, 2026

A one-week snapshot of what shipped, grouped by area. Each section ends with
**"How to show it"** — the exact steps to demo that feature live.

---

## What was done

- **Reports page** now shows real computed KPIs, charts, a full data table,
  and a provenance strip — not placeholder content.
- **Upload flow** gained a multi-sheet picker for Excel workbooks, a
  file-list bug fix (adding files no longer clears earlier ones), and a
  "Remove all" button.
- **Report reliability**: the pipeline now auto-repairs an AI recommendation
  that's missing its `groupby` field instead of failing the report.
- **Export** can now send a generated report by email, via a Mailtrap.io
  sandbox SMTP inbox.
- **Performance**: all recommended reports now prefetch in the background
  the moment the Analysis page loads, so opening one is instant.
- **Testing infrastructure**: a 316-test automated suite for the file-upload
  pipeline, plus a script that generates a live HTML test report.
- **Usage & metadata tracking**: anonymous telemetry into a local
  `usage.db`, a new Home tab with live usage counters, and a console/browser
  tool for inspecting the data.
- **Developer Report Browser**: past analyses can now be saved and replayed
  on the real Reports page for free — no AI/LLM call — via a hidden Dev tab.

---

## Demo cheat sheet

Quick reference — what to click/run for each feature, in presentation order.

| # | Feature | Command / steps |
|---|---|---|
| 1 | Reports page (KPIs, chart, table) | Upload a file → Analyze → open a report. No command needed. |
| 2 | Multi-sheet upload picker | Upload a multi-sheet `.xlsx` file, show the sheet picker and the "Remove all" button. No command needed. |
| 3 | Groupby auto-repair | `pytest tests/test_groupby_repair.py -v` |
| 4 | Email export (Mailtrap) | Reports page → export panel → **Email**, then show the message land in the Mailtrap sandbox inbox. |
| 5 | Report prefetching | Open browser DevTools → Network tab → land on Analysis page → show reports loading in the background before you click anything. |
| 6 | Automated test suite | `pytest -v` then `python scripts/run_test_plan.py --strict` and open the generated HTML report. |
| 7 | Usage tracking / Home tab | Show the **Home** tab counters, then `python scripts/show_usage.py` for the console view. |
| 8 | Dev Report Browser (replay) | In `.env`: `SAVE_REPORT_HISTORY=true` and `ADMIN_TOKEN=<random string>` → restart uvicorn → run one analysis → visit `http://localhost:5173/?dev=1` → enter token → **Generate report** on a saved session. |

Full setup detail for each is in the numbered sections below.

---

## 1. Reports Page: real stats and a working layout

**Commits:** `populated report stats & reformatted page`, `adjusted bar chart rendering`

The Reports page moved from placeholder content to real computed output: KPI
tiles, an insight/distribution strip, a full sortable data table, and a
provenance strip naming the source files and pipeline steps behind each
number. `report_stats.py` grew from a stub into the module that actually
computes all of it (+900 lines). Bar charts were also fixed to render
correctly regardless of category count or label length.

**How to show it:** Upload any spreadsheet → Analyze → open a report. Point
out the KPI row, the chart, the data table below it, and the provenance strip
naming which files/columns fed the report.

---

## 2. Upload Experience: multi-sheet workbooks and file-list fixes

**Commits:** `added sheet selection for uploads`, `Fixed bug — file list no
longer clears when adding additional files. Added remove all button`

Two related upload-flow improvements:

- **Sheet selection.** Multi-sheet Excel workbooks (`.xls`/`.xlsx`) now show
  a picker so the user chooses which worksheets to include *before* analysis
  runs, instead of silently loading every sheet. Backed by a new
  `workbook_probe.py` that inspects a workbook without fully loading it.
- **File list bug fix.** Adding a second batch of files used to wipe the
  first batch from the upload list — fixed — and a **Remove all** button
  (with a confirmation step) was added for clearing a bad batch quickly.

**How to show it:** On the Upload tab, add a multi-sheet `.xlsx` file and
show the sheet picker. Add a second file afterward to show the list no
longer clears. Use **Remove all** to show the confirmation step.

---

## 3. Report Reliability: automatic repair of LLM output

**Commit:** `added repair for missing groupby field from LLM`

The AI model occasionally proposes a report (e.g., "average sales by
region") without naming the column to group by. Previously this failed the
report outright. `report_builder.py` and `response_validator.py` now detect
a missing `groupby` field and infer a sensible one from the recommendation's
own text/columns before building — with 220 lines of tests covering the
inference logic (`tests/test_groupby_repair.py`).

**How to show it:** Best shown by pointing at `tests/test_groupby_repair.py`
and running it directly:
```
pytest tests/test_groupby_repair.py -v
```

---

## 4. Export: sandboxed email delivery

**Commit:** `added sandbox SMTP using mailtrap.io`

The existing PDF/HTML export gained real email delivery, wired to a
Mailtrap.io sandbox inbox so test emails never risk reaching a real address
during development. `emailer.py` was extended for provider-agnostic SMTP
(works with Mailtrap, Gmail, Brevo, etc. via `.env` config only).

**How to show it:** From a Reports page, use the export panel's **Email**
option, send to the Mailtrap sandbox address, then show the email arriving
in the Mailtrap inbox (not a real mailbox — that's the point of the
sandbox).

---

## 5. Performance: reports build the instant you land on Analysis

**Commit:** `reports now all build on entering the analysis page instead of
waiting for the user to click one by one`

Previously, each report (A/B/C) was only built when its button was clicked —
a 2–3 second wait every time. Now all recommended reports are prefetched in
the background, sequentially, the moment the Analysis page loads, so opening
any of them is instant. No extra AI/LLM calls are involved — prefetching is
pure pandas computation, since the single LLM call already happened during
upload.

**How to show it:** Upload a file, land on the Analysis page, and open the
browser Network tab — report requests fire immediately in the background.
Then click into report B or C and show it opens instantly with no spinner.

---

## 6. Testing Infrastructure: a real automated test suite

**Commits:** `added test coverage for file load pipeline`, `added more test
spreadsheets`, `added new text cases`, `updated usage tracking`

The file-loading pipeline (CSV/XLS/XLSX ingestion, multi-sheet handling,
edge cases) now has a documented test plan (`docs/TEST_PLAN.md`) and an
automated suite: **316 tests passing** across `tests/test_file_loading.py`,
`tests/test_workbook_matrix.py`, `tests/test_inbox_files.py`,
`tests/test_upload_api.py`, backed by a growing corpus of real test
spreadsheets in `tests/data/`. A companion script produces a human-readable
HTML report of the same coverage for live demonstration.

**How to show it — this is the best "wow" moment for the professor:**
```
pytest -v                              # full suite, ~316 tests
python scripts/run_test_plan.py --strict   # generates an HTML report
```
Open the generated HTML report and walk through the test matrix — it shows
every file-type × sheet-selection combination tested, pass/fail, and named
example files (e.g. `employees.xls`, `Q1 (budget_2024).xls`).

---

## 7. Usage & Metadata Tracking

**Commits:** `added metadata tracking`, `updated usage tracking`, `added
home page`

A telemetry layer (`app/data/telemetry.py`) now records anonymous,
aggregate-only usage into a local `usage.db` SQLite database — no cell
values from uploaded spreadsheets are ever stored, only shape/metadata
(file counts, sheet counts, report patterns built, LLM timing, validation
failures). This powers two visible things:

- **A new Home tab** showing live counters — Users, Files processed, Reports
  built — pulled from `GET /api/stats`.
- **`scripts/show_usage.py`**, a console tool for answering "did that event
  fire" without opening a database browser.

For deeper inspection there's also `docs/USAGE_DB_BROWSING.md`, describing
how to browse `usage.db` with Datasette (sortable columns, facets, and raw
SQL) when the console tool's 44-character truncation isn't enough.

**How to show it (metadata / usage tracking):**
```
python scripts/show_usage.py                 # quick console summary
python scripts/show_usage.py --events 50      # recent event log
```
Or, for the visual version: run `pip install -r requirements-dev.txt` once,
then:
```
datasette usage.db --port 8001 -o
```
This opens a browser at `localhost:8001/usage` with sortable/filterable
tables (`events`, `files`, `reports`) — good for showing the professor exact
counts of what shapes of data people uploaded, LLM timings, etc. Also worth
just showing the **Home tab** counters in the running app as the simplest
version of this.

---

## 8. Developer Report Browser: replay past analyses for free

**Commits:** `added dev view`, `Adjustments to dev report loading`

The single biggest feature of the week. Previously, once a browser tab was
refreshed, a generated report was gone — the app holds sessions in memory
only. Now (opt-in, developer-only):

- Every analysis can be **saved** (`SAVE_REPORT_HISTORY=true` in `.env`) —
  the original uploaded files plus the AI's recommendations are kept on
  disk under `session_data/<session_id>/`.
- A hidden **Dev tab** (only reachable via `?dev=1` in the URL, and stripped
  out of production builds entirely) lists every saved session — its files,
  size, and age — and can **replay** any of them on the real Reports page,
  regenerating the report from the saved source data.
- Replaying **never calls the AI model** — it's the same deterministic
  pandas pipeline, so browsing past sessions costs nothing and is
  effectively instant.
- Includes size/age-aware **pruning** tools (delete selected, delete older
  than N days, keep newest N) with a dry-run preview before anything is
  deleted, and admin API routes (`/api/admin/sessions/...`) gated by a
  shared `ADMIN_TOKEN` secret.
- Backed by 97 new tests (`tests/test_admin_api.py`,
  `tests/test_session_store.py`) and a 380-line design doc
  (`docs/DEV_REPORT_BROWSER.md`).

**How to show it — this is the other standout demo:**
1. In `.env`, set:
   ```
   SAVE_REPORT_HISTORY=true
   ADMIN_TOKEN=<any long random string>
   ```
   Restart the API (`python -m uvicorn app.api:app --reload --port 8000`) —
   `.env` is only read at startup.
2. Run an analysis normally first (upload → analyze → generate a report),
   so there's something saved to replay.
3. Go to **`http://localhost:5173/?dev=1`** (must be `localhost`, not
   `127.0.0.1`) — a new **Dev** tab appears.
4. Enter the `ADMIN_TOKEN` value (kept only in `sessionStorage` for that
   browser session).
5. Pick the session just created and click **Generate report** — it opens
   on the real Reports page with an amber banner naming the replayed
   session. Click **Back to my session** to return to the live session
   untouched.
6. Optionally demo pruning: tick **"older than N days"** or **"keep newest
   N"** and show the **preview** step (nothing deletes until confirmed).

---

## Summary table

| Area | What changed | Best demo |
|---|---|---|
| Reports page | Real KPIs, charts, data table, provenance | Open any report |
| Upload | Multi-sheet picker, file-list bug fix, remove-all | Upload multi-sheet workbook |
| Report reliability | Auto-repairs missing LLM `groupby` field | `pytest tests/test_groupby_repair.py -v` |
| Export | Mailtrap sandbox email delivery | Export panel → Email |
| Performance | Reports prefetch on page load | Network tab on Analysis page |
| Testing | 316-test automated suite + HTML report | `python scripts/run_test_plan.py --strict` |
| Usage tracking | Anonymous telemetry, Home tab counters, Datasette browsing | Home tab, or `python scripts/show_usage.py` |
| Dev report browser | Replay any past session for free, no LLM call | `?dev=1` Dev tab |
