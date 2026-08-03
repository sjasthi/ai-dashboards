# The developer report browser

Re-open a past analysis — chart, KPIs, insights and data table — without
re-uploading the spreadsheet and **without spending an LLM call**.

This is a developer tool. There is no nav entry for it in the normal app, no
user-facing endpoint, and the whole thing is stripped from production builds. It
exists because `SESSIONS` is an in-memory dict: reports vanish on refresh, and
until now there was no way to inspect what the pipeline produced for a run that
had already happened.

---

# Part 1 — How to use it

## 1. Turn it on

Two settings in `.env` at the repo root. Both are off by default and both are
documented in `.env.example`.

```ini
# Save each analysis so it can be re-opened later.
SAVE_REPORT_HISTORY=true

# Shared secret for the /api/admin/* routes. Any long random string.
ADMIN_TOKEN=pick-something-long-and-random
```

Restart the API after editing `.env` — it is read once, at import.

```
python -m uvicorn app.api:app --reload --port 8000
```

**Nothing analysed before you switched this on was saved.** Old sessions cannot
be recovered; the uploads they were built from were deleted at the end of their
request. Run a fresh analysis to get something to look at.

## 2. Open the browser

```
cd app/web && npm run dev
```

Then go to **<http://localhost:5173/?dev=1>** — note the query parameter. A
sixth nav tab, **Dev**, appears next to Settings. Without `?dev=1` there is no
tab, and in a production build there is no code behind it at all.

Use `localhost`, not `127.0.0.1`: the Vite dev server binds `::1` only.

## 3. Enter the token

The first screen asks for `ADMIN_TOKEN`. Paste the value from `.env`. It is kept
in `sessionStorage`, so you type it once per browser session and it is gone when
you close the browser. **Forget token** clears it.

If the token screen is followed by an error, the message tells you which problem
you have:

| What you see | What it means |
|---|---|
| *"ADMIN_TOKEN is not set on the server…"* | The server has no `ADMIN_TOKEN`, so the routes answer 404 on purpose. Set it in `.env` and restart. |
| *"That token was rejected."* | The token is wrong. Compare it against `.env` — no quotes, no trailing spaces. |
| *"SAVE_REPORT_HISTORY is off…"* | The routes work, but nothing new is being saved. |

## 4. Browse and replay

Each saved session is one row: its id, the files it was built from, when it was
saved, and a button per recommendation (A, B, C…). Click a letter to rebuild that
report and render it below.

- Rebuilding takes as long as the pandas pipeline takes — usually well under a
  second. **It never calls the model**, so browsing costs nothing.
- **Download JSON** saves the rendered payload to a file.
- **Open a .json report** loads one of those files back, so a report can be handed
  to someone who has no access to your server. It renders through the same path.
- **Delete** removes the session's directory: the manifest *and* the retained
  workbooks. This is how you forget one run.

A row whose source files have been deleted is shown greyed out with *"Source
files deleted"*, and its letter buttons are disabled. The manifest is still
listed, so you can see the session existed.

## 5. What it looks like

The rebuilt report shows a KPI row, the chart, the four insight cards, and the
full data table — plus a provenance strip naming the source files, the pipeline
operations that produced the numbers, and when the rebuild happened.

## 6. Where the data lives

```
session_data/<session_id>/
  source/<the files exactly as uploaded>
  manifest.json
```

`session_data/` is gitignored. Delete a directory to forget that session; delete
the whole folder to forget everything.

> **This retains real spreadsheets.** Every sheet, every row, every column of what
> was uploaded, indefinitely, with no pruning. That is fine on your own machine and
> is the reason the feature is off by default. See
> [Retention](#retention-is-not-solved) before pointing it at anything shared.

## 7. Calling the API directly

Everything the UI does is three GETs and a DELETE. All require
`X-Admin-Token`.

```bash
TOK=your-admin-token

# every saved session, newest first
curl -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions

# rebuild one report
curl -H "X-Admin-Token: $TOK" \
  localhost:8000/api/admin/sessions/20260803_113839_c38cd9/reports/A

# the raw event log, not just the aggregates /api/stats returns
curl -H "X-Admin-Token: $TOK" "localhost:8000/api/admin/stats?limit=50"

# forget a session
curl -X DELETE -H "X-Admin-Token: $TOK" \
  localhost:8000/api/admin/sessions/20260803_113839_c38cd9
```

Response codes worth knowing:

| Code | Meaning |
|---|---|
| 404 on every admin route | `ADMIN_TOKEN` is not configured. Deliberate — see [D4](#d4-an-unset-admin_token-answers-404-not-401). |
| 401 | `ADMIN_TOKEN` is set; yours is wrong or missing. |
| 404 on one session | Never saved, or deleted. |
| 410 on one session | The manifest is there but `source/` has been deleted. |
| 422 on one report | The saved recommendation no longer executes against its own data. **This is a finding, not a viewer bug** — see [D6](#d6-reproduction-not-archival). |

## 8. Turning it off

Remove or set `SAVE_REPORT_HISTORY=false` and restart: nothing further is saved,
and everything already in `session_data/` stays until you delete it. Remove
`ADMIN_TOKEN` as well and the routes stop answering entirely.

---

# Part 2 — How it works

## The problem, and the shape of the answer

The uploaded spreadsheet does not survive its own request. `/api/analyze-full`
writes it to a `tempfile.mkdtemp()` and the `finally` block deletes that
directory ([api.py:582](../app/api.py#L582),
[api.py:826-828](../app/api.py#L826-L828)). The loaded DataFrames stay in memory in
`SESSIONS` so `/api/generate-report` can reach them, and a server restart takes
those with it.

There were two ways to make a past report re-openable:

|  | Store the report output | Persist the source workbook |
|---|---|---|
| What is retained | 500–5000 rows of derived output, ×3 per session | the whole workbook |
| Provenance | an extract the app invented | the file the user handed us |
| Columns retained | only what a report touched | everything |
| What you get back | what the user saw then | what today's code does with that input |

**This implementation persists the source and regenerates the report.** The
deciding argument was not size: a workbook is a thing the user knowingly gave us
and can reason about deleting, whereas a stored bundle is a second, invisible copy
in a shape they never saw. It also keeps the retained artifact *auditable* —
`session_data/<id>/source/` is a file you can open — and it means **no cell values
are written to any database**, so `usage.db`'s "no cell values, ever" contract
needed no second database to protect it.

## Why regeneration is cheap

Three properties of the existing pipeline, all verified rather than assumed:

1. **It is deterministic.** No `datetime.now()`, `date.today()` or `time.time()`
   anywhere in `report_builder`, `chart_builder` or `report_stats`. The only
   wall-clock read on the report path is `generated_at`, set in the endpoint
   itself. Same bytes in, same DataFrame out.
2. **`generate_report` already takes a `tables` dict** — exactly what the live
   endpoint hands it. Rehydration means rebuilding that dict and nothing else.
3. **Table keys are basenames.** `DataLoader.tables()` keys on
   `os.path.basename(name)`, so a saved file can live at any path *as long as its
   filename is preserved exactly*.

Point 3 is why the snapshot copies files under their original names rather than
numbering them. The LLM's recommendations reference those keys; rename
`sales.xlsx` and the replay resolves against nothing.

## The pieces

### `app/data/session_store.py` — what gets written

`save_session_snapshot()` copies the uploads and writes a manifest:

```json
{ "manifest_version": 1, "saved_at": "…", "app_version": "…", "pandas_version": "…",
  "session_id": "…", "client_id": "…",
  "recommendations": { /* the full LLM response, verbatim */ },
  "sheet_selections": { "book.xlsx": ["Sheet1"] },
  "file_profiles": [ … ],
  "files": [ { "name": "…", "size": 0, "rows": 0, "columns": 0 } ] }
```

Two fields carry more weight than they look like they do:

- **`sheet_selections` is mandatory**, not optional metadata.
  `DataLoader.add_files(paths, selections)` decides which worksheets load, and
  therefore what the table keys are. A workbook with two sheets loaded produces
  `"Orders (sales).xlsx"`; the same workbook with one sheet selected produces
  `"sales.xlsx"`. Replay without the selections and the keys change underneath the
  recommendation.
- **`pandas_version` is cheap insurance.** A pandas upgrade can shift dtypes or
  group ordering. Recording the version makes a puzzling replay diff diagnosable
  in one look instead of an afternoon.

**The directory is the source of truth.** A manifest whose `source/` is missing
describes an *expired* session, so `list_sessions()` marks it unreplayable rather
than raising — deleting directories by hand is the intended way to prune, and it
must never break the listing. Directories with no manifest at all (the ones
`SAVE_DEBUG_FILES` leaves behind) are skipped entirely.

### Where the snapshot is taken

Inside `/api/analyze-full`'s `try`, immediately after `SESSIONS[session_id] = {…}`
and **before the `finally` deletes the temp directory**. That is the only point
where every value the manifest needs is in scope and the uploaded bytes still
exist. Getting the ordering wrong is unrecoverable: after `shutil.rmtree` there is
nothing left to copy.

The call is wrapped twice — the `SESSION_STORE_AVAILABLE` import guard, then a
`try/except` in `save_snapshot()` — following the same pattern telemetry uses.
History is a developer convenience layered onto a request a user actually made, so
neither a missing module nor a full disk may turn a completed analysis into a 500.

### `_build_report()` — one render path, not two

The live endpoint's body from `generate_report` through chart, stats and payload
was lifted verbatim into `_build_report(session, session_id, report_type)`. The
replay endpoint calls the same function. This was a pure refactor with no
behaviour change; the existing test suite was the safety net.

The reason for insisting on it: a second render path would drift from the first
within a release, and the browser would then be showing something the app never
produces. `_build_report` returns `(stored, diagnostics)` — `stored` is the
response payload plus a `"data"` key of raw rows for the export appendix, and
`diagnostics` carries chart/stats failure classes for telemetry. `_response_of()`
drops `"data"` on the way out, which is not cosmetic: those rows keep `NaN`/`NaT`,
and Starlette renders with `allow_nan=False`, so returning them raises on any
report with one missing cell.

### `rehydrate_session()` — making a session real again

```
manifest.json ──► recommendations, sheet_selections
source/*      ──► DataLoader.add_files(paths, selections) ──► tables
                  SummaryGenerator.profile_all_files      ──► file_profiles
```

The result is written **into `SESSIONS`**, not kept aside. Doing so makes the
session genuinely exist again, which is what lets `/api/export/{sid}/status` and
the whole export path work on a replayed session instead of needing to be hidden.
Replaying B after A reuses the entry rather than re-reading the workbook.

Note the profiles are *re-derived* rather than read back from the manifest, even
though the manifest holds a copy. The manifest's are plain JSON, and the consumer
on this path (`_axis_granularity`) walks
`profile.columns[].temporal_granularity` as attributes — dicts would silently
yield no granularity and every date axis would replay at day resolution instead of
the dataset's own. Re-profiling is deterministic and costs no model call.

### The gate

`admin_token()` is a plain function reading `Header(None)`, wired with `Depends`
— the same shape as `client_id()`, which six handlers already use. It compares
with `secrets.compare_digest` so the check leaks neither length nor matching
prefix through timing.

It reads `os.environ` per call rather than calling `load_dotenv()` itself. `.env`
reaches the environment as a side effect of importing `AI_Engine` at startup, so
if the data modules fail to import the token looks unset and every admin route
404s — fail-closed, the right direction for a gate.

### The frontend, and why it can't ship

`app/web/src/dev/` sits behind two independent gates:

```js
const DevReportBrowser = import.meta.env.DEV
  ? lazy(() => import('./dev/DevReportBrowser'))
  : null;

const DEV_TAB_ENABLED = !!DevReportBrowser
  && new URLSearchParams(window.location.search).has('dev');
```

Vite substitutes the literal `false` for `import.meta.env.DEV` at build time, the
ternary collapses to `null`, and the dynamic `import()` inside dead code is
eliminated — so the module, the admin API client, and everything they pull in
never reach a production bundle. That makes "developer only" a build-time fact
rather than a convention. `npm run build` emits a single chunk with no separate
dev bundle, and grepping `dist/` for `DevReportBrowser`, `X-Admin-Token`,
`admin/sessions` or `ADMIN_TOKEN` returns nothing.

The admin fetch helper lives in `src/dev/adminApi.js` rather than in `src/api.js`
for the same reason: none of the shared helpers attach `X-Admin-Token`, and
adding them there would ship the admin surface to everyone.

The viewer holds entirely local state and never sets App's `sessionId` or
`recommendations`. That is deliberate — assigning them would trip the background
prefetch effect, which would then build reports B and C over HTTP for a session
someone only wanted to look at.

## Design decisions worth knowing

### D4. An unset `ADMIN_TOKEN` answers 404, not 401

A deployment that never configured these routes should not advertise that they
exist. Once a token *is* configured, a wrong one gets 401, because at that point
the caller already knows.

One honest limitation: FastAPI still lists the routes in `/openapi.json` and
`/docs`. The 404 hides them from someone probing paths, not from someone reading
the generated schema. If that matters for a given deployment, disable the schema
endpoints — it is not something this feature can do on its own.

### D6. Reproduction, not archival

A stored bundle would show **what the user saw last March**. A regenerated report
shows **what today's code does with last March's input**. These are different
products, and the difference is not a detail.

This is a developer tool, so it chooses reproduction. A past session that now
renders differently after a refactor is a *finding*, and the accumulated
`session_data/` becomes a free regression corpus. The costs are real and worth
stating plainly:

- **It cannot serve as an archive** of what a user was shown.
- **A replay can fail where the original succeeded** — hence the visible error
  banner with the failing report's letter and the server's message. A blank panel
  would hide exactly the information the tool exists to surface.

### Retention is not solved

`session_data/` accumulates real user workbooks with no pruning, no expiry and no
size cap. The **Delete** button and `rm -rf session_data/<id>` are the entire
deletion story.

That is acceptable for a capstone running on a developer's machine, and it is why
the flag is off by default. Before this is pointed at anything else it needs a
retention policy and a documented deletion path for the person whose data it is.

## Verifying it yourself

| # | Check | How |
|---|---|---|
| 1 | Test suite | `python -m pytest -q` — 58 tests cover the store and the admin routes |
| 2 | Flag off saves nothing | run an analysis with `SAVE_REPORT_HISTORY` unset → no `session_data/<id>/source/` |
| 3 | Replay equivalence | generate A live, restart the server, replay A → `rows`, `stats` and `chart` identical; only `generated_at` differs |
| 4 | The gate | wrong token → 401; `ADMIN_TOKEN` unset → 404 |
| 5 | Multi-sheet workbook | a session whose keys are `"<sheet> (<stem>).xlsx"` replays correctly |
| 6 | No LLM call | replay with the Network tab open → one GET, no `/api/analyze-full`, no prefetch of B and C |
| 7 | Graceful expiry | `rm -rf session_data/<id>/source` → lists as unreplayable, opening it gives 410 with a visible message |
| 8 | Not in production | `npm run build`, then grep `dist/` for `DevReportBrowser` → nothing |
| 9 | No new database | `usage.db` is still the only one, and still holds no cell values |

## Files

| Path | Role |
|---|---|
| [app/data/session_store.py](../app/data/session_store.py) | writes and reads the snapshot; no HTTP, no report logic |
| [app/api.py](../app/api.py) | `save_snapshot`, `_build_report`, `rehydrate_session`, `admin_token`, the four `/api/admin/*` routes |
| [app/web/src/dev/DevReportBrowser.jsx](../app/web/src/dev/DevReportBrowser.jsx) | the viewer |
| [app/web/src/dev/adminApi.js](../app/web/src/dev/adminApi.js) | the admin fetch helper and token storage |
| [tests/test_session_store.py](../tests/test_session_store.py) | the store and the `/api/analyze-full` hook |
| [tests/test_admin_api.py](../tests/test_admin_api.py) | the gate, replay equivalence, degradation |
| [docs/docs-test-usage-prevload-plan-md-phase-parsed-seal.md](docs-test-usage-prevload-plan-md-phase-parsed-seal.md) | the design document this was built from |
