# How to Use the New Features

Practical instructions for the three things added: the **file-loading test plan**,
**usage tracking with a home page**, and the **developer-only saved report
browser**.

Every command assumes the repo root as the working directory and the virtual
environment activated. On Windows PowerShell that is `.\ai-env\Scripts\Activate.ps1`.

The design reasoning behind all of this lives in [TEST_PLAN.md](TEST_PLAN.md) and
[USAGE_TRACKING.md](USAGE_TRACKING.md). This file is only about how to drive it.

---

## 0. One-time setup

```bash
pip install -r requirements-dev.txt
```

This adds `xlwt`, which writes the `.xls` test fixtures. It is **dev-only** —
nothing in `app/` imports it, and reading `.xls` is xlrd's job.

Nothing else is needed. The test fixtures are committed, and usage tracking uses
`sqlite3` from the standard library.

---

## 1. The test plan

### Run the tests

```bash
pytest -v
```

Expect **284 passed, 12 skipped, 4 xfailed**.

Nothing is wrong when you see those skips and xfails:

| Result | Meaning |
|---|---|
| `skipped` | The case does not apply at that level (a malformed form field has no unit-level form), or its fixture is the gitignored large one. |
| `xfailed` | A **confirmed defect**, with the manifest recording the behaviour that *should* happen. Expected to fail until someone fixes it. |
| `XPASS` (a failure) | A known defect now passes. Good news — go remove its `xfail` flag. |

Useful narrower runs:

```bash
pytest tests/test_file_loading.py -v          # unit level only
pytest tests/test_upload_api.py -v            # through the HTTP endpoints
pytest -k "matrix" -v                         # just the required Excel 2x2
pytest -k "selection" -v                      # sheet-selection behaviour
```

### Produce the report to show someone

```bash
python scripts/run_test_plan.py --open
```

Writes `test_results/test_report.html` and opens it. One self-contained file — no
external assets — so it can be emailed or opened from a USB stick.

It leads with the four required Excel topologies, shows expected beside actual for
every case, and prints the library versions the run used. Exit code is non-zero if
anything failed *or* unexpectedly passed, so it works as a build gate.

```bash
python scripts/run_test_plan.py --group required-matrix   # one section only
python scripts/run_test_plan.py --group encodings --group failure-modes
```

Group names: `required-matrix`, `extensions`, `sheet-selection`, `naming-hazards`,
`structure-hazards`, `encodings`, `failure-modes`, `scale`.

### Demonstrating it live

The walkthrough in [TEST_PLAN.md §9](TEST_PLAN.md#9-demonstration) is written to be
read aloud. In short:

1. `pytest -v` — everything green alongside the pre-existing suite.
2. `python scripts/run_test_plan.py --open` — walk the four required matrix rows.
3. Upload the real workbooks from `datasets/excel tests/` through the UI. Those
   files map onto the same four topologies, so the manual pass mirrors the
   automated one.

To show the tests actually catch regressions, break something on purpose:

```bash
# In app/data/data_loader.py, comment out the header-strip line (~line 60):
#   df.columns = df.columns.map(lambda c: c.strip() if isinstance(c, str) else c)
python scripts/run_test_plan.py --group structure-hazards
```

Exactly two cases fail — `hz-whitespace-headers` in both `.xlsx` and `.xls` — and
nothing else. Restore the line and they pass again.

### Adding your own test files

This is the part designed to be cheap. **You do not write code.**

1. Drop files into `tests/fixtures/excel/`, `tests/fixtures/csv/`, or
   `tests/fixtures/broken/`.
2. Add a case to `tests/fixtures/manifest.json`:

```json
{
  "id": "my-new-case",
  "group": "extensions",
  "description": "what this file is testing, and why it matters",
  "files": ["my_workbook.xlsx"],
  "selections": null,
  "expect": { "status": 200, "tables": {}, "origins": {}, "inspect": null }
}
```

3. Fill in the expected values automatically, then **read the diff**:

```bash
python scripts/run_test_plan.py --record
git diff tests/fixtures/manifest.json
```

`--record` writes down what the code currently does. That is not the same as
checking it is right — the review of that diff is where the expectation actually
gets established. If a recorded number looks wrong, you have found a bug, not a
manifest to update.

`--record` refuses to touch the `xfail` cases, because their expectations are
hand-written descriptions of correct behaviour; recording over them would replace
the bug report with the bug.

### Regenerating the fixtures

Only needed if you edit `scripts/make_test_fixtures.py`:

```bash
python scripts/make_test_fixtures.py
python scripts/make_test_fixtures.py --scale     # also build the 50k-row workbook
```

Generation is byte-deterministic, so `git status` stays quiet unless content really
changed. The `--scale` fixture is gitignored; the case that uses it skips when it is
absent.

---

## 2. Usage tracking and the home page

### Just run the app

```bash
# Terminal 1
uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2
npm --prefix app/web run dev
```

Open <http://localhost:5173>. **Home is now the landing tab.** It lists what the app
can do and shows the usage counters.

Tracking is **on by default and needs no configuration**. The database is created on
first write at `usage.db` in the repo root (gitignored).

Before anyone has used the app the counters are hidden and a short note appears
instead. Upload a file and generate a report, then return to Home — the tiles show
People, Sessions, Files processed and Reports built, plus a daily activity chart.

Hover the chart to read a specific day.

### Check the numbers yourself

```bash
curl http://127.0.0.1:8000/api/stats
```

To prove they persist — the whole reason this uses SQLite rather than a variable in
memory — stop uvicorn, start it again, and re-query. The counts survive.

### Inspecting the database directly

The `sqlite3` command-line tool is **not installed** in this environment, so use
Python:

```bash
python -c "import sqlite3;print(sqlite3.connect('usage.db').execute('select event, count(*) from events group by 1 order by 2 desc').fetchall())"
```

```bash
python -c "import sqlite3;print(sqlite3.connect('usage.db').execute('select ext, kind, rows, columns from files order by id desc limit 10').fetchall())"
```

For something more readable:

```bash
python - <<'EOF'
import json, sqlite3
conn = sqlite3.connect("usage.db")
for row in conn.execute("select ts, event, props from events order by id desc limit 10"):
    print(row[0], row[1])
    for key, value in json.loads(row[2] or "{}").items():
        print(f"    {key} = {value}")
EOF
```

The `analysis_completed` event is the interesting one. Its props include
`llm_provider` and `llm_model` — **which provider actually answered**, not which one
was configured. If `llm_failed_over` is `true`, your primary provider is failing and
requests are silently falling through to the next tier.

### What is and is not stored

Filenames and column names are **hashed** by default. To read them while developing:

```bash
# .env
TELEMETRY_STORE_NAMES=1
```

Cell values are never stored, under any setting. `/api/stats` is public and returns
counts only — no filenames, no identifiers.

To keep the database somewhere else:

```bash
# .env
TELEMETRY_DB=C:/some/other/path/usage.db
```

### If the counters look wrong

Delete the file and start again — it is disposable:

```bash
rm usage.db usage.db-wal usage.db-shm
```

Tests never write here; they redirect to a temporary database, so a full `pytest`
run leaves `usage.db` untouched.

---

## 3. The saved report browser (developer only)

Reopen a report you generated earlier — no re-upload, no second LLM call. Intended
for debugging and QA. **It is not a user feature:** nothing in the UI links to it,
and it does not exist in a production build.

### Turn it on

Two settings, both off by default:

```bash
# .env
SAVE_REPORT_HISTORY=true
ADMIN_TOKEN=pick-a-long-random-string
```

Restart the backend. `SAVE_REPORT_HISTORY` controls whether reports are kept;
`ADMIN_TOKEN` controls whether they can be read back. You need both.

Reports built *before* you enabled the flag were never saved — there is nothing to
recover.

### Open it

<http://localhost:5173/?dev=1>

Paste your `ADMIN_TOKEN` and press Unlock. It is remembered for the browser session.
You then get a table of saved reports; **Open** renders one exactly as the Reports
tab would.

There is no export panel in this view. That is deliberate: exporting needs a live
server session, and a restored report has none.

### Check it with curl

```bash
curl -H "X-Admin-Token: your-token" http://127.0.0.1:8000/api/admin/reports
curl -H "X-Admin-Token: your-token" http://127.0.0.1:8000/api/admin/reports/1
curl -H "X-Admin-Token: your-token" http://127.0.0.1:8000/api/admin/events
```

Expected responses:

| Situation | Response |
|---|---|
| `ADMIN_TOKEN` not set | **404** — an unconfigured server does not admit these routes exist |
| Set, wrong or missing token | **401** |
| Set, correct token | **200** |

So a 404 means "not configured", not "no reports".

### Sharing a report without database access

Every opened report has a **Download bundle** button, producing a `.json` file that
contains everything needed to view it. Anyone can load it back with **Open bundle
file** — no token and no database required.

Useful for handing a broken report to someone else, or attaching one to a bug report.

### Confirming it is absent from production

This is a build-time guarantee, not a convention. To verify:

```bash
npm --prefix app/web run build
grep -r "DevReportBrowser" app/web/dist/ ; echo "exit $? (1 = absent, good)"
```

Nothing should be found. The same holds for `X-Admin-Token`, `api/admin`,
`dev-browser` and `aidash_admin_token`. If any of those ever turn up in `dist/`, the
gating in `App.jsx` has been broken — see the comment above `DevReportBrowser` there
for why the guard has to wrap the `import()` itself.

### Turning it back off

Remove or comment out `SAVE_REPORT_HISTORY` and restart. To delete what was already
saved:

```bash
python -c "import sqlite3;c=sqlite3.connect('usage.db');c.execute('delete from saved_reports');c.commit();print('cleared')"
```

Worth doing when you are finished debugging: unlike the rest of the database, these
bundles contain the actual rows of the user's data. That is exactly why the feature
is off by default.

---

## 4. Two known defects you may hit

Both are recorded as expected failures in the test suite, with the correct behaviour
written down. Neither is fixed, because changing app behaviour was outside the scope
of this work — the one-line fixes are described in
[TEST_PLAN.md §8](TEST_PLAN.md#8-defects-found).

**A CSV saved as Windows-1252 gets mangled.** Column names containing `’` or `€`
come back as control characters. The encoding ladder in `data_loader.py` tries
`latin-1` before `cp1252`, and `latin-1` never rejects any byte, so `cp1252` is
never reached. Workaround: save as UTF-8.

**A corrupt Excel file looks readable, then fails.** Upload a truncated `.xlsx` and
the upload screen reports it as a small CSV; the analysis then fails with a generic
error. The probe falls back to a CSV read that succeeds on binary noise, while the
loader raises. Nothing is lost, but the message is misleading.

---

## 5. Quick reference

| Task | Command |
|---|---|
| Run every test | `pytest -v` |
| Report for review | `python scripts/run_test_plan.py --open` |
| One test group | `python scripts/run_test_plan.py --group required-matrix` |
| Record new expectations | `python scripts/run_test_plan.py --record` then read `git diff` |
| Rebuild fixtures | `python scripts/make_test_fixtures.py [--scale]` |
| Usage counters | `curl http://127.0.0.1:8000/api/stats` |
| Saved reports (dev) | <http://localhost:5173/?dev=1> |
| List saved reports | `curl -H "X-Admin-Token: …" .../api/admin/reports` |
| Reset usage data | `rm usage.db usage.db-wal usage.db-shm` |

| Setting | Default | Does |
|---|---|---|
| `TELEMETRY_DB` | `usage.db` | Where the usage database lives |
| `TELEMETRY_STORE_NAMES` | off | Store filenames unhashed (development only) |
| `SAVE_REPORT_HISTORY` | off | Keep a reopenable copy of every report |
| `ADMIN_TOKEN` | unset | Enables `/api/admin/*`; unset means 404 |

All four are documented in `.env.example`.
