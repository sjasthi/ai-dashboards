# Test Plan — Spreadsheet Upload and Loading

**Identifier:** TP-LOAD-001
**Version:** 1.0
**Date:** 2026-08-02
**Author:** Patrick
**Status:** Active

Derived from IEEE 829-1998, trimmed to what this project actually needs.

---

## 1. Scope

The upload pipeline was the least-tested part of the application. Before this
work, grepping `tests/` for `inspect`, `analyze-full`, `data_loader`,
`workbook_probe` or `DataLoader` returned zero hits — all four existing test files
started from a hand-built `SESSIONS` dictionary, so nothing exercised real file
reading at all.

This plan covers **three tests**:

| | Test | Question it answers |
|---|---|---|
| **Test 1** | Extension coverage | Does every `.csv`, `.xls` and `.xlsx` file in the corpus read and load correctly, alone and in mixed batches? |
| **Test 2** | Workbook / sheet matrix | Do `.xlsx` and `.xls` workbooks load correctly across 1-vs-2+ workbooks × 1-vs-many sheets? |
| **Test 3** | Inbox | Does a file a developer just dropped in read and load correctly? No convention, no declared expectations. |

All three are **folder-driven**. The suite walks a directory tree and treats what
it finds as the corpus, so adding coverage means dropping a file in a folder —
there is no manifest to edit and no code to change.

### 1.1 Out of scope

Deliberately not tested here, with the reason:

| Area | Why not |
|---|---|
| LLM recommendation quality | Non-deterministic and costs money. Stubbed at the API level; what is tested is that files reach the pipeline intact. |
| Report mathematics | Already covered by `tests/test_report_stats.py` and `tests/test_groupby_repair.py`. |
| Export and email delivery | Covered by `tests/test_export_api.py`. |
| Sheet selection, CSV encodings, failure modes, constructed naming hazards, 50k-row scale | Deferred. See §9. |

---

## 2. References

- `project_file_structure.md` — module layout
- IEEE 829-1998 — test documentation standard this is derived from

---

## 3. Test items

| Item | Location |
|---|---|
| `DataLoader` | [app/data/data_loader.py](../app/data/data_loader.py) |
| `workbook_probe.inspect_file` | [app/data/workbook_probe.py](../app/data/workbook_probe.py) |
| `POST /api/inspect` | [app/api.py:159](../app/api.py#L159) |
| `POST /api/analyze-full` | [app/api.py:228](../app/api.py#L228) |

---

## 4. Features tested

- Extension dispatch — `.csv` via encoding-detecting read, `.xls`/`.xlsx` via pandas
- Multi-sheet expansion — one table per populated worksheet
- Empty-sheet skipping, and its agreement with the probe's `empty` flag
- Table naming, both branches: `"{sheet} ({stem}){ext}"` and the single-sheet collapse to the bare filename
- Cross-workbook name uniqueness when two workbooks share a sheet name
- `origins` mapping every generated table back to its uploaded file
- **Probe/pandas row agreement** — the row count the upload screen shows must equal the row count the analysis loads
- Header whitespace stripping, with non-string headers left untouched
- Mixed-extension batches
- `/api/inspect` agreeing with the probe, and its per-file error envelope
- `/api/analyze-full` building the same tables, and backfilling per-upload row counts through `origins`

---

## 5. Approach

Three levels, cheapest first:

| Level | What runs | Where |
|---|---|---|
| **Unit** | `DataLoader.add_files` / `tables()` / `origins` and `workbook_probe.inspect_file`, directly | `tests/test_file_loading.py`, `tests/test_workbook_matrix.py`, `tests/test_inbox_files.py` |
| **API** | The same batches over HTTP via `TestClient`, LLM stubbed | `tests/test_upload_api.py` |
| **Manual** | The real UI in a browser | §8.3 |

The checks themselves live in **one** place, `tests/loading_checks.py`, imported by
both pytest and `scripts/run_test_plan.py`. The HTML report and the test suite
therefore cannot disagree about what passed.

The API level runs a representative subset — one batch per matrix cell plus one
mixed batch — rather than the whole corpus. Its job is to prove the HTTP path
agrees with the unit level, and re-uploading 106 files would add minutes while
finding nothing the unit suites do not.

---

## 6. Pass / fail criteria

### 6.1 Invariants

Because the corpus is real developer-supplied files rather than authored fixtures,
expected values are not known when the test code is written. The primary criteria
are therefore properties that must hold for **any** well-formed input. Every case
is checked against all that apply:

| # | Invariant |
|---|---|
| 1 | `inspect_file` returns no `error`, and `kind` matches the extension. A `.xlsx` reporting `kind: "csv"` is a **failure** — it means the workbook did not parse and fell through the CSV rescue path. |
| 2 | `DataLoader.add_files` completes without raising |
| 3 | **Per sheet: probe rows == `len(df)`, probe columns == `len(df.columns)`** |
| 4 | Every non-empty probed sheet produces exactly one table; every `empty` sheet produces none |
| 5 | Table naming follows the rule for its branch |
| 6 | No tables lost to a name collision — the loaded table count equals the number of populated sheets |
| 7 | `origins[table]` is the uploaded filename, for every table |
| 8 | Batch total rows == sum of per-file probe rows |
| 9 | No string column name carries leading or trailing whitespace; non-string headers survive untouched |
| 10 | Every table has at least one row and one column |

**Invariant 3 is the highest-value check in the suite.** It is the entire reason
`workbook_probe` scans cells instead of trusting the stored dimension record:
Excel writes that record over the *used* range, which counts rows holding nothing
but formatting. Without the scan, the upload screen promises a number the analysis
then contradicts.

Test 2 additionally checks the folder's own claim: workbook count (`>= n` for a
`n+wb` cell), single- vs multi-sheet per workbook, and that the naming took the
branch the shape implies.

Every check carries the id of the invariant it belongs to (`INVARIANTS` in
`tests/loading_checks.py`), so the report's closing **"What was checked"** section
is a live rendering of this table — per-invariant check counts, taken from the same
run rather than restated by hand.

That section exists to answer a question a case count cannot: an invariant that
runs **zero** checks is reported as `NOT EXERCISED`, not as a pass. Nothing in the
corpus put it in a position to fail, so its green status is an absence of evidence
— the failure mode §13.1 records, made visible on the day rather than at the next
mutation run. One invariant is currently in that state; see §13.2.

### 6.2 Non-criteria

**Row counts are not compared across formats.** BIFF8 caps `.xls` at 65,536 rows,
so a large CSV converted to `.xls` is legitimately truncated. The report shows
per-format numbers side by side and never asserts they match.

### 6.3 Recorded baseline

`tests/data/baseline.json` pins exact observed values — kind, sheet names, per-sheet
rows and columns, per-table rows and columns — for each single-file case, keyed by
the file's sha256.

| Status | Meaning |
|---|---|
| `recorded` | Checked against exact values; any drift fails |
| `unrecorded` | Invariant-checked only; shown as such in the report |
| `stale` | File changed since recording; the entry describes different bytes, so it is reported rather than failed against |

Drift on a `recorded` file fails **both** paths — `test_matches_recorded_baseline`
and `scripts/run_test_plan.py`, which folds it in as a check named
`matches recorded baseline` and exits non-zero. Before, the runner computed drift
and discarded it, so the HTML could show a green `recorded` chip over a real
regression. Since drift means the sha matched and the numbers moved anyway, the
file is unchanged and the *code* changed, which is never a note.

Workflow for a new file: drop in → run → review the invariant result → `--record`
→ review the JSON diff → commit.

---

## 7. Environment

| | |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| Python | 3.13.9 |
| pandas | 2.3.3 |
| openpyxl | 3.1.5 |
| xlrd | 2.0.2 |
| pytest | 9.1.1 |
| Browser (manual) | Chrome |

No new dependencies were added for this test plan.

---

## 8. Procedure

### 8.1 Automated

```bash
pytest -v                                   # everything, including the pre-existing suites
pytest tests/test_workbook_matrix.py -v     # just the matrix
python scripts/run_test_plan.py --strict    # the demo report
```

Runner flags:

| Flag | Effect |
|---|---|
| `--strict` | A missing or empty **required** corpus is an error, not a skip. Use for the demo. |
| `--record` | Write observed values into the baseline |
| `--only extensions\|workbooks\|inbox` | Run one group |

Output: `test_results/file_loading_report.html` (self-contained, no external
assets) and `test_results/file_loading_report.json`.

The report closes with **"What was checked"** — every check the run emitted, rolled
up per §6.1 invariant, with the properties nothing exercised flagged rather than
left to look like passes (§6.1). The same aggregate is written to the JSON under
`summary`, and the console prints the check total plus a warning naming any
unexercised invariant.

The environment variable `TEST_DATA_ROOT` relocates the corpus — useful for
keeping it outside the repo, and for exercising the missing-corpus path by
pointing it at an empty directory.

### 8.2 Live demonstration

1. `pytest -v` — everything green, including the pre-existing tests.
2. `python scripts/run_test_plan.py --strict` — open the HTML report; walk the four matrix cells for `.xlsx`, then the same four for `.xls`. The generated table names are shown in each cell: `employees.xls` for a single-sheet workbook, `Q1 (budget_2024).xls` for a multi-sheet one.
3. Drop a file into `tests/data/inbox/` and re-run — it appears as a new case with no code change. This is the "how do we add more tests later" answer, demonstrated rather than asserted.
4. The manual UI pass below.

### 8.3 Manual UI checklist

For one workbook set from each matrix cell:

- [ ] Upload through the running app
- [ ] Sheet checkboxes appear for multi-sheet workbooks (only rendered when `sheets.length > 1`, [Uploaddashboard.jsx:541](../app/web/src/components/Uploaddashboard.jsx#L541))
- [ ] The row count shown matches the report's number for that file
- [ ] Deselecting a sheet updates the count via `statsFor` ([Uploaddashboard.jsx:159](../app/web/src/components/Uploaddashboard.jsx#L159))
- [ ] The analysis runs to completion

---

## 9. Deferred

Recorded so it is not lost. Each is scoped against a real code path, so picking one
up later is implementation rather than redesign. Estimated cost: **under 200 KB**
of committed fixtures — space was never the constraint; authoring and verifying
roughly 35 constructed files is.

| Group | Items |
|---|---|
| **Sheet selection** | All sheets; a subset; exactly one of many; none selected (expect 400); malformed `selections` JSON (400); non-list values (400). Needs no new files. |
| **Naming / structure hazards** | Sheet name containing parentheses; unicode and accented names; whitespace, numeric and blank headers; empty sheet among populated; header-only sheet; single column / single row; whitespace-only cell |
| **Empty sheet among populated** | Promoted from the row below: it is the only §6.1 invariant currently reporting `NOT EXERCISED` (§13.2), so it is now the highest-value item on this list. One workbook with a blank sheet beside a populated one closes it. |
| **Formatting-only trailing rows, `.xls`** | Closed as *unreachable*, not as done — see §13.1. The `.xls` twin was built and it does not close the mutation, because xlrd hides the condition from the probe. |
| **CSV encodings** | utf-8, utf-8 BOM, utf-16, cp1252 against the fallback ladder |
| **Failure modes** | Zero-byte file; CSV renamed `.xlsx`; truncated or corrupt `.xlsx` |
| **Scale** | 50k × 12 workbook, gitignored and built on demand |

**The empty-sheet case is now the highest-value item left**, and unlike the `.xls`
one it is genuinely closable by adding a file (§13.2).

The remaining items need *constructed* files. `xlwt` is unmaintained (1.3.0,
~2017), so hand-saving `.xls` from Excel is simpler than generating — though §13.1
is a caution against assuming a faithful hand-saved file necessarily reaches the
code path it was built for.

Some hazards are already covered incidentally by the delivered corpus — see §10.3.

---

## 10. Test data

### 10.1 Layout

```
tests/data/
  extensions/  csv/  xls/  xlsx/          Test 1
  workbooks/
    xlsx/  1wb-1sheet/       <example>/   Test 2 - each <example> is ONE batch
           2+wb-1sheet/      <example>/
           1wb-multisheet/   <example>/
           2+wb-multisheet/  <example>/
    xls/   (the same four cells)
  inbox/                                  Test 3 - untracked except README.md
  baseline.json                           recorded expectations
```

Cell folders parse as `^(\d+)(\+?)wb-(1|multi)sheet$`, where `+` means "at least".
Each batch is filtered to its tree's own format; off-format files are reported as
ignored rather than dropped silently.

### 10.2 Version control

`tests/data/` is **committed** — 12.2 MB, smaller than the already-committed
`datasets/`. That makes the result reproducible on a fresh clone rather than
dependent on one machine.

`tests/data/inbox/` contents are **not** committed, so a developer can drop a large
or private file in without it reaching GitHub. Consequently inbox recordings go to
`tests/data/inbox/baseline.json`, never the committed baseline — pinning
expectations against files nobody else has would fail every other clone. An empty
inbox skips rather than fails, and `--strict` does not demand it.

### 10.3 Hazards the corpus covers incidentally

Present in the delivered files, in both formats, without any constructed fixture:

- **Duplicate sheet names across workbooks in one batch** — four batches: `budget_2024`/`budget_2025` (both `Q1..Q4`), `region_east`/`region_west`, `dept_engineering`/`dept_sales`, `electronics`/`furniture`/`office_supplies`. This exercises invariant 6 and the `({stem})` disambiguator for real.
- **31-character sheet name** (Excel's limit) — `2 multi-single restaurant_db_da`
- **Spaces in sheet names** — `sales teams`, `order details`, `Master Data`, `Opening Stock`
- **Spaces and hyphens in filenames** — `food supplier 01.xlsx`, `Dataset-Bhagwati Store.xlsx`
- **Five sheets in one workbook** — `CRM_sales_opportunities`

One hazard is covered *deliberately* rather than incidentally:

- **Formatting-only trailing rows** — `extensions/xlsx/format_only_trailing.xlsx`, a
  purpose-built file: 4 data rows under a stored dimension of `A1:C11`, the extra
  six carrying borders and no values. It is the only file in the corpus whose stored
  dimension overstates its real extent *as the probe sees it*, and it is what gives
  invariant 3 teeth against the specific bug it was written for (§13.1).
- `extensions/xls/format_only_trailing.xls` is its hand-saved `.xls` twin, carrying
  the same shape — 4 data rows, six formatting-only rows to row 11. It does **not**
  give the `.xls` path the same teeth, for a reason that turned out to be about
  xlrd rather than about the corpus; §13.1 has the detail.

Not covered: scale tops out at 25,000 rows, and **no workbook has an empty sheet
beside a populated one**, which leaves invariant 4's second half unexercised
(§13.2).

### 10.4 Inventory

**107 files, 12.2 MB** — 13 single-file cases (`extensions/csv|xls|xlsx/`) plus 32
workbook batches across the 4 matrix cells, in both `.xls` and `.xlsx`. Per-file
size, sheet names, row counts and sha256 are recorded in `tests/data/baseline.json`
and regenerated on demand by `python scripts/run_test_plan.py --record`; not
duplicated here since it drifts from the actual corpus otherwise.

---

## 11. Deliverables

| Artefact | Path |
|---|---|
| This plan | `docs/TEST_PLAN.md` |
| Shared check module | `tests/loading_checks.py` |
| Unit suites | `tests/test_file_loading.py`, `tests/test_workbook_matrix.py`, `tests/test_inbox_files.py` |
| API suite | `tests/test_upload_api.py` |
| Demo runner | `scripts/run_test_plan.py` |
| Report | `test_results/file_loading_report.html` and `.json` (gitignored) |
| Recorded baseline | `tests/data/baseline.json` |
| Corpus | `tests/data/` |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| A missing corpus makes the suite pass with zero cases | Suites skip with a message naming the folder; `run_test_plan.py --strict` turns it into an error, and that is the mode used for the demo |
| Inbox results are machine-local by design | Accepted — the two required corpora are what `--strict` enforces |
| A file added to the corpus is weaker than the rest until recorded | Shown as `unrecorded` in the report rather than passing silently; `--record` closes it |
| An invariant looks strong but guards nothing, because no corpus file exhibits the failure | The structural risk of this plan. Three defences, in increasing generality: a purpose-built `.xlsx` plus `test_corpus_covers_formatting_only_trailing_rows` for invariant 3; the report's per-invariant check counts, which flag any invariant emitting zero checks on **every** run (§6.1) and currently name one (§13.2); and mutation testing, which is manual and run on demand. **Still live for `.xls` invariant 3**, where §13.1 shows no corpus file can close it. |
| A recorded baseline entry is edited by hand and stops describing the file | Found once (§13.2). The runner now fails on drift rather than discarding it (§6.3), which catches an entry that contradicts the file it names — provided the file is in a group that gets recorded. |
| No CI — tests only run when someone runs them | Accepted at project scale |
| Single-worker dev server | Out of scope for this plan |

---

## 13. Results

Full run, 2026-08-02:

| | |
|---|---|
| Corpus | 107 files, 12.2 MB |
| Cases | 84 (37 single-file, 5 mixed batch, 42 workbook batch) |
| Result | **84 / 84 passed** |
| Checks | **1,782** across 15 properties; 1 invariant not exercised (§13.2) |
| Probe/loader mismatches | **0** |
| Duration | ~30 s (runner), ~19 s (unit sweep alone) |
| pytest | 316 passed, 3 skipped (empty inbox) |

### 13.1 Mutation testing — does the suite actually have teeth?

Each key invariant was verified by deliberately breaking the code and confirming
the right cases went red (every change reverted afterward):

| Mutation | Expected | Result |
|---|---|---|
| Remove the header strip at `data_loader.py:60` | Invariant 9 fails | 2 batches red (`stores`, both formats) |
| Stop subtracting the header row in `_sheet_entry` | Invariant 3 fails everywhere | 42 batches red |
| Remove the `({stem})` disambiguator at `data_loader.py:69` | Invariant 6 fails on duplicate-sheet-name batches | 8 batches red |
| Make `_extent` count formatting-only cells as data | Invariant 3 fails | 2 cases red |
| Make `_probe_xls` trust `sheet.nrows`/`ncols` instead of scanning | Invariant 3 fails | No failures (see below) |

**The `.xlsx` path is covered**: `extensions/xlsx/format_only_trailing.xlsx` (4
data rows under a stored dimension of `A1:C11`) reproduces the exact bug
`workbook_probe` exists to prevent — Excel's dimension record covers the *used*
range, so formatting-only trailing rows still count unless the probe scans cells
itself. `test_corpus_covers_formatting_only_trailing_rows`
([test_file_loading.py](../tests/test_file_loading.py)) asserts the corpus keeps
at least one such file, so this can't silently regress.

**The `.xls` path cannot be closed with a corpus file.** A mutation confined to
`_probe_xls` (trusting xlrd's `sheet.nrows`/`ncols` instead of scanning) survives
the whole suite, even against a faithful hand-saved `.xls` twin of the `.xlsx`
fixture. The reason is in xlrd, not the corpus: `xlrd.open_workbook(path,
on_demand=True)` (what `_probe_xls` uses) already discards formatting-only rows
before `_extent` runs, so stored and scanned extents agree regardless. On this
path, immunity to the bug comes from xlrd's default parsing, not this project's
code — `_extent` still matters for empty-string-cell rows, which xlrd does count,
but no corpus file exhibits that condition either. Closing this would need a unit
test against a synthetic row sequence, not another corpus file.

### 13.2 Known gaps

- **Invariant 4's "empty sheet produces no table" half is unexercised** — no
  corpus file has an empty sheet beside a populated one. Highest-priority item in
  §9; closable by adding one file.
- **`tests/test_workbook_matrix.py` has no baseline test** — only Tests 1 and 3
  do, so the workbook group's recordings aren't drift-checked by pytest between
  runner invocations.
