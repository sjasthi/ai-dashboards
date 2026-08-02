# Implementation Plan — Test Plan, Usage Tracking, and Saved Reports

## Context

Three requirements, driven by a professor review and by gaps the codebase currently has:

1. **A test plan.** The upload pipeline is the least-tested part of the app: grepping `tests/` for `inspect`, `analyze-full`, `data_loader`, `workbook_probe`, or `DataLoader` returns **zero hits**. All 4 existing test files start from a hand-built `SESSIONS` dict, so nothing exercises real file reading. The professor wants `.csv` / `.xls` / `.xlsx` covered, with emphasis on a 2x2 Excel matrix (1 vs 2+ workbooks x 1 vs multiple sheets), and wants it demonstrated live. **Scope is those two tests, plus an untracked drop-anything folder so later files need no plan change** — see §1.0.
2. **Usage tracking.** There is no persistence at all — `SESSIONS` is a module-level dict at [api.py:124](../app/api.py#L124) that is lost on restart. We need user / file / report counts plus behavioural metadata, for a home page that does not exist yet.
3. **Loading previous reports — a developer tool.** Reports vanish on refresh, and there is no way to inspect what the pipeline produced for a past run. A saved report must be re-openable without re-uploading and without another LLM call. This is for debugging and QA only: it is not exposed to users and does not appear on the home page.

The enabling discovery: **the report payload is already pure JSON and re-rendering needs no server.** `/api/generate-report` returns `chart` (a plain Plotly figure dict), `stats` (scalars/strings/lists), and `rows` (via pandas' JSON writer). [Plot.jsx:11-36](../app/web/src/components/ui/Plot.jsx#L11-L36) draws it with `window.Plotly.newPlot(node, chart.data, buildLayout(...))` — client-side only. So saving the JSON is sufficient to view a report again offline.

**No new dependencies at all.** `sqlite3` is stdlib; `openpyxl`/`xlrd`/`pandas` are already installed. The earlier `xlwt` fixture-generator idea is dropped — the corpus is real files supplied by the developer, not generated ones.

### Decisions already made
- **Test corpus is real, developer-supplied files discovered from a folder tree**, not a hand-written case manifest. The test walks `tests/data/` and checks every file it finds; adding coverage means dropping a file in a folder, with no code or manifest edit.
- Because the files are supplied rather than authored, assertions are **invariants** (§1.4) plus an **optional recorded baseline** (§1.5) that pins exact values once a file has been reviewed.
- **The corpus is delivered and measured** — 106 files, 13 MB, ~20 s full sweep, 0 probe/loader mismatches. `tests/data/` is committed; `tests/data/inbox/` is not (§1.3).
- **No fast/slow test split** — at 19.4 s there is nothing to optimise, and a split is one more thing that can silently skip coverage.
- "Users" = anonymous browser UUID in `localStorage`, sent as `X-Client-Id`.
- **Loading previous reports is a developer tool, not a user feature.** The home page does not list reports. Report history is persisted behind a `SAVE_REPORT_HISTORY` flag and read only through a token-gated admin endpoint plus a dev-only frontend route.
- Home page is user-facing: it outlines what the app can do and shows aggregate usage counters.

### One flagged concern
The professor asked for "a very large range of excel files". Folder-driven discovery answers this better than the earlier golden-manifest design did: N is however many files get dropped in, and volume costs nothing to add. The trade is that a brand-new file is checked against invariants only until it is recorded into the baseline. §1.5's `--record` step is what converts a file from "loads without contradiction" to "loads with these exact numbers, and any change is a regression" — run it once per file, review the diff, commit.

---

## Execution — small chunks with safe stop points

Rate limits are expected to interrupt this work. Every chunk below is sized to leave the repo in a **working, committable state**: tests green, app runnable, no half-applied edit. Stop and report after each one; a hard stop then lands between chunks rather than mid-file.

Note: remaining rate-limit budget is not visible from inside the session, so pacing is by chunk boundary, not by a token estimate. If a usage warning appears, finish the current chunk and stop.

| # | Chunk | Ends green when |
|---|---|---|
| 1 | Phase 0 — copy plan to `docs/` | File exists, links resolve |
| 2 | `.gitignore` rules, `tests/data/inbox/README.md`, `tests/conftest.py` with discovery fixtures | `pytest` passes (skips cleanly if a folder is empty) |
| 3 | `tests/loading_checks.py` — discovery, `run_batch`, the invariant set | Importable, `pytest` passes |
| 4 | `tests/test_file_loading.py` — Test 1 (extensions + mixed batches), unit level | Unit level green |
| 5 | `tests/test_workbook_matrix.py` — Test 2 (the 1/2+ workbook x 1/multi sheet matrix, `.xlsx` and `.xls`) | Matrix green |
| 6 | `tests/test_inbox_files.py` — Test 3 (drop-anything corpus) | Green, and skips when the inbox is empty |
| 7 | `tests/test_upload_api.py` — same batches through `/api/inspect` + `/api/analyze-full` | API level green |
| 8 | `scripts/run_test_plan.py` + HTML/JSON result output + `--record` | Demo runs end to end |
| 9 | `docs/TEST_PLAN.md` | **Phase 1 complete — major stop point** |
| 10 | `app/data/telemetry.py` + schema + its own unit test (no wiring) | `pytest` passes |
| 11 | `clientId.js`, `X-Client-Id` plumbing, CORS check | App still works |
| 12 | Instrument `/api/inspect` + `/api/analyze-full`, **including provider attribution in `AI_Engine.send_prompt`** | Events land in `usage.db` |
| 13 | Instrument `/api/generate-report`, export, `POST /api/events` | Events land |
| 14 | `GET /api/stats` + tests | **Phase 2 complete — major stop point** |
| 15 | `Homedashboard.jsx` — capabilities content, static | Renders as landing tab |
| 16 | Stat tiles wired to `/api/stats` (load `dataviz` skill first) | **Phase 3a complete** |
| 17 | `saved_reports` table, `SAVE_REPORT_HISTORY`, auto-save insert | Rows appear when flag on |
| 18 | Admin endpoints + token gate + tests | Verified with curl |
| 19 | `DevReportBrowser.jsx` + dev gating + production-bundle absence check | **Phase 3b complete** |

---

## Phase 1 — Test plan and harness

### 1.0 Scope — two required tests, plus a drop-anything folder

Phase 1 delivers the two tests the professor asked for, plus a third that exists so future files need no plan change. Everything under naming/structure hazards, encodings, failure modes, scale, and sheet selection is **deferred** — see §1.7.

| | Test | Question it answers |
|---|---|---|
| **Test 1** | Extension coverage | Does every `.csv`, `.xls` and `.xlsx` file in the corpus read and load correctly, alone and in mixed batches? |
| **Test 2** | Workbook/sheet matrix | Do `.xlsx` and `.xls` workbooks load correctly across 1-vs-2+ workbooks x 1-vs-many sheets? |
| **Test 3** | Inbox | Does *this particular file a developer just dropped in* read and load correctly? No convention, no declared expectations, untracked. |

All three are **folder-driven**: the suite walks a directory tree, treats what it finds as the corpus, and reports a row per file and per batch. Adding coverage is dropping a file in a folder — no code and no manifest edit.

**Status: the corpus is delivered and measured.** 106 files, 13 MB, full sweep **~20 s with 0 probe/loader mismatches**. Details in §1.2.

### 1.1 `docs/TEST_PLAN.md`
IEEE-829 derived, trimmed to what a capstone actually needs. Sections:

| Section | Content |
|---|---|
| Identifier & scope | Version, date, the three tests in §1.0, and an explicit "deferred" list from §1.7 |
| References | `project_file_structure.md`, this plan |
| Test items | `/api/inspect`, `/api/analyze-full`, `DataLoader`, `workbook_probe` |
| Features tested | Extension dispatch, multi-sheet expansion, table naming, `origins` mapping, probe/pandas row agreement, mixed-batch handling, cross-workbook name uniqueness |
| Features **not** tested | Everything in §1.7, plus LLM recommendation quality (non-deterministic — stubbed), report math (already covered by `test_report_stats.py`), email delivery |
| Approach | 3 levels: unit (`DataLoader`/`inspect_file`), API (`TestClient`, LLM stubbed), manual UI |
| Pass/fail criteria | The invariants in §1.4, plus the recorded baseline in §1.5 where one exists |
| Environment | Windows 11, Python version, pandas 2.3.3 / openpyxl 3.1.5 / xlrd 2.0.2, Chrome |
| Test data | `tests/data/` (§1.2) — full inventory with per-file size, sheet names and sha256 |
| Deliverables | This doc, the suites, `test_results/file_loading_report.html`, `tests/data/baseline.json` |
| Risks | `inbox/` is untracked by design, so its results are machine-local; single-worker server; no CI |
| Demo script | The walkthrough in §1.8 |

### 1.2 Test corpus — the delivered tree

Root: **`tests/data/`**. Deliberately not `datasets/` (that is demo data the app is pointed at by hand) and not `tests/fixtures/` (which would imply generated content).

```
tests/data/
  extensions/                         <- Test 1
    csv/  xls/  xlsx/                   files of that format, any number
  workbooks/                          <- Test 2
    xlsx/
      1wb-1sheet/       <example>/      each <example> folder is ONE batch
      2+wb-1sheet/      <example>/
      1wb-multisheet/   <example>/
      2+wb-multisheet/  <example>/
    xls/
      1wb-1sheet/  2+wb-1sheet/  1wb-multisheet/  2+wb-multisheet/
  inbox/                              <- Test 3, untracked except README.md
  baseline.json                       <- recorded expectations (§1.5)
```

**Note the depth:** a batch is `workbooks/<fmt>/<cell>/<example>/`, four levels down, not three. The cell folder groups many examples of the same shape, so the matrix has 42 independent batch cases rather than 8.

**The folder name is the expectation.** Cell folders are parsed with `^(\d+)(\+?)wb-(1|multi)sheet$`. The `+` means "at least": `2+wb-1sheet` asserts `>= 2` workbooks, while a bare `2wb-1sheet` would assert exactly 2. Every file in an `<example>` folder is uploaded **as one batch**, which is what makes "2 workbooks" a real multi-file case rather than two single-file cases. Dropping in a `3wb-multisheet/` folder later works with no code change; a folder whose name doesn't match is reported as skipped-unrecognised rather than silently ignored.

**Each batch is filtered to its tree's own format.** Under `workbooks/xlsx/` only `.xlsx` files count, under `workbooks/xls/` only `.xls`. Without this, a single stray file makes a `1wb-1sheet` folder read as two workbooks and reddens a matrix cell on a corpus problem rather than a code problem. Off-format files are listed as ignored in the report, never dropped silently.

The `xlsx/` and `xls/` split exists because these are **two separate code paths**, not a formality: `.xlsx` goes through `_probe_xlsx`/openpyxl ([workbook_probe.py:53-67](../app/data/workbook_probe.py#L53-L67)), `.xls` through `_probe_xls`/xlrd with `on_demand`/`unload_sheet`/`release_resources` ([workbook_probe.py:70-85](../app/data/workbook_probe.py#L70-L85)). They share only `_extent`, which carries a format-dependent assumption on one line — openpyxl yields `None` for an empty cell, xlrd yields `''` ([workbook_probe.py:45](../app/data/workbook_probe.py#L45)). Running the same matrix through both is the point.

#### What was delivered, and what measuring it showed

| Tree | Contents |
|---|---|
| `extensions/csv` | 13 files |
| `extensions/xls` | 11 files — all single-sheet |
| `extensions/xlsx` | 12 files — all single-sheet, incl. the purpose-built `format_only_trailing.xlsx` |
| `workbooks/xls` | 4 cells / 21 batch folders / 35 workbooks |
| `workbooks/xlsx` | 4 cells / 21 batch folders / 35 workbooks |

Cells hold 4–7 batches each. 11 datasets exist in all three extensions, so mixed batches form cleanly. **83 discovered cases.** 106 files, **13 MB**, largest file 1.3 MB.

Running `inspect_file` + `DataLoader.add_files` across the whole corpus: **19.4 s, 0 probe/loader row mismatches**, slowest single file 3.8 s. Every cell label matches reality, and the naming rule produces exactly the strings §1.4 invariant 5 predicts (`'Q1 (budget_2024).xlsx'`, bare `'menu_items.xlsx'`).

The corpus also covers several §1.7 hazards for free — see the note there.

#### `inbox/` — the drop-anything folder

`extensions/` and `workbooks/` both impose structure before the suite knows what to expect of a file. That is what lets them assert folder-derived expectations, but it leaves a developer holding "here is a spreadsheet that broke something, does it load?" with nowhere to put it.

`tests/data/inbox/` takes anything, with no convention:
- A loose `.csv` / `.xls` / `.xlsx` at the root → one single-file case.
- Any subfolder → one batch case from every supported file inside it, **mixed extensions allowed** — that is the point of it.
- One level of nesting; a directory that directly contains supported files is a batch.
- Unsupported extensions are listed as ignored, so a mistyped filename is visible rather than invisible.

It runs the §1.4 invariants that don't depend on folder metadata — all except the file-count and sheet-class expectations, which have no declared value here. So it answers "does this read and load, and do the probe and the loader agree about it" for literally any file, which is the question the developer actually has.

### 1.3 Version control

**`tests/data/` is committed** — 13 MB, smaller than the already-committed `datasets/` (41 files, including a 12 MB `.xls`). That makes "all 106 files green" reproducible on a fresh clone and in front of the professor, rather than a claim that depends on one laptop.

**`inbox/` contents are not**, so a developer can drop a 300 MB export in without it ever reaching GitHub:

```gitignore
test_results/
tests/data/inbox/*
!tests/data/inbox/README.md
```

Two consequences of `inbox/` being untracked, both handled:
- **Inbox results must not enter the committed `tests/data/baseline.json`**, or it would pin exact row counts against files nobody else has and every other clone would fail on entries it cannot satisfy. Inbox recordings go to `tests/data/inbox/baseline.json`, which the rule above already ignores.
- Empty is its normal resting state on any machine but the one that filled it, so it skips rather than fails, and `--strict` does not demand it.

For the two required corpora:
- **Missing or empty folder → `pytest.skip` naming the folder**, never a failure. A partial checkout stays green; it just reports zero cases.
- The runner (§1.8) takes `--strict`, which turns "required corpus missing" into a hard error. That is the mode used for the demo, so an empty folder can't masquerade as a pass on the day.
- `docs/TEST_PLAN.md` carries the corpus inventory — path, size, sheet names, sha256 — so what "all green" covered on a given date is recorded rather than implied.

### 1.4 What "reads and loads correctly" means — the invariant set

Since the files are supplied rather than authored, the assertions are properties that must hold for *any* well-formed input. `tests/loading_checks.py` defines them once. Per batch:

| # | Invariant | Why it has teeth |
|---|---|---|
| 1 | `inspect_file` returns no `error`, and `kind` matches the extension (`csv` for `.csv`, `excel` for `.xls`/`.xlsx`) | Catches an unreadable file. Note a `.xlsx` reporting `kind: "csv"` is a **failure, not a pass** — it means the workbook didn't parse and fell through the CSV rescue at [workbook_probe.py:127-135](../app/data/workbook_probe.py#L127-L135), which would otherwise hide a broken file behind a plausible-looking row count |
| 2 | `DataLoader.add_files` completes without raising | The baseline "it loads" |
| 3 | **Per sheet: probe rows == `len(df)` and probe columns == `len(df.columns)`** | The contract the whole `workbook_probe` module exists to hold ([workbook_probe.py:11-21](../app/data/workbook_probe.py#L11-L21)). The upload screen's row count must equal what the analysis loads. **Highest-value check in the suite** |
| 4 | Every non-empty probed sheet produces exactly one table; every `empty: true` sheet produces none | Pins the skip at [data_loader.py:57](../app/data/data_loader.py#L57) against the `empty` flag at [workbook_probe.py:100](../app/data/workbook_probe.py#L100) |
| 5 | Table naming: `>1` kept sheet → `"{sheet} ({stem}){ext}"`; otherwise the bare filename | The rule at [data_loader.py:69](../app/data/data_loader.py#L69), including the collapse-to-filename branch |
| 6 | Table names are unique across the batch | This is what the 2-workbook cells are for — two workbooks sharing a sheet name must not collide |
| 7 | `origins[table] == uploading filename`, for every table | [data_loader.py:13](../app/data/data_loader.py#L13); the documented mitigation for the ambiguous parenthesised name |
| 8 | Batch total rows == sum of per-file `inspect` rows | Invariant 3 rolled up to what the UI shows for the batch |
| 9 | No string column name has leading/trailing whitespace; non-string headers survive untouched | [data_loader.py:60](../app/data/data_loader.py#L60) |
| 10 | Every table has ≥1 column and ≥1 row | Guards a "loaded" table that is actually nothing |

**Measured status: all 10 hold across all 106 files, 0 mismatches.** That is the starting point, not the goal — the value is that any future change which breaks one now reddens a named case.

**Test 1 (`extensions/`)** runs these over: every file individually; then mixed batches — `csv+xlsx`, `csv+xls`, `xls+xlsx`, `csv+xls+xlsx` (one representative per extension, first by sorted name) — then one batch of **every file in `extensions/` at once**. A `--all-combos` flag runs the full cross-product instead of one representative each, capped, for when the corpus is small enough to afford it. An extension folder that is absent or empty drops its combinations rather than failing them.

**Test 2 (`workbooks/`)** runs these per `<example>` batch folder, plus the folder-name expectations:
- file count `>= n` for a `<n>+wb` cell, `== n` for a bare `<n>wb` cell (after format filtering)
- `1sheet` cells: each workbook probes exactly one non-empty sheet, and its table is named with the **bare filename** (the `multi=False` branch)
- `multisheet` cells: each workbook probes ≥2 non-empty sheets, and **every** table is named `"{sheet} ({stem}){ext}"`
- table count across the batch == total non-empty sheets across its workbooks

**Test 3 (`inbox/`)** runs invariants 1–10 minus the folder-derived expectations, since nothing has been declared about a dropped file.

**One thing that is deliberately *not* an invariant:** the same logical dataset in different formats need not have the same row count. BIFF8 caps `.xls` at 65,536 rows, so a large CSV converted to `.xls` is legitimately truncated. The report shows per-format numbers side by side; it never compares them.

### 1.5 Recorded baseline — turning invariants into regressions

`tests/data/baseline.json`, written by `scripts/run_test_plan.py --record` and committed after review:

```json
{ "extensions/xlsx/orders.xlsx": {
    "sha256": "...", "kind": "excel", "rows": 8200,
    "sheets": [{"name": "Orders", "rows": 8200, "columns": 12, "empty": false}],
    "tables": {"orders.xlsx": {"rows": 8200, "columns": 12}} } }
```

- A file **in** the baseline is checked against exact recorded values on top of the invariants — any drift is a failure.
- A file **not** in the baseline is invariant-checked only, and the report marks it `unrecorded` so it is visible rather than quietly weaker.
- `sha256` keys the entry: if the file itself changes, the entry is stale and the case reports `baseline-stale` instead of failing on numbers that describe a different file.
- **Inbox recordings go to `tests/data/inbox/baseline.json`, not the committed one** — see §1.3.

This is what recovers the strength of the original golden-manifest design without needing the files in hand when the code is written. Workflow for new files: drop in → run → review invariant results → `--record` → review the JSON diff → commit.

### 1.6 Shared check module and suites

`tests/loading_checks.py` — plain importable module, not a test file. Both pytest and the demo runner import it, so there is exactly one definition of what is checked:
- `discover_extension_cases(root)` — per-file cases plus the mixed batches
- `discover_workbook_cases(root)` — the 4-level walk, format filter, `^(\d+)(\+?)wb-(1|multi)sheet$`
- `discover_inbox_cases(root)` — loose files as single-file cases, subfolders as batches, no convention
- each returns case dicts (`id`, `description`, `files`, `expectations`)
- `run_batch(case)` → result dict (`case_id`, `files`, `checks: [{name, passed, expected, actual}]`, `passed`, `duration_ms`, `error`). Each case declares which invariants apply, so inbox cases simply carry fewer.
- `load_baseline(path)` / `record_baseline(results, path)`

Suites:
- `tests/conftest.py` — **new file**; `tests/` has no conftest yet, and [test_export_api.py:28](../tests/test_export_api.py#L28) does `from tests.test_generate_report_api import ...` relying on rootdir insertion. Adding one fixes that fragility and hosts the shared fixtures: `data_dir`, `client` (`TestClient`), `extension_cases`, `workbook_cases`, `inbox_cases`, `baseline`.
- `tests/test_file_loading.py` — Test 1, `@pytest.mark.parametrize` over discovered cases, unit level (`DataLoader.add_files` + `tables()` + `origins`, `workbook_probe.inspect_file`).
- `tests/test_workbook_matrix.py` — Test 2, same shape, one parametrised case per `<example>` folder, ids like `xlsx/2+wb-multisheet/example_1_finance_budgets`.
- `tests/test_inbox_files.py` — Test 3, same shape; skips when the inbox is empty, which is its state on a fresh clone.
- `tests/test_upload_api.py` — a subset of the discovered batches pushed through `/api/inspect` and `/api/analyze-full`, asserting the API agrees with the unit level. Stub the LLM by monkeypatching `api.ai_engine.get_validated_recommendations` (mirror the pattern in [test_generate_report_api.py:67](../tests/test_generate_report_api.py#L67)) so the suite is fast and deterministic.

Parametrisation is at **case** granularity so one bad file shows as one red row naming that file, not a single opaque failure.

**No fast/slow split.** The full sweep is 19.4 s, so every `pytest` run covers everything — no marker to register, and nothing that can be silently skipped.

### 1.7 Hazards — what the corpus covers for free, and what is deferred

#### Already covered incidentally

The delivered corpus turns out to exercise several of these without any constructed fixture. They are covered by the ordinary invariants, in both formats, since every one of these files exists as an `.xls` and an `.xlsx`:

- **Same sheet name in two different workbooks** — four separate batches: `budget_2024`/`budget_2025` both hold `Q1..Q4`; `region_east`/`region_west` both `Sales`,`Returns`; `dept_engineering`/`dept_sales` both `Employees`,`Payroll`; `electronics`/`furniture`/`office_supplies` all `Products`,`Inventory`,`Sales`. Invariant 6 and the `({stem})` disambiguator get a real workout, not a theoretical one.
- **31-character sheet name** (Excel's limit) — `'2 multi-single restaurant_db_da'` in `restaurant.xls`/`.xlsx`.
- **Spaces in sheet names** — `'sales teams'`, `'sales pipeline'`, `'order details'`, `'Master Data'`, `'Opening Stock'`.
- **Spaces and hyphens in filenames** — `'food supplier 01.xlsx'`, `'Dataset-Bhagwati Store.xlsx'`.
- **Many sheets in one workbook** — `CRM_sales_opportunities` at 5 sheets.

#### Covered deliberately, after mutation testing found the gap

- **Formatting-only trailing rows (`.xlsx`)** — `extensions/xlsx/format_only_trailing.xlsx`: 4 data rows under a stored dimension of `A1:C11`, the extra six carrying borders and no values. Not incidental coverage — the corpus originally had none, and a mutation reproducing the exact bug [workbook_probe.py:11-21](../app/data/workbook_probe.py#L11-L21) prevents passed the whole suite. With the file in place that mutation reddens invariant 3 by 6 rows. `test_corpus_covers_formatting_only_trailing_rows` asserts the corpus keeps the property, naming no file, so the gap cannot silently reopen.

  The `.xls` half is **not** closed: a mutation confined to `_probe_xls` still survives, because all 82 `.xls` sheets in the corpus have `nrows`/`ncols` equal to their true extent. See "Still deferred".

#### Still deferred

Out of scope for now, recorded so it isn't lost. Each is scoped against a real code path, so picking one up later is implementation, not redesign. **Cost is ~200 KB committed** (measured in the plan file for this work) — space was never the constraint; authoring and verifying ~35 constructed fixtures is.

**Sheet selection** — all sheets; a subset; exactly one sheet of many (asserts the name collapses to the bare filename per [data_loader.py:69](../app/data/data_loader.py#L69)); none selected (400 from [api.py:350](../app/api.py#L350)); malformed `selections` JSON (400); non-list values (400). Needs no new files — it reuses the existing corpus.

**Naming and structure hazards** — each to run in both `.xlsx` and `.xls`:
- Sheet name containing parentheses (the `"{sheet} ({stem}){ext}"` format is ambiguous; assert `origins` still maps back correctly)
- Unicode / accented sheet names and headers
- Headers with leading/trailing whitespace; numeric and blank headers
- Empty sheet among populated ones; header-only sheet (`empty: true`)
- **Formatting-only trailing rows in `.xls`** — the `.xlsx` twin is done (above); this one is not, and it is the **highest-value item left**. `_extent` is shared, so the `.xlsx` file guards it against a change there, but a mutation confined to `_probe_xls` — taking xlrd's `sheet.nrows`/`ncols` directly instead of scanning — passes the entire suite today. One hand-saved `.xls` closes it.
- Single column / single data row; whitespace-only cell (counts as data, [workbook_probe.py:38](../app/data/workbook_probe.py#L38))

**CSV encodings** — utf-8, utf-8 BOM, utf-16, cp1252 against the fallback ladder at [data_loader.py:75](../app/data/data_loader.py#L75).

**Failure modes** — zero-byte file (per-file `error`, batch continues, [api.py:197](../app/api.py#L197); 400 from `/api/analyze-full`, [api.py:316](../app/api.py#L316)); CSV renamed `.xlsx` ([data_loader.py:40](../app/data/data_loader.py#L40)); truncated/corrupt `.xlsx` (`kind: "unknown"` + `error`, never a 500).

**Scale** — a 50k-row x 12-col workbook, gitignored and built on demand. The corpus tops out at 25,000 rows, so this is genuinely uncovered. Note BIFF8 caps at 65,536 rows / 256 columns, so `.xls` cannot exceed that.

These need *constructed* files rather than real ones, which is why they were originally paired with a generator. If picked up, revisit `scripts/make_test_fixtures.py` and the dev-only `xlwt` dependency then — with the caveat that `xlwt` is unmaintained (1.3.0, ~2017), so generated `.xls` should be guarded with `pytest.importorskip("xlwt")`, and the formatting-only-trailing-rows case should use a **real** Excel-saved `.xls`, since that bug depends on how the writing application fills the dimension record. Hand-saving from Excel — the same path that produced the corpus's `.xls` twins — may be simpler than generating.

In the meantime, `inbox/` accepts any of these immediately without a plan change.

### 1.8 Demonstration and output

`scripts/run_test_plan.py` — imports `loading_checks`, runs all three tests, prints a live table, and writes a **self-contained** `test_results/file_loading_report.html` plus a machine-readable `test_results/file_loading_report.json`.

Report contents:
- **Header** — run timestamp, corpus root, total files and batches, pass/fail counts, wall time, library versions (pandas / openpyxl / xlrd / Python).
- **Test 1 section** — one row per file: path, extension, size, `kind`, sheets found, tables produced, probe rows vs loaded rows, baseline status (`recorded` / `unrecorded` / `stale`), pass-fail, duration. Then one row per mixed batch, listing its files. Per-format row counts sit side by side but are never compared — see the non-invariant note in §1.4.
- **Test 2 section** — a 2x2 grid per format, cells labelled `1wb-1sheet` … `2+wb-multisheet`, each expanding to its `<example>` batches and showing workbooks, sheets, and the tables produced with their **exact generated names**. Those names are worth putting on screen: they are the visible output of the naming rule the professor is being shown.
- **Test 3 section** — inbox results, or "empty" when there are none. Never a failure.
- **Ignored files** — off-format files inside a workbook batch and unsupported extensions in the inbox, listed rather than silently dropped.
- **Failure detail** — for any red row, the failing invariant by name with expected vs actual, not just a traceback.

Flags: `--record` (write the baseline), `--strict` (a missing *required* corpus is an error; the inbox is exempt), `--all-combos`, `--only extensions|workbooks|inbox`.

The live demo, scripted in `docs/TEST_PLAN.md`:
1. `pytest -v` — everything green, including the pre-existing 130+ tests.
2. `python scripts/run_test_plan.py --strict` — open `test_results/file_loading_report.html`; walk the four matrix cells for `.xlsx`, then the same four for `.xls`, then the mixed-batch rows.
3. Drop a file into `inbox/`, re-run, show it appear as a new case with no code change. This is the "how do we add more tests later" answer, demonstrated rather than asserted.
4. Manual UI checklist — upload a workbook set from each matrix cell through the running app, show the sheet checkboxes appearing (only rendered when `sheets.length > 1`, [Uploaddashboard.jsx:541](../app/web/src/components/Uploaddashboard.jsx#L541)), confirm the row counts on screen match the report's numbers via `statsFor` ([Uploaddashboard.jsx:159](../app/web/src/components/Uploaddashboard.jsx#L159)), run the analysis.

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
- `pytest -v` — new suites pass alongside the existing ~130 tests, and the case ids name individual files and matrix cells.
- `python scripts/run_test_plan.py --strict` — open `test_results/file_loading_report.html`; every case green; all eight matrix cells (four per format) present and labelled with their generated table names.
- **Rename `tests/data/` temporarily and re-run `pytest`** — confirm it skips with a clear message rather than failing, and that `--strict` on the runner *does* fail. This is the one way "all green" could be a lie.
- Deliberately break something and confirm the right case goes red, proving the suite has teeth rather than asserting tautologies. Three worth trying: comment out the header-strip at [data_loader.py:60](../app/data/data_loader.py#L60) (invariant 9); make `_extent` ([workbook_probe.py:32](../app/data/workbook_probe.py#L32)) return the stored dimension instead of scanning (invariant 3); drop the `({stem})` disambiguator at [data_loader.py:69](../app/data/data_loader.py#L69) and confirm the four duplicate-sheet-name batches redden on invariant 6. Restore each afterwards.
- Confirm a file that is present but unrecorded shows as `unrecorded` in the report, then `--record` and confirm it flips to `recorded` with a reviewable JSON diff.
- **Inbox**: drop in one `.csv`, one `.xls` and a mixed-extension subfolder; confirm they appear as cases and pass. Then `git status` — nothing under `inbox/` should be addable except the README, and `--record` must have written `inbox/baseline.json`, not the committed one. Empty the folder and confirm the suite skips rather than fails.
- Manually upload a workbook set from each matrix cell through the running UI per the §1.8 checklist, and confirm the on-screen row counts match the report.

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
