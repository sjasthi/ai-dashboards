# Usage Tracking and the Saved Report Browser

Two features that share a database. Neither is discoverable from the UI on
purpose, so this is where they are written down.

| | |
|---|---|
| **Version** | 1.0 |
| **Date** | 2026-08-01 |
| **Code** | [../app/data/telemetry.py](../app/data/telemetry.py), [../app/api.py](../app/api.py) |

---

## 1. What is counted, and what is deliberately not

Everything lives in one SQLite file — `usage.db` at the repo root by default,
gitignored, overridable with `TELEMETRY_DB`. No new dependency: `sqlite3` is
standard library.

### Two rules the design exists to enforce

**Telemetry can never break a request.** Every call goes through
`_track` / `_record_file` / `_record_report` in `api.py`, which absorb anything
thrown, *and* telemetry's own functions swallow their internal errors. Two layers,
because they fail differently: a locked database raises inside the function, while
a mistyped keyword argument raises at the call site before any of that runs. A test
makes every entry point raise and asserts reports still generate.

**Cell values are never stored.** Filenames and column names are hashed to
`sha256[:12]`; set `TELEMETRY_STORE_NAMES=1` in development to keep them readable.
Error *classes* are recorded, never messages, because exception text can quote file
contents. `/api/stats` is public and returns only counts — a test asserts no
filename, client id or session id appears in its response.

### Schema

Three narrow typed tables for the headline counters, plus a general `events` log
with a JSON `props` column so a new event never needs a migration.

| Table | One row per |
|---|---|
| `events` | anything worth recording; `props` is free-form JSON |
| `files` | uploaded file — extension, size, kind, sheet counts, shape, load outcome |
| `reports` | report build attempt — pattern, chart type, rows, duration, outcome |
| `saved_reports` | full report bundle (see §3) — the only table holding user data |

Event names are `object_verb`, `snake_case`, from a whitelist. A name off the list
is stored prefixed `unknown_event:` rather than dropped, so a frontend typo shows
up in the data instead of vanishing.

### Counting people

An anonymous UUID in `localStorage` ([../app/web/src/clientId.js](../app/web/src/clientId.js)),
sent as `X-Client-Id`. Not a login and not a fingerprint: clearing site data makes
a new one, which is the right trade for a usage counter. It authorises nothing —
the admin endpoints are token-gated precisely because this value is editable from
the browser console.

### LLM provider attribution

`AI_Engine.send_prompt` falls back Gemini → Groq → Groq-fallback → Ollama and used
to report only the text, so `AI_BACKEND` recorded *intent* and was wrong every time
a fallback fired. It now takes an optional `attribution` dict and fills in the
provider and model that actually answered, plus `failed_over` and
`failed_providers`. This surfaces something previously invisible in normal
operation: how often the primary provider is failing.

An out-parameter rather than a changed return type, so existing callers are
unaffected; a module-level "last provider" variable would have been simpler and
wrong, since these handlers run in Starlette's threadpool and concurrent analyses
would overwrite each other.

---

## 2. The home page

`GET /api/stats` returns `users`, `sessions`, `files_processed`, `reports_built`,
an extension and pattern breakdown, and a daily series.
[Homedashboard.jsx](../app/web/src/components/Homedashboard.jsx) is the landing tab
and this is the **only** endpoint it calls.

It degrades in two steps: `available: false` (nothing recorded yet) shows the
capability content and a short note; a failed fetch shows the capability content
alone. The page renders with the backend stopped.

**There is no path from the home page to anyone's report.** No list, no link, no
call to `/api/admin/*`.

---

## 3. The saved report browser — developer only

### Why a report can be reopened at all

The report payload is already pure JSON: `chart` is a Plotly figure dict, `stats`
are scalars and strings, `rows` is a list of records. `Plot.jsx` draws it entirely
client-side. So storing that JSON is sufficient to view the report again — no
server call, no model call, and no access to the original spreadsheet.

CSV would be the wrong container: it can carry only the flat `rows`, while the
chart spec, statistics, provenance and insight text are nested. Hence JSON.

### Turning it on

```bash
# .env
SAVE_REPORT_HISTORY=true
ADMIN_TOKEN=<a long random string>
```

Both off by default. With `SAVE_REPORT_HISTORY` unset nothing is written — checked
inside `telemetry.save_report`, not at the call site, so the flag cannot be honoured
in one place and forgotten in another.

Saved **server-side**, at the point where the report is already cached into
`SESSIONS`, because that cache holds up to `MAX_STORED_ROWS` (5000) rows while the
client only ever receives `MAX_ROWS_RETURNED` (500). Saving from the browser would
silently keep the smaller set.

### Reading it

| Endpoint | Returns |
|---|---|
| `GET /api/admin/reports` | listing — id, timestamp, session, letter, name, size. Never the payload: a bundle can be megabytes. |
| `GET /api/admin/reports/{id}` | the full bundle |
| `GET /api/admin/events` | raw event log, including props `/api/stats` aggregates away |

All require `X-Admin-Token`. **With `ADMIN_TOKEN` unset every route returns 404, not
401** — an unconfigured deployment should not confirm the routes exist. A wrong
token against a configured server does get 401, because there the route's existence
is not the secret.

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" localhost:8000/api/admin/reports
```

### The dev route

`http://localhost:5173/?dev=1`, behind **two** independent gates:

- `import.meta.env.DEV` — a build-time constant, so the branch is dead code in a
  production build
- `?dev=1` in the query string

The guard wraps the `import()` expression itself, not just the branch that renders
the component. That distinction is load-bearing: with `lazy()` hoisted to module
scope and only the *branch* guarded, Rollup still code-splits the import and emits
a `DevReportBrowser` chunk into `dist/`. Its CSS is imported by the component for
the same reason, rather than living in the global stylesheet.

Verified after `npm run build` — none of `DevReportBrowser`, `dev-browser`,
`aidash_admin_token`, `X-Admin-Token`, `api/admin` or `Saved report browser` appears
anywhere in `dist/`.

Two details in [DevReportBrowser.jsx](../app/web/src/dev/DevReportBrowser.jsx) that
look incidental and are not:

- It renders from its own local state and never sets App's `sessionId` or
  `recommendations`. App's prefetch effect fires on those, so leaving them alone
  means it never runs — no guard flag needed, and no session-restore endpoint.
- It passes `showExport={false}`. `ExportPanel` polls `/api/export/{id}/status` on
  mount, and a restored bundle has no live session, so the panel must be **absent**
  rather than disabled.

It also opens a bundle from a local `.json` file with no token, so one can be handed
over without database access, and offers the open bundle as a download.

---

## 4. Verification

```bash
pytest tests/test_telemetry.py tests/test_stats_api.py tests/test_admin_api.py -v
```

54 tests. The ones worth knowing about:

- counters survive a restart — the whole reason for SQLite over an in-memory dict
- a cached report is **not** counted twice (the browser prefetches every report, so
  duplicate requests are routine and would otherwise inflate the count)
- a failed report is recorded but not counted as built
- `/api/stats` leaks no filename, client id or session id
- telemetry raising everywhere does not break report generation
- admin routes 404 with no token configured, 401 with a wrong one
- nothing is saved with `SAVE_REPORT_HISTORY` off

Manual checks. The `sqlite3` command-line tool is not installed in this
environment, so these use Python — see
[FEATURE_GUIDE.md](FEATURE_GUIDE.md#inspecting-the-database-directly) for more.

```bash
python -c "import sqlite3; print(sqlite3.connect('usage.db').execute('select event, count(*) from events group by 1').fetchall())"
```

Restart uvicorn and re-query `/api/stats` — the counters must survive.
