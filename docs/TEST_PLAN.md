# Test Plan — File Loading Pipeline

| | |
|---|---|
| **Identifier** | TP-001 |
| **Version** | 1.0 |
| **Date** | 2026-08-01 |
| **Scope** | Upload, inspection and loading of `.csv`, `.xls` and `.xlsx` files |
| **Author** | Patrick |

Structure follows IEEE 829, trimmed to what this project actually needs. Sections
that would be ceremony at capstone scale (staffing, training, formal approvals)
are omitted deliberately rather than padded.

---

## 1. Introduction

The upload path was the least-tested part of the application. Before this plan,
searching `tests/` for `inspect`, `analyze-full`, `data_loader`, `workbook_probe`
or `DataLoader` returned **zero matches**: all four existing test files began from
a hand-built `SESSIONS` dictionary, so no test ever read a real spreadsheet.

That is also the part of the app most exposed to whatever a user happens to
upload. This plan closes that gap with a corpus of hand-checked cases driven from
a JSON manifest.

### 1.1 References

- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — the wider plan this is Phase 1 of
- [../project_file_structure.md](../project_file_structure.md) — module layout
- [../tests/fixtures/manifest.json](../tests/fixtures/manifest.json) — the case table itself

---

## 2. Test items

| Item | Location |
|---|---|
| `DataLoader.add_files` / `.tables()` / `.origins` | [../app/data/data_loader.py](../app/data/data_loader.py) |
| `workbook_probe.inspect_file` | [../app/data/workbook_probe.py](../app/data/workbook_probe.py) |
| `POST /api/inspect` | [../app/api.py](../app/api.py) |
| `POST /api/analyze-full` | [../app/api.py](../app/api.py) |

---

## 3. Features tested

- **Extension dispatch** — `.csv` vs `.xls` vs `.xlsx`, alone and mixed in one upload
- **Multi-sheet expansion** — one workbook contributing one table per worksheet
- **Table naming** — the two branches: a bare filename when one sheet survives,
  `"<sheet> (<stem>)<ext>"` when several do
- **Sheet selection** — all, a subset, exactly one, none, and malformed payloads
- **Origin tracking** — mapping a table name back to the file it came from
- **Header handling** — whitespace stripping, and numeric/blank headers surviving untouched
- **Encoding fallback** — the `.csv` decoding ladder
- **Probe/loader row agreement** — the sheet dimensions shown in the UI matching
  the rows the analysis actually loads
- **Graceful degradation** — unreadable files producing a clear message, never a 500

## 4. Features **not** tested

| Not tested | Why |
|---|---|
| LLM recommendation quality | Non-deterministic. The model is stubbed; asserting on its prose would be flaky or vacuous. What *is* asserted is the input the pipeline hands it — see §6.2. |
| Report statistics and chart building | Already covered by `test_report_stats.py` (~100 tests) |
| Export rendering and email delivery | Already covered by `test_export_api.py` |
| Browser rendering | Manual checklist, §9.3 |
| Concurrency / load | Single-worker dev server; out of scope at this stage |

---

## 5. Approach

Three levels, deliberately layered so a failure localises itself.

| Level | What runs | Where |
|---|---|---|
| **Unit** | `DataLoader` + `workbook_probe` directly against fixture files | `tests/test_file_loading.py` |
| **API** | The real routes via FastAPI's `TestClient`, LLM stubbed | `tests/test_upload_api.py` |
| **Manual** | The running UI in a browser | §9.3 |

### 5.1 One definition of each case

Cases live as data in [../tests/fixtures/manifest.json](../tests/fixtures/manifest.json)
and are executed by `run_case()` in
[../tests/loading_checks.py](../tests/loading_checks.py). Both `pytest` and the
demo runner call that same function.

This matters: the report shown in a demo and the suite run in development
**cannot disagree about what a case asserts**, because there is only one
implementation of the assertion. They differ only in presentation.

Adding coverage is therefore a data change, not a code change — drop files in
`tests/fixtures/`, add manifest entries, done.

### 5.2 Fixtures are generated, not committed blobs

[../scripts/make_test_fixtures.py](../scripts/make_test_fixtures.py) builds all
44 fixtures. Generation is **byte-deterministic**: regenerating produces
identical files, so a fixture change always means a real content change and never
clock noise. Two things had to be pinned for that to hold in `.xlsx`, which is a
ZIP archive:

1. every ZIP member's timestamp, which `zipfile` takes from the clock
2. `<dcterms:modified>` in `docProps/core.xml` — openpyxl overwrites it *during*
   `save()`, so setting `wb.properties.modified` beforehand is not enough

Each workbook is written as **both** `.xlsx` and `.xls`, because the two are read
by entirely different code: `.xlsx` through openpyxl, `.xls` through xlrd. They
share only one helper, whose emptiness test has to treat a blank cell as `None`
for one reader and `''` for the other. A case existing in only one format leaves
half of that untested. Writing `.xls` requires `xlwt`, which is dev-only —
nothing in `app/` imports it.

### 5.3 Deliberately: golden cases, not volume

The corpus gives **breadth of shape** — 50 cases over 44 files covering
extensions, sheet topologies, naming hazards, encodings and failure modes — with
every expected value hand-checked. It does not give high N.

That is a conscious trade: **verified expectations over unverified volume.** A
thousand generated files would tell us the code did not crash; it would not tell
us the answers were right. The manifest plus `--record` mode makes growing the
corpus cheap when it is worth doing — add files, re-record, review the diff.

---

## 6. Pass / fail criteria

### 6.1 Per case

A case passes when **all** of the following match the manifest exactly:

- the set of table names produced, and each table's row and column counts
- `origins`, mapping each table back to its uploaded file
- `inspect_file` output per file: kind, total rows, and per-sheet name / rows /
  columns / empty flag
- for cases that pin them, the loaded column names
- the HTTP status code, and a substring of the error detail where one is expected

### 6.2 Probe/loader agreement

The suite's highest-value assertion, because it needs no hand-labelling and
therefore holds for any file added later:

> The row count `workbook_probe` reports must equal the row count `DataLoader`
> loads.

If it does not, the upload screen promises rows the analysis will never deliver.
This is asserted only when no sheet selection is applied — narrowing a selection
makes the two diverge by design (9 rows in the workbook, 6 loaded, for a 2-of-3
subset), so asserting agreement there would be asserting a bug.

### 6.3 Known defects

Two cases record the **correct** behaviour that the code does not yet produce.
They are marked `xfail` with `strict=True`, so if someone fixes the defect the
case passes unexpectedly and pytest reports that as a failure — which is the only
reliable prompt to remove a stale flag. See §8.

---

## 7. Environment

| | |
|---|---|
| OS | Windows 11 (10.0.26200) |
| Python | 3.13.9 |
| pandas | 2.3.3 |
| openpyxl | 3.1.5 (`.xlsx` read/write) |
| xlrd | 2.0.2 (`.xls` read) |
| xlwt | 1.3.0 (`.xls` fixture writing, dev-only) |
| pytest | 9.1.1 |
| Browser | Chrome (manual checks) |

Install with `pip install -r requirements-dev.txt`.

The exact versions are printed in the header of every generated HTML report, so a
result is always attributable to a known set of readers.

### 7.1 Test data

| Source | Contents |
|---|---|
| `tests/fixtures/excel/` | 35 generated workbooks — 18 `.xlsx` and 17 `.xls`, paired except for the formatting-only case (§12) |
| `tests/fixtures/csv/` | 6 CSVs, including four encodings |
| `tests/fixtures/broken/` | 3 unreadable files |
| `datasets/excel tests/` | Real-world workbooks, used for the manual UI checks |
| `scale_50k.xlsx` | 50,000 x 12, gitignored, built with `--scale` |

---

## 8. Defects found

Both were found by this plan, and both trace to the same root cause: the CSV
reader **cannot fail**. Its fallback ladder ends in `latin-1`, which decodes any
byte sequence without raising.

### 8.1 `cp1252` in the encoding ladder is unreachable

The ladder is `['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'cp1252']`, tried in
order. `latin-1` and `iso-8859-1` never raise, so **`cp1252` is dead code**. A
cp1252 file containing `’` or `€` (bytes `0x92` and `0x80`, control codes in
latin-1) decodes silently to control characters instead of the intended
punctuation.

*Severity:* low — mangles some column names.
*Suggested fix:* try `cp1252` before `latin-1`. It is the stricter superset and
does raise on its five undefined bytes.
*Case:* `enc-cp1252-specific-chars`

### 8.2 A corrupt workbook is reported as a readable CSV

`inspect_file` catches the `BadZipFile` from a truncated `.xlsx` and falls back to
a CSV read, which **succeeds** on the binary remains. `/api/inspect` therefore
reports `kind: "csv"`, `rows: 3`, no error. But `DataLoader._add_excel` catches
only `ValueError`, and `BadZipFile` is not one, so the same file raises there and
`/api/analyze-full` returns a generic **502**.

The user is shown a readable 3-row table, and the analysis then dies.

*Severity:* medium — misleading UI, and a 502 where a 400 belongs.
*Suggested fix:* align the exception sets the two paths catch, and make the CSV
fallback refuse input that is obviously not text.
*Case:* `fail-truncated-xlsx`

---

## 9. Demonstration

### 9.1 Automated suite

```bash
pytest -v
```

Expected: **284 passed, 12 skipped, 4 xfailed**. The 12 skips are cases that do
not apply at a given level (an API-only payload has no unit form) plus the
gitignored scale fixture; the 4 xfails are the two defects above, seen at both
levels.

The count includes the suites for usage tracking (`test_telemetry.py`,
`test_stats_api.py`) and the developer report browser (`test_admin_api.py`), which
are not part of this plan's scope but run alongside it.

### 9.2 Report for review

```bash
python scripts/run_test_plan.py --open
```

Writes `test_results/test_report.html` — one self-contained file, no external
assets, openable from disk or by email. It leads with the required Excel matrix,
shows expected beside actual for every case, and prints the library versions used.

Exit code is non-zero if anything failed *or* unexpectedly passed, so it works as
a build gate.

To add fixtures later:

```bash
python scripts/run_test_plan.py --record   # then review the manifest diff
```

`--record` refuses to touch the `xfail` cases: their expectations are the correct
behaviour, so recording over them would erase the bug report and replace it with
the bug.

### 9.3 Manual UI checklist

With the backend and `npm run dev` both running, upload each set from
`datasets/excel tests/` — they map onto the four required cells:

| Cell | Files |
|---|---|
| 1 workbook, 1 sheet | `1 single-single vgchartz-2024.xlsx` |
| 2 workbooks, 1 sheet each | the three `2 multi-single …` files |
| 1 workbook, many sheets | `3 single-multi CRM_sales_opportunities.xlsx` |
| 2 workbooks, many sheets | both `4 multi-multi food supplier …` files |

For each:

1. Sheet checkboxes appear — only for workbooks with more than one sheet
2. Deselect a sheet; the row and column totals update
3. Run the analysis; the recommendations name the tables that were kept
4. Confirm the row counts shown match the file (they are per workbook, not per sheet)

Then the `.xls` path, which no automated UI test covers:
`xls test - vgchartz-2024.xls`.

---

## 10. Deliverables

| Deliverable | Path |
|---|---|
| This plan | `docs/TEST_PLAN.md` |
| Case table | `tests/fixtures/manifest.json` |
| Shared case runner | `tests/loading_checks.py` |
| Unit suite | `tests/test_file_loading.py` |
| API suite | `tests/test_upload_api.py` |
| Fixture generator | `scripts/make_test_fixtures.py` |
| Demo runner | `scripts/run_test_plan.py` |
| Generated report | `test_results/test_report.html` |

---

## 11. Validation of the suite itself

A suite that asserts tautologies passes forever and protects nothing. To show
these tests have teeth, the header-stripping line in `data_loader.py` was
disabled and the suite re-run:

- **exactly 2 cases failed** — `hz-whitespace-headers` in both `.xlsx` and `.xls`
- nothing else failed, so the failure localised rather than cascading
- both `pytest` and `run_test_plan.py` caught it, and the runner exited 1
- restoring the line returned the suite to green

---

## 12. Risks and limitations

| Risk | Note |
|---|---|
| No CI | Nothing runs the suite automatically; it depends on being run by hand. Adding a GitHub Actions workflow is the obvious next step. |
| Corpus breadth, not depth | §5.3. Real-world files can be malformed in ways no fixture anticipates. |
| `xlwt` is unmaintained | Last release 1.3.0 (2017). Verified working on Python 3.13, and the tests needing it are skippable, but it cannot be assumed forever. It writes fixtures only — never used at runtime. |
| `.xls` fixtures are not Excel-written | xlwt is not Excel, so `.xls` cases test xlrd against xlwt's output. The formatting-only-trailing-rows case is `.xlsx` only for this reason. |
| Single-worker assumptions | Sessions live in a module-level dict; nothing here tests multi-worker behaviour. |
| Manual steps are manual | §9.3 depends on a person following it. |
