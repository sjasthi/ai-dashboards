# The developer report browser

Re-open a past analysis on the real Reports page — chart, KPIs, insights and data
table — without re-uploading the spreadsheet and **without spending an LLM call**.

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

Each saved session is one row: its id, the files it was built from, its size, and
when it was saved.

Click **Generate report** and the session opens **on the Reports tab** — the real
one, the same page users read their own reports on. From there the A/B/C switcher,
*Compare all*, the data table and the export panel all work as usual; the other
letters are rebuilt on click. (Hovering *Generate report* lists what each letter
is named, if you want to know before you open it.)

- Rebuilding takes as long as the pandas pipeline takes — usually well under a
  second. **It never calls the model**, so browsing costs nothing.
- **JSON** rebuilds report A and saves the payload to a file without opening it.
- **Open a .json report** loads one of those files back, so a report can be handed
  to someone who has no access to your server. It also opens on the Reports tab.
  Only the letter in the file is available; asking for another says so.
- **Delete** removes the session's directory: the manifest *and* the retained
  workbooks. This is how you forget one run.

A row shows its size on disk and its age, and the bar above shows the total
footprint — the numbers you need to decide what to clear out.

A row whose source files have been deleted is greyed out with *"Source files
deleted"* and its buttons are disabled. The manifest is still listed, so you can
see the session existed.

**Your own session is not lost.** A replay sits *over* the Reports tab rather than
replacing what you were doing: an amber bar names the session you are looking at,
and **Back to my session** returns you to your own analysis with its reports
exactly as you left them. Starting a new upload also drops the replay.

Failures stay on the Dev tab rather than travelling to the Reports page. A 410
(source files gone) or a 422 (the saved recommendation no longer runs against its
own data) is a finding about this pipeline, and it is reported where you clicked.

## 5. Pruning

Retention is manual: **nothing here deletes anything on a timer.** The prune bar
is where you clear space by hand, and it offers three ways to choose what goes.

| Control | What it selects |
|---|---|
| **Delete selected (N)** | exactly the rows you ticked |
| **older than [N] days** | everything saved at least N days ago |
| **keep newest [N]** | everything except the N most recent |

Ticking **"Only sessions whose source files are already gone"** narrows any of the
three to dead manifests — sessions that can no longer be replayed anyway, so
clearing them frees space without losing anything that still worked.

**Every path previews first.** Clicking any of them asks the server what *would*
be deleted and shows you the exact list, each session's size and age, and the
total that would be freed. Nothing is touched until you click **Delete N** on that
panel; **Cancel** backs out. This is deliberately not a yes/no dialog — deleting
the only copy of files someone handed you deserves a list of what is going.

### Rehearsing a retention policy

This is the other reason the age criterion exists. Preview `older than 30 days`
and you are reading **exactly** what a 30-day retention rule would have removed —
which sessions, how many megabytes, and whether anything you still care about
would have been caught. Try a few windows before deciding whether automatic
deletion is worth wiring up, and what the window should be.

The preview's byte count is the same number the real run reports freeing, so the
projection can be trusted as a basis for that decision.

## 6. What it looks like

Exactly like a live report, because it *is* the live report page: the KPI row, the
chart, the computed insight cards, the distribution strip, the full data table, the
provenance strip naming source files and pipeline operations, and the export panel.
The only difference is the amber bar at the top naming the saved session.

That is the point. If the Reports page grows a panel, a replay shows it the same
day — there is no second renderer to keep in step.

## 7. Where the data lives

```
session_data/<session_id>/
  source/<the files exactly as uploaded>
  manifest.json
```

`session_data/` is gitignored. Delete a directory to forget that session; delete
the whole folder to forget everything.

> **This retains real spreadsheets.** Every sheet, every row, every column of what
> was uploaded, until someone deletes it. Nothing expires on its own. That is fine
> on your own machine and is the reason the feature is off by default. See
> [Retention](#retention-is-manual-on-purpose--for-now) before pointing it at
> anything shared.

## 8. Calling the API directly

Everything the UI does is four GETs, a DELETE and a POST. All require
`X-Admin-Token`.

```bash
TOK=your-admin-token

# every saved session, newest first — with bytes, age_days and total_bytes
curl -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions

# one session's full recommendations, straight from its manifest — no workbook
# is opened, so this is cheap. The listing carries only letters and names.
curl -H "X-Admin-Token: $TOK" \
  localhost:8000/api/admin/sessions/20260803_113839_c38cd9

# rebuild one report
curl -H "X-Admin-Token: $TOK" \
  localhost:8000/api/admin/sessions/20260803_113839_c38cd9/reports/A

# the raw event log, not just the aggregates /api/stats returns
curl -H "X-Admin-Token: $TOK" "localhost:8000/api/admin/stats?limit=50"

# forget one session
curl -X DELETE -H "X-Admin-Token: $TOK" \
  localhost:8000/api/admin/sessions/20260803_113839_c38cd9
```

Pruning is a POST, and previews unless told otherwise:

```bash
# what a 30-day retention policy WOULD delete (deletes nothing)
curl -X POST -H "X-Admin-Token: $TOK" -H "Content-Type: application/json" \
  -d '{"older_than_days": 30}' \
  localhost:8000/api/admin/sessions/prune

# actually do it
curl -X POST -H "X-Admin-Token: $TOK" -H "Content-Type: application/json" \
  -d '{"older_than_days": 30, "dry_run": false}' \
  localhost:8000/api/admin/sessions/prune

# other criteria — exactly one per call
-d '{"keep_newest": 20}'
-d '{"session_ids": ["20260803_113839_c38cd9"]}'
-d '{"older_than_days": 30, "unreplayable_only": true}'
```

Passing **no** criterion is a 400, not "everything" — an empty body can never be
a request to empty the store. Passing two is also a 400, because the intersection
would be ambiguous to whoever reads the call later.

Response codes worth knowing:

| Code | Meaning |
|---|---|
| 404 on every admin route | `ADMIN_TOKEN` is not configured. Deliberate — see [D4](#d4-an-unset-admin_token-answers-404-not-401). |
| 401 | `ADMIN_TOKEN` is set; yours is wrong or missing. |
| 404 on one session | Never saved, or deleted. |
| 410 on one session | The manifest is there but `source/` has been deleted. |
| 422 on one report | The saved recommendation no longer executes against its own data. **This is a finding, not a viewer bug** — see [D6](#d6-reproduction-not-archival). |

## 9. Turning it off

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

### Where a replayed report is drawn

On the production Reports page, not here. The dev module fetches, App holds, and
`Reportsdashboard` renders:

```
DevReportBrowser ──fetchSessionDetail──► recommendations ─┐
                 ──fetchReport(id,'A')─► payload ─────────┴─► onLoadSession(...)
                                                                    │
App: setReplay({sessionId, recommendations, reports, fetchReport})   │
                                                                    ▼
                    <ReportsDashboard {...(replay ? replay : live)} />
```

Two things make this safe.

**`replay` is its own state slot.** It is *not* written into `sessionId` /
`recommendations` / `reports`. Those are the dependencies of the background
prefetch effect, and assigning a saved session into them would start building
every remaining letter over HTTP for a session someone only wanted to look at.
Keeping the replay beside them means the effect's dependencies never change, so it
cannot fire — the hazard is removed structurally rather than guarded by a flag.
It also means the live session is untouched, so leaving a replay is `setReplay(null)`
and nothing needs repairing.

**`fetchReport` is handed over, not imported.** App never imports anything from
`src/dev/`; it holds a function reference the dev module passed in. That is what
keeps the build-time guarantee above intact while letting the Reports page's own
letter switcher fetch B and C through the admin route.

The Reports page gained two props for this — `replaySessionId` and `onExitReplay`
— and renders the amber bar only when the second is set. Both are `null` in the
app users run, so the branch is inert there. The bar is not decoration: a replayed
report is otherwise indistinguishable from live data, on a page whose whole premise
is that every claim is labelled with where it came from.

A second renderer used to live in `DevReportBrowser.jsx`. It was always a plainer
view of the same payload, and keeping it meant every panel added to the Reports
page quietly stopped being visible to the one tool built for inspecting reports.
It is gone.

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
  banner on the Dev tab carrying the status code and the server's message. A blank
  panel would hide exactly the information the tool exists to surface.
- **A replayed report must say it is one**, which is what the amber bar is for.

One consequence worth knowing: replay reuses a session already in `SESSIONS`
before rehydrating, so replaying a session that is *currently live* returns the
cached live report rather than rebuilding it. Only sessions the server has
forgotten are genuinely rebuilt.

### Retention is manual, on purpose — for now

`session_data/` accumulates real user workbooks. **Nothing expires them
automatically**, and there is no size cap: the prune controls are the entire
deletion story, and they only run when a person clicks them.

That is a deliberate staging rather than an oversight. Automatic deletion is
irreversible and its right window is a judgement call, so the tooling that makes
the judgement possible comes first:

- the listing reports **size and age per session** plus a total footprint, so the
  cost of keeping things is visible rather than inferred;
- `older_than_days` **dry-runs a policy** — preview at 30 and you are reading
  precisely what a 30-day rule would have taken, before committing to one;
- `unreplayable_only` clears dead manifests, which is the one case where deletion
  is obviously safe.

What is still missing, and what to build if this ever leaves a dev machine:

1. **Something that runs without a person.** The honest options are a sweep on
   startup, a sweep at the end of `save_session_snapshot`, or a scheduled script.
   The middle one is probably right — it runs exactly when growth happens and
   needs no new machinery.
2. **A stated policy** in the README, so "we keep your file for N days" is a claim
   the code backs rather than an assumption.
3. **A deletion path for the person whose data it is.** Everything here is a
   developer deleting on someone's behalf, and only if they can work out which
   session id was theirs.

## Verifying it yourself

| # | Check | How |
|---|---|---|
| 1 | Test suite | `python -m pytest -q` — 97 tests cover the store, pruning and the admin routes |
| 2 | Flag off saves nothing | run an analysis with `SAVE_REPORT_HISTORY` unset → no `session_data/<id>/source/` |
| 3 | Replay equivalence | generate A live, restart the server, replay A → `rows`, `stats` and `chart` identical; only `generated_at` differs |
| 4 | The gate | wrong token → 401; `ADMIN_TOKEN` unset → 404 |
| 5 | Multi-sheet workbook | a session whose keys are `"<sheet> (<stem>).xlsx"` replays correctly |
| 6 | No LLM call, no prefetch | replay with the Network tab open → `GET /api/admin/sessions/<id>` then `…/reports/A`, and **no `POST /api/generate-report`** even after clicking B |
| 7 | Graceful expiry | `rm -rf session_data/<id>/source` → lists as unreplayable, opening it gives 410 on the Dev tab |
| 8 | Not in production | `npm run build`, then grep `dist/` for `DevReportBrowser`, `adminApi`, `X-Admin-Token`, `admin/sessions` → nothing. (`replay-banner` *does* appear — it lives in the Reports page and is inert without `onExitReplay`.) |
| 9 | No new database | `usage.db` is still the only one, and still holds no cell values |
| 10 | Prune previews honestly | dry-run a criterion, note `freed_bytes`, commit it → the same number comes back |
| 11 | Prune can't run away | `POST /api/admin/sessions/prune -d '{}'` → 400, and nothing is deleted |
| 12 | Live session survives a replay | run an analysis, replay a **different** session, then *Back to my session* → title, provenance, KPIs and letters all identical to before |

## Files

| Path | Role |
|---|---|
| [app/data/session_store.py](../app/data/session_store.py) | writes and reads the snapshot; no HTTP, no report logic |
| [app/api.py](../app/api.py) | `save_snapshot`, `_build_report`, `rehydrate_session`, `admin_token`, the five `/api/admin/*` routes |
| [app/web/src/dev/DevReportBrowser.jsx](../app/web/src/dev/DevReportBrowser.jsx) | the session list, pruning, and the hand-off to App — draws no report itself |
| [app/web/src/dev/adminApi.js](../app/web/src/dev/adminApi.js) | the admin fetch helper and token storage |
| [app/web/src/App.jsx](../app/web/src/App.jsx) | the `replay` state slot and `requestReplayReport`, which keep a replay clear of the prefetch effect |
| [app/web/src/components/Reportsdashboard.jsx](../app/web/src/components/Reportsdashboard.jsx) | draws every report, live or replayed; owns the amber `ReplayBanner` |
| [tests/test_session_store.py](../tests/test_session_store.py) | the store and the `/api/analyze-full` hook |
| [tests/test_admin_api.py](../tests/test_admin_api.py) | the gate, replay equivalence, degradation |
| [docs/docs-test-usage-prevload-plan-md-phase-parsed-seal.md](docs-test-usage-prevload-plan-md-phase-parsed-seal.md) | the design document this was built from |
