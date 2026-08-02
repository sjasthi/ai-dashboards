# Test Plan — Spreadsheet Upload and Loading

**Identifier:** TP-LOAD-001
**Version:** 1.0
**Date:** 2026-08-02
**Author:** Patrick
**Status:** Active

Derived from IEEE 829-1998, trimmed to what this project actually needs. Companion
to [Test_Usage_PrevLoad_PLAN.md](Test_Usage_PrevLoad_PLAN.md), which holds the
design rationale; this document is the plan of record.

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

- [Test_Usage_PrevLoad_PLAN.md](Test_Usage_PrevLoad_PLAN.md) — design rationale, Phase 1
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

Verified 2026-08-02. `sha256` is the first 12 hex digits.

**107 files, 12.2 MB.**

| File | Bytes | Kind | Sheets | Rows | sha256 |
|---|---:|---|---|---:|---|
| `extensions/csv/accounts.csv` | 4,670 | csv | — | 85 | `e5242324768a` |
| `extensions/csv/behavior.csv` | 969,783 | csv | — | 25,000 | `f39729ffd9cc` |
| `extensions/csv/colors.csv` | 3,669 | csv | — | 135 | `1aa4c2a394c9` |
| `extensions/csv/customers.csv` | 1,366,176 | csv | — | 25,000 | `024566f26b0c` |
| `extensions/csv/data_dictionary.csv` | 996 | csv | — | 21 | `22b34e498d07` |
| `extensions/csv/inventories.csv` | 185,752 | csv | — | 11,681 | `c3bc331a60ea` |
| `extensions/csv/inventory_sets.csv` | 44,754 | csv | — | 2,846 | `041403317cfa` |
| `extensions/csv/part_categories.csv` | 1,255 | csv | — | 57 | `83fbfd83609b` |
| `extensions/csv/products.csv` | 171 | csv | — | 7 | `7c1c8cbbdb6d` |
| `extensions/csv/sales_pipeline.csv` | 637,773 | csv | — | 8,800 | `825ce8f6c32d` |
| `extensions/csv/sales_teams.csv` | 1,284 | csv | — | 35 | `aeff1272ebe1` |
| `extensions/csv/sets.csv` | 519,188 | csv | — | 11,673 | `76c8ff6bed1a` |
| `extensions/csv/themes.csv` | 11,956 | csv | — | 614 | `7b0c165b3a1c` |
| `extensions/xls/accounts.xls` | 13,824 | excel | accounts | 85 | `ae9617653676` |
| `extensions/xls/colors.xls` | 17,920 | excel | colors | 135 | `e4e8bad3bf0f` |
| `extensions/xls/data_dictionary.xls` | 5,632 | excel | data_dictionary | 21 | `4696c7adb861` |
| `extensions/xls/format_only_trailing.xls` | 26,112 | excel | Sheet1 | 4 | `82e22579349e` |
| `extensions/xls/inventories.xls` | 777,216 | excel | inventories | 11,681 | `c826bc269302` |
| `extensions/xls/inventory_sets.xls` | 203,776 | excel | inventory_sets | 2,846 | `a29145e1ec07` |
| `extensions/xls/part_categories.xls` | 9,728 | excel | part_categories | 57 | `eecd8520a1c3` |
| `extensions/xls/products.xls` | 5,632 | excel | products | 7 | `644e55a49162` |
| `extensions/xls/sales_pipeline.xls` | 1,239,552 | excel | sales_pipeline | 8,800 | `c39b3e4ce736` |
| `extensions/xls/sales_teams.xls` | 9,728 | excel | sales_teams | 35 | `816d32affb79` |
| `extensions/xls/sets.xls` | 1,301,504 | excel | sets | 11,673 | `52cb42f69c49` |
| `extensions/xls/themes.xls` | 46,592 | excel | themes | 614 | `afbe504d6ad1` |
| `extensions/xlsx/accounts.xlsx` | 8,928 | excel | Sheet1 | 85 | `12aa19c2ddc8` |
| `extensions/xlsx/colors.xlsx` | 8,741 | excel | Sheet1 | 135 | `1ee1df0e1877` |
| `extensions/xlsx/data_dictionary.xlsx` | 5,542 | excel | Sheet1 | 21 | `c9a6d427a103` |
| `extensions/xlsx/format_only_trailing.xlsx` | 8,738 | excel | Sheet1 | 4 | `051afad52bdf` |
| `extensions/xlsx/inventories.xlsx` | 225,001 | excel | Sheet1 | 11,681 | `bfb24d3bd9b5` |
| `extensions/xlsx/inventory_sets.xlsx` | 52,104 | excel | Sheet1 | 2,846 | `c7e1af1bd89a` |
| `extensions/xlsx/part_categories.xlsx` | 6,072 | excel | Sheet1 | 57 | `3d8c16e69f59` |
| `extensions/xlsx/products.xlsx` | 5,031 | excel | Sheet1 | 7 | `2ac3004d09a6` |
| `extensions/xlsx/sales_pipeline.xlsx` | 406,858 | excel | Sheet1 | 8,800 | `afebdada75cc` |
| `extensions/xlsx/sales_teams.xlsx` | 5,786 | excel | Sheet1 | 35 | `dc442fab4470` |
| `extensions/xlsx/sets.xlsx` | 414,091 | excel | Sheet1 | 11,673 | `69dd9acbbc3b` |
| `extensions/xlsx/themes.xlsx` | 17,785 | excel | Sheet1 | 614 | `4a7c9e914e82` |
| `workbooks/xls/1wb-1sheet/example_1_employees/employees.xls` | 9,728 | excel | Employees | 25 | `0e5be976aa3e` |
| `workbooks/xls/1wb-1sheet/example_2_products/products.xls` | 5,632 | excel | Products | 20 | `0621b35ec001` |
| `workbooks/xls/1wb-1sheet/example_3_sales_transactions/sales_transactions.xls` | 9,728 | excel | Transactions | 40 | `5d4bacfab3f5` |
| `workbooks/xls/1wb-1sheet/example_4_weather_observations/weather_observations.xls` | 9,728 | excel | Weather | 30 | `8f6ace09c3f6` |
| `workbooks/xls/1wb-multisheet/crm/CRM_sales_opportunities.xls` | 1,152,512 | excel | data_dictionary, accounts, sales teams, products, sales pipeline | 8,948 | `79a8bf023a2e` |
| `workbooks/xls/1wb-multisheet/example_1_company_report/company_report.xls` | 13,824 | excel | Revenue, Expenses, Employees | 99 | `810f2907e6e3` |
| `workbooks/xls/1wb-multisheet/example_2_sales_dashboard/sales_dashboard.xls` | 13,824 | excel | Sales, Products, Regions | 65 | `03929ff082d8` |
| `workbooks/xls/1wb-multisheet/example_3_school_records/school_records.xls` | 13,824 | excel | Students, Grades, Teachers | 110 | `cb26795fb530` |
| `workbooks/xls/1wb-multisheet/example_4_inventory_system/inventory_system.xls` | 9,728 | excel | Products, Suppliers, Stock | 46 | `9590fb8ca31e` |
| `workbooks/xls/1wb-multisheet/fastfood/fastfood.xls` | 50,688 | excel | bk, mcd | 264 | `2bec72878c71` |
| `workbooks/xls/1wb-multisheet/stores/Dataset-Bhagwati Store.xls` | 175,616 | excel | Master Data, Opening Stock, Sales Data | 2,232 | `e796be67a1e5` |
| `workbooks/xls/2+wb-1sheet/example_1_crm/customers.xls` | 9,728 | excel | Customers | 25 | `550dd8725d2c` |
| `workbooks/xls/2+wb-1sheet/example_1_crm/orders.xls` | 9,728 | excel | Orders | 35 | `19dc89e16616` |
| `workbooks/xls/2+wb-1sheet/example_2_hr/departments.xls` | 5,632 | excel | Departments | 8 | `39f2aa90eea4` |
| `workbooks/xls/2+wb-1sheet/example_2_hr/employees.xls` | 9,728 | excel | Employees | 20 | `0b0dbbbb1736` |
| `workbooks/xls/2+wb-1sheet/example_2_hr/payroll.xls` | 5,632 | excel | Payroll | 20 | `dd098569f3af` |
| `workbooks/xls/2+wb-1sheet/example_3_retail/inventory.xls` | 5,632 | excel | Inventory | 20 | `40ac53dcb350` |
| `workbooks/xls/2+wb-1sheet/example_3_retail/products.xls` | 5,632 | excel | Products | 20 | `8b20c7b0cd2b` |
| `workbooks/xls/2+wb-1sheet/example_4_school/grades.xls` | 13,824 | excel | Grades | 90 | `90043e64793d` |
| `workbooks/xls/2+wb-1sheet/example_4_school/students.xls` | 9,728 | excel | Students | 30 | `95ab74cfdeff` |
| `workbooks/xls/2+wb-1sheet/example_4_school/teachers.xls` | 5,632 | excel | Teachers | 12 | `953958f4ce67` |
| `workbooks/xls/2+wb-1sheet/restaurant/menu_items.xls` | 28,672 | excel | menu_items | 32 | `63a30aea5224` |
| `workbooks/xls/2+wb-1sheet/restaurant/order_details.xls` | 1,034,240 | excel | order_details | 12,234 | `07173a0ecd72` |
| `workbooks/xls/2+wb-1sheet/restaurant/restaurant.xls` | 26,624 | excel | 2 multi-single restaurant_db_da | 9 | `f9fe549cf684` |
| `workbooks/xls/2+wb-multisheet/example_1_finance_budgets/budget_2024.xls` | 5,632 | excel | Q1, Q2, Q3, Q4 | 20 | `469d983b2b65` |
| `workbooks/xls/2+wb-multisheet/example_1_finance_budgets/budget_2025.xls` | 5,632 | excel | Q1, Q2, Q3, Q4 | 20 | `30b4092f95ac` |
| `workbooks/xls/2+wb-multisheet/example_2_regional_sales/region_east.xls` | 9,728 | excel | Sales, Returns | 31 | `86edad940db6` |
| `workbooks/xls/2+wb-multisheet/example_2_regional_sales/region_west.xls` | 9,728 | excel | Sales, Returns | 31 | `c7867349114e` |
| `workbooks/xls/2+wb-multisheet/example_3_hr_by_department/dept_engineering.xls` | 9,728 | excel | Employees, Payroll | 30 | `847e1df5f4a4` |
| `workbooks/xls/2+wb-multisheet/example_3_hr_by_department/dept_sales.xls` | 9,728 | excel | Employees, Payroll | 30 | `2ee868e268d1` |
| `workbooks/xls/2+wb-multisheet/example_4_product_lines/electronics.xls` | 9,728 | excel | Products, Inventory, Sales | 60 | `a4f8f5a09e7a` |
| `workbooks/xls/2+wb-multisheet/example_4_product_lines/furniture.xls` | 9,728 | excel | Products, Inventory, Sales | 60 | `580a81d6293f` |
| `workbooks/xls/2+wb-multisheet/example_4_product_lines/office_supplies.xls` | 9,728 | excel | Products, Inventory, Sales | 60 | `c2808e9d8cb1` |
| `workbooks/xls/2+wb-multisheet/food_supplier/food supplier 01.xls` | 43,520 | excel | customers, shippers, employees | 103 | `7e3c4902d8ee` |
| `workbooks/xls/2+wb-multisheet/food_supplier/food supplier 02.xls` | 259,072 | excel | orders, order details, products, categories | 3,070 | `128bb72d58d8` |
| `workbooks/xlsx/1wb-1sheet/example_1_employees/employees.xlsx` | 6,347 | excel | Employees | 25 | `17b06280cb8d` |
| `workbooks/xlsx/1wb-1sheet/example_2_products/products.xlsx` | 5,618 | excel | Products | 20 | `e7f334a812cb` |
| `workbooks/xlsx/1wb-1sheet/example_3_sales_transactions/sales_transactions.xlsx` | 6,837 | excel | Transactions | 40 | `3aa6a3f31807` |
| `workbooks/xlsx/1wb-1sheet/example_4_weather_observations/weather_observations.xlsx` | 6,045 | excel | Weather | 30 | `4cab432e03c9` |
| `workbooks/xlsx/1wb-multisheet/crm/CRM_sales_opportunities.xlsx` | 444,878 | excel | data_dictionary, accounts, sales teams, products, sales pipeline | 8,948 | `535f97fec83c` |
| `workbooks/xlsx/1wb-multisheet/example_1_company_report/company_report.xlsx` | 8,591 | excel | Revenue, Expenses, Employees | 99 | `1b195588675a` |
| `workbooks/xlsx/1wb-multisheet/example_2_sales_dashboard/sales_dashboard.xlsx` | 8,773 | excel | Sales, Products, Regions | 65 | `bb602b53fd40` |
| `workbooks/xlsx/1wb-multisheet/example_3_school_records/school_records.xlsx` | 8,457 | excel | Students, Grades, Teachers | 110 | `e2d51fbdb1bf` |
| `workbooks/xlsx/1wb-multisheet/example_4_inventory_system/inventory_system.xlsx` | 7,402 | excel | Products, Suppliers, Stock | 46 | `c3b7977db382` |
| `workbooks/xlsx/1wb-multisheet/fastfood/fastfood.xlsx` | 17,411 | excel | bk, mcd | 264 | `c60e77998c94` |
| `workbooks/xlsx/1wb-multisheet/stores/Dataset-Bhagwati Store.xlsx` | 52,081 | excel | Master Data, Opening Stock, Sales Data | 2,232 | `6514a734b824` |
| `workbooks/xlsx/2+wb-1sheet/example_1_crm/customers.xlsx` | 6,242 | excel | Customers | 25 | `43811f162759` |
| `workbooks/xlsx/2+wb-1sheet/example_1_crm/orders.xlsx` | 6,100 | excel | Orders | 35 | `0cfef412ec30` |
| `workbooks/xlsx/2+wb-1sheet/example_2_hr/departments.xlsx` | 5,256 | excel | Departments | 8 | `27247ee6f8fe` |
| `workbooks/xlsx/2+wb-1sheet/example_2_hr/employees.xlsx` | 6,081 | excel | Employees | 20 | `cdf4ba8ae823` |
| `workbooks/xlsx/2+wb-1sheet/example_2_hr/payroll.xlsx` | 5,654 | excel | Payroll | 20 | `e28a3a43f0af` |
| `workbooks/xlsx/2+wb-1sheet/example_3_retail/inventory.xlsx` | 5,591 | excel | Inventory | 20 | `99fa4701b738` |
| `workbooks/xlsx/2+wb-1sheet/example_3_retail/products.xlsx` | 5,621 | excel | Products | 20 | `c8bbb294721e` |
| `workbooks/xlsx/2+wb-1sheet/example_4_school/grades.xlsx` | 6,494 | excel | Grades | 90 | `2a29378adbed` |
| `workbooks/xlsx/2+wb-1sheet/example_4_school/students.xlsx` | 5,834 | excel | Students | 30 | `62427a9169b9` |
| `workbooks/xlsx/2+wb-1sheet/example_4_school/teachers.xlsx` | 5,277 | excel | Teachers | 12 | `2104e1757ab0` |
| `workbooks/xlsx/2+wb-1sheet/restaurant/menu_items.xlsx` | 11,199 | excel | menu_items | 32 | `031c4b2f907e` |
| `workbooks/xlsx/2+wb-1sheet/restaurant/order_details.xlsx` | 403,113 | excel | order_details | 12,234 | `4e439b14212c` |
| `workbooks/xlsx/2+wb-1sheet/restaurant/restaurant.xlsx` | 10,479 | excel | 2 multi-single restaurant_db_da | 9 | `bf5b69b2dbe4` |
| `workbooks/xlsx/2+wb-multisheet/example_1_finance_budgets/budget_2024.xlsx` | 7,185 | excel | Q1, Q2, Q3, Q4 | 20 | `ba28de09ab78` |
| `workbooks/xlsx/2+wb-multisheet/example_1_finance_budgets/budget_2025.xlsx` | 7,181 | excel | Q1, Q2, Q3, Q4 | 20 | `7459f5d1b568` |
| `workbooks/xlsx/2+wb-multisheet/example_2_regional_sales/region_east.xlsx` | 6,497 | excel | Sales, Returns | 31 | `9513c060b175` |
| `workbooks/xlsx/2+wb-multisheet/example_2_regional_sales/region_west.xlsx` | 6,500 | excel | Sales, Returns | 31 | `df7d73074c5b` |
| `workbooks/xlsx/2+wb-multisheet/example_3_hr_by_department/dept_engineering.xlsx` | 6,943 | excel | Employees, Payroll | 30 | `818eb5aa9c9d` |
| `workbooks/xlsx/2+wb-multisheet/example_3_hr_by_department/dept_sales.xlsx` | 6,941 | excel | Employees, Payroll | 30 | `c88d88062d1c` |
| `workbooks/xlsx/2+wb-multisheet/example_4_product_lines/electronics.xlsx` | 7,195 | excel | Products, Inventory, Sales | 60 | `427ca4143310` |
| `workbooks/xlsx/2+wb-multisheet/example_4_product_lines/furniture.xlsx` | 7,199 | excel | Products, Inventory, Sales | 60 | `235b59115856` |
| `workbooks/xlsx/2+wb-multisheet/example_4_product_lines/office_supplies.xlsx` | 7,219 | excel | Products, Inventory, Sales | 60 | `cfba61766230` |
| `workbooks/xlsx/2+wb-multisheet/food_supplier/food supplier 01.xlsx` | 18,081 | excel | customers, shippers, employees | 103 | `2902a4880470` |
| `workbooks/xlsx/2+wb-multisheet/food_supplier/food supplier 02.xlsx` | 114,252 | excel | orders, order details, products, categories | 3,070 | `0db002bf50ef` |

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

A suite that cannot fail proves nothing, so each key invariant was verified by
deliberately breaking the code and confirming the right cases went red. Every
change was reverted afterwards.

| Mutation | Expected | Result |
|---|---|---|
| Remove the header strip at `data_loader.py:60` | Invariant 9 fails | **2 batches red** (`stores`, both formats — the only corpus files with padded headers) |
| Stop subtracting the header row in `_sheet_entry` | Invariant 3 fails everywhere | **42 batches red** |
| Remove the `({stem})` disambiguator at `data_loader.py:69` | Invariant 6 fails on duplicate-sheet-name batches | **8 batches red** — exactly the four duplicate-name batches × 2 formats |
| Make `_extent` count formatting-only cells as data | Invariant 3 fails | **2 cases red** — `extensions/xlsx/format_only_trailing.xlsx` (`probe rows == loaded rows: expected 10, got 4`) and the all-files mixed batch |
| Make `_probe_xls` trust `sheet.nrows`/`ncols` instead of scanning | Invariant 3 fails | **No failures — see below** |

#### The `.xlsx` half of this gap is now closed

The fourth mutation reproduces the exact bug `workbook_probe` exists to prevent:
Excel writes the dimension record over the *used* range, so rows left holding only
formatting after their data was deleted still count.

On the first run of this plan it caused **zero failures** — no file in the corpus
had formatting-only trailing rows, so the stored dimension happened to equal the
true extent everywhere and the suite stayed green through a real regression.

`extensions/xlsx/format_only_trailing.xlsx` closes it: 4 data rows under a stored
dimension of `A1:C11`, the extra six rows carrying cell borders and no values.
Probe and loader both report 4; under the mutation the probe reports 10 and
invariant 3 goes red by 6 rows.

To stop the gap reopening silently, `test_corpus_covers_formatting_only_trailing_rows`
([test_file_loading.py](../tests/test_file_loading.py)) asserts the *corpus* still
contains at least one workbook whose stored dimension overstates its real extent.
It names no file, so any workbook can satisfy it, and it recomputes the true extent
locally rather than calling `_extent` — borrowing the function under test would move
both sides of the comparison together and make a broken `_extent` look like a corpus
with nothing to find.

#### The `.xls` half cannot be closed with a corpus file

`_extent` is shared, so the mutation above is caught for both formats. A mutation
confined to `_probe_xls` — taking xlrd's `sheet.nrows`/`ncols` directly instead of
scanning — **survives the whole suite.** Checked directly: across every `.xls`
sheet in the corpus, stored `nrows`/`ncols` equals the true extent every time.

The earlier conclusion was that one hand-saved workbook would close this. **That
was wrong, and building the file is what showed it.**
`extensions/xls/format_only_trailing.xls` is a faithful hand-saved twin of the
`.xlsx` — same 4 data rows, same formatting carried down to row 11 — and the
mutation still survives. The reason is in xlrd, not in the corpus:

| How the file is opened | `sheet.nrows` |
|---|---|
| `xlrd.open_workbook(path, formatting_info=True)` | **11** — the formatting-only rows are there |
| `xlrd.open_workbook(path, on_demand=True)` — what `_probe_xls` does | **5** |

xlrd discards formatting-only rows itself unless asked for formatting, so by the
time `_extent` runs the condition is already gone. Stored `nrows` (5) and the
scanned extent (5, being 4 data rows plus the header) agree, and the mutation has
nothing to trip over.

The consequence is worth stating plainly: on the `.xls` path, immunity to this
particular bug comes from **xlrd's default parsing, not from this project's code**.
`_extent` is not redundant there — it still differs from `nrows` for rows holding
empty-string cells, which xlrd does count — but that is a different condition, and
no corpus file currently exhibits it either. Adding more `.xls` files will not
change any of this. Making the `.xls` scan provable would mean testing `_extent`
directly against a synthetic row sequence rather than through a workbook, which is
a unit test rather than a corpus file.

This mutation run also caught a flaw in the test code itself: invariant 6
originally checked that the *predicted* names were distinct, which was a tautology
— this module generates them from `(sheet, stem)` pairs that are distinct by
construction, so it could never fail. It now measures the *loaded* table count
against the number of populated sheets, which is what the third mutation above
confirms.

### 13.2 What the closing summary found on its first run

Adding the per-invariant counts (§6.1) immediately surfaced two things that 84/84
green had been hiding.

**Invariant 4's second half is unexercised.** `empty-skipped` — every empty probed
sheet produces no table — emitted **zero** checks. Confirmed against the run data:
no sheet anywhere in the corpus is flagged `empty`, so `DataLoader`'s empty-sheet
skip has never once been observed doing its job. This is the §12 risk in its pure
form, and it is the reason that item was promoted to the top of §9. Unlike the
`.xls` case above, one workbook with a blank sheet beside a populated one closes it.

**A hand-edited baseline entry was contradicting its own file.** Making drift fail
the runner (§6.3) turned up
`workbooks/xlsx/1wb-multisheet/crm/CRM_sales_opportunities.xlsx`, whose recording
listed **six** sheets — including a phantom `format_only_trailing` — against
**five** tables, for a file that has five sheets and whose sha still matched.
`record_baseline` derives both lists from one `observed` dict and cannot produce
that pair, so the entry had been edited by hand. Nothing caught it: the workbook
group has no `test_matches_recorded_baseline` (only Tests 1 and 3 do), and the
runner was discarding drift. The entry has been corrected.

That second finding leaves a real gap open: **`tests/test_workbook_matrix.py` still
has no baseline test**, so between runner invocations the workbook group's
recordings are unguarded by pytest. Worth adding.
