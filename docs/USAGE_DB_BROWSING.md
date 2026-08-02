# Browsing usage.db in a browser

How to read the usage database as a web page instead of a console table, written
2026-08-02. `scripts/show_usage.py` is still the fastest way to answer "did that
event fire"; this is for the questions it can't answer.

---

## Why this exists

`show_usage.py` prints fixed-width tables, and its `table()` renderer truncates
every cell at 44 characters. That is fine for the `files` and `reports` tables,
whose columns are all short scalars. It is a real limit on `events`, where the
interesting content lives in `props` — a JSON blob per row carrying the LLM
provider, the timings, the pattern tallies, the validation-failure counts. On the
console you see the first 44 characters of it and nothing more, and there is no way
to group, filter or average across it.

Datasette opens the same file as a browsable site: sortable columns, one-click
facets, and an SQL box that can reach inside `props` with `json_extract`.

---

## Install and run

Datasette is in [`requirements-dev.txt`](../requirements-dev.txt), not
`requirements.txt`. The app writes this database through the stdlib `sqlite3`
module and never reads it back through Datasette, so it is tooling, not a runtime
dependency — deploying the app does not install it.

```powershell
ai-env\Scripts\python.exe -m pip install -r requirements-dev.txt
ai-env\Scripts\datasette.exe usage.db --port 8001 -o
```

`-o` opens your browser at <http://127.0.0.1:8001/usage>. Drop it to just start the
server. Ctrl-C stops it.

**Port 8001, not 8000.** The API server owns 8000; running both at once is the
normal case, since the whole point is watching rows appear as you use the app.

**Leave it running while the app runs.** Datasette opens the file read-only, so it
cannot corrupt or lock out a write. It does not poll — refresh the page after an
upload to see new rows.

Don't pass `--immutable`/`-i`. That flag lets Datasette cache row counts because it
has been promised the file will never change, which is exactly the wrong promise
here: you would be reading a snapshot from whenever the server started.

---

## The three tables

Schema is `_DDL` in [`app/data/telemetry.py`](../app/data/telemetry.py#L39).

| Table | One row per | Read it for |
|---|---|---|
| `events` | anything that happened | the timeline; everything specific is in `props` |
| `files` | uploaded file, per analysis | what shapes of data people actually bring |
| `reports` | report **build** attempt | pattern mix, build latency, failures |

Two things about `reports` that will otherwise mislead you: cache hits are **not**
in here (a cached read built nothing, and counting it would deflate every average
in the table), and a failed build still gets a row, with `ok = 0`. So
`count(*)` is attempts, not successes.

Filenames are hashed on the way in. `files.name_hash` is a hash unless
`TELEMETRY_STORE_NAMES` is set in `.env`, in which case rows written while it was
set hold the plaintext name — which is why some rows can look like a name and
others like a digest.

---

## Reading the sheet columns

The `files` table carries two sheet numbers, and the pair is only meaningful read
together:

- **`sheet_count`** — how many worksheets the workbook holds. Recorded before any
  filtering, so it is the file's own size.
- **`sheets_selected`** — how many the sheet picker asked for. Comes from the
  client, not the file.

So `2 / 1` means a two-sheet workbook with one sheet analysed. **`sheets_selected`
NULL is not zero** — it means the client sent no `selections` field at all, i.e.
every sheet was analysed by default. That distinction is the point of the column:
it is what tells you whether the sheet picker is being used at all.

`sheet_count` is NULL for a CSV, which has no worksheets — a different fact from a
one-sheet workbook. `ext` says which you are looking at.

Rows written before 2026-08-02 have NULL `sheet_count` and NULL `kind` regardless
of the file, because the analyze path never passed those values. There is nothing
to backfill them from; they are only recorded going forward.

---

## Queries worth keeping

Paste into the SQL box at <http://127.0.0.1:8001/usage>.

**What the LLM actually did, per analysis** — the `props` unpacking that the console
cannot show:

```sql
select ts,
       json_extract(props, '$.llm_provider')          as provider,
       json_extract(props, '$.llm_ms')                as llm_ms,
       json_extract(props, '$.llm_attempts')          as attempts,
       json_extract(props, '$.validation_failures')   as validation_failures,
       json_extract(props, '$.recommendation_count')  as recs,
       json_extract(props, '$.patterns')              as patterns
from events
where event = 'analysis_completed'
order by ts desc
```

**Is the sheet picker earning its complexity:**

```sql
select ext, kind, sheet_count, sheets_selected, rows, columns, ts
from files
where sheet_count is not null
order by ts desc
```

**Report build cost by pattern** — successes only, so a fast failure can't flatter
the average:

```sql
select pattern,
       count(*)                as builds,
       round(avg(build_ms))    as avg_ms,
       max(build_ms)           as worst_ms,
       sum(is_truncated)       as truncated
from reports
where ok = 1
group by pattern
order by builds desc
```

**Funnel, per browser** — how far each client got:

```sql
select client_id,
       sum(event = 'visit_started')      as visits,
       sum(event = 'files_inspected')    as inspects,
       sum(event = 'analysis_completed') as analyses,
       sum(event = 'report_generated')   as reports
from events
group by client_id
order by reports desc
```

**Everything that went wrong:**

```sql
select ts, event, json_extract(props, '$.error_type') as error_type, props
from events
where event like '%failed%'
order by ts desc
```

---

## Faceting, which is the part worth learning

On any table page, click a column name → **Facet by this**. Datasette adds a
sidebar of that column's distinct values with counts, and each is a filter link.

Faceting `events` by `event` reproduces the "Reports by pattern" breakdown that
[`show_usage.py`](../scripts/show_usage.py#L61) draws by hand — except it works on
any column, on any table, without editing anything. Facet `files` by `ext`, or
`reports` by `pattern` and then `ok`, and you have the same answers in two clicks.

The URL holds the full state, so a filtered, faceted view is a link you can paste
into notes or a report. Add `.json` to any of those URLs for the same data as an
API response, and `.csv` to download it.

---

## Alternatives

Datasette is the one to reach for when you have a question. For the other two
cases:

- **A glance without leaving the editor** — the VS Code extension *SQLite Viewer*
  (`qwtel.sqlite-viewer`). Click `usage.db` in the explorer, get a read-only grid.
  No SQL, no aggregation, and `props` renders as a wall of text.
- **Editing or deleting rows** — [DB Browser for SQLite](https://sqlitebrowser.org)
  (`winget install DBBrowserForSQLite.DBBrowserForSQLite`). Datasette is read-only
  by design, so this is the tool for pruning junk rows from a test run.
- **The `sqlite3` CLI** — `winget install SQLite.SQLite`. Not installed on Windows
  by default, which is the gap `show_usage.py` was written to cover.

---

## If it won't start

| Symptom | Cause |
|---|---|
| `Error: Invalid value for '[FILES]...'` | Run it from the repo root — `usage.db` is a relative path. |
| `no such table` / everything empty | The database is created on the **first telemetry write**. Run an analysis first. |
| Address already in use | Something else has 8001. Use `--port 8002`. |
| Numbers disagree with the app | You are on a different file. `python scripts/show_usage.py` prints the path it read, and `TELEMETRY_DB` overrides it. |
