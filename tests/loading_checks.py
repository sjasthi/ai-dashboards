"""Shared discovery and checking logic for the file-loading test plan.

Not a test file - a plain module imported by both pytest (tests/test_file_loading.py,
test_workbook_matrix.py, test_inbox_files.py) and the demo runner
(scripts/run_test_plan.py), so there is exactly one definition of what each case
asserts and the HTML report can never disagree with the suite.

The corpus is real spreadsheets supplied by the developer, not generated fixtures,
so expected values aren't known when this code is written. Two mechanisms cover
that gap:

- INVARIANTS - properties that must hold for any well-formed input, checked on
  every case. See the module docstring of each check function.
- The baseline - once a file's numbers have been eyeballed, `--record` pins them
  and any later drift is a failure rather than a shrug.

Discovery is folder-driven throughout: adding coverage is dropping a file in a
folder, never editing this module.
"""

import hashlib
import io
import json
import os
import re
import contextlib
import time

from app.data.data_loader import DataLoader
from app.data.workbook_probe import inspect_file

SUPPORTED_EXTENSIONS = (".csv", ".xls", ".xlsx")
EXCEL_KIND = "excel"
CSV_KIND = "csv"

# tests/data/, resolved from this file so it works regardless of cwd.
#
# TEST_DATA_ROOT relocates the corpus. Two uses: pointing the suite at a corpus
# kept outside the repo, and verifying the missing-corpus path (point it at an
# empty directory and confirm the suites skip while run_test_plan.py --strict
# fails) without renaming folders, which Windows will not always allow.
DATA_ROOT = os.environ.get("TEST_DATA_ROOT") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
EXTENSIONS_ROOT = os.path.join(DATA_ROOT, "extensions")
WORKBOOKS_ROOT = os.path.join(DATA_ROOT, "workbooks")
INBOX_ROOT = os.path.join(DATA_ROOT, "inbox")

BASELINE_PATH = os.path.join(DATA_ROOT, "baseline.json")
# Inbox files are gitignored, so their recorded numbers must stay out of the
# committed baseline - otherwise every other clone fails on entries describing
# files it does not have.
INBOX_BASELINE_PATH = os.path.join(INBOX_ROOT, "baseline.json")

# <n>wb-<class>sheet, where a trailing + on the count means "at least n".
# 2+wb-multisheet -> at least 2 workbooks, each with multiple sheets.
CELL_PATTERN = re.compile(r"^(\d+)(\+?)wb-(1|multi)sheet$")

# Mixed-extension batches: the combinations worth forming from one representative
# of each extension. Single extensions are already covered file-by-file.
MIXED_COMBOS = (
    ("csv", "xlsx"),
    ("csv", "xls"),
    ("xls", "xlsx"),
    ("csv", "xls", "xlsx"),
)

# The properties this module asserts, in the order and numbering of TEST_PLAN
# section 6.1. Every check emitted by run_batch carries one of these ids, which is
# what lets the report say *what* was verified rather than only how many cases
# passed - and, more usefully, which invariants ran no checks at all.
#
# An invariant with zero checks is not a pass. It is the failure mode TEST_PLAN
# section 13.1 records for .xls extent scanning: a property that looks guarded but
# has nothing in the corpus to guard against. Counting per invariant is what makes
# that state visible instead of indistinguishable from green.
#
# `number` is the section 6.1 row; None means the check is not one of the ten
# universal invariants. Invariant 4 appears twice because its halves fire under
# different conditions - a populated sheet must produce a table, an empty one must
# not - and only splitting them makes the zero-check flag meaningful.
INVARIANTS = (
    {"id": "probe-ok", "number": 1, "section": "invariant",
     "label": "inspect_file returns no error, and kind matches the extension"},
    {"id": "loads", "number": 2, "section": "invariant",
     "label": "DataLoader.add_files completes without raising"},
    {"id": "row-agreement", "number": 3, "section": "invariant",
     "label": "Per sheet: probe rows == len(df), probe columns == len(df.columns)"},
    {"id": "sheet-tables", "number": 4, "section": "invariant",
     "label": "Every non-empty probed sheet produces exactly one table"},
    {"id": "empty-skipped", "number": 4, "section": "invariant",
     "label": "Every empty probed sheet produces none"},
    {"id": "naming", "number": 5, "section": "invariant",
     "label": "Table naming follows the rule for its branch"},
    {"id": "no-collision", "number": 6, "section": "invariant",
     "label": "No tables lost to a name collision"},
    {"id": "origins", "number": 7, "section": "invariant",
     "label": "origins[table] is the uploaded filename, for every table"},
    {"id": "batch-rows", "number": 8, "section": "invariant",
     "label": "Batch total rows == sum of per-file probe rows"},
    {"id": "headers", "number": 9, "section": "invariant",
     "label": "No string column name carries leading or trailing whitespace"},
    {"id": "non-hollow", "number": 10, "section": "invariant",
     "label": "Every table has at least one row and one column"},
    {"id": "workbook-count", "number": None, "section": "folder",
     "label": "Workbook count matches the cell folder's claim"},
    {"id": "sheet-class", "number": None, "section": "folder",
     "label": "Single- vs multi-sheet matches the cell folder's claim"},
    {"id": "baseline", "number": None, "section": "baseline",
     "label": "Observed values match the recorded baseline"},
    # error_only: fires solely when something is wrong, so zero checks means a
    # clean corpus rather than an untested property. Reported as "none to report"
    # instead of the not-exercised warning, which would otherwise cry wolf on every
    # healthy run and teach the reader to ignore the flag that matters.
    {"id": "corpus", "number": None, "section": "corpus", "error_only": True,
     "label": "Cell folder name parses, and the batch is not empty"},
)

INVARIANT_SECTIONS = (
    ("invariant", "Invariants - checked on every case that reaches them"),
    ("folder", "Folder claims - Test 2 only, the cell folder's own assertion"),
    ("baseline", "Recorded baseline - single-file cases with pinned numbers"),
    ("corpus", "Corpus hygiene - reported rather than silently skipped"),
)


# ---------------------------------------------------------------------------
# Case construction
# ---------------------------------------------------------------------------

def _case(case_id, description, files, group, **expectations):
    """One unit of work: a batch of files uploaded together, plus whatever the
    folder they came from lets us assert about them.

    `expectations` is deliberately open: extension cases declare almost nothing,
    workbook cases declare workbook count and sheet class, inbox cases declare
    nothing at all. run_batch only checks the keys that are present, which is what
    lets one code path serve all three tests.
    """
    return {
        "id": case_id,
        "description": description,
        "files": list(files),
        "group": group,
        "expectations": expectations,
    }


def _supported(names):
    return sorted(n for n in names if n.lower().endswith(SUPPORTED_EXTENSIONS))


def _rel(path):
    """Corpus-relative, forward-slashed - stable across platforms so baseline keys
    written on Windows still match on anything else."""
    return os.path.relpath(path, DATA_ROOT).replace(os.sep, "/")


def discover_extension_cases(root=EXTENSIONS_ROOT):
    """Test 1: every file on its own, then mixed-extension batches.

    Each `extensions/<ext>/` folder holds files of exactly that extension, so the
    extension is itself an expectation - a .xlsx that comes back as kind "csv" has
    silently fallen through the CSV rescue path and is a failure, not a pass.
    """
    if not os.path.isdir(root):
        return []

    by_ext = {}
    for ext in ("csv", "xls", "xlsx"):
        folder = os.path.join(root, ext)
        if not os.path.isdir(folder):
            continue
        by_ext[ext] = [os.path.join(folder, n) for n in _supported(os.listdir(folder))]

    cases = []
    for ext, paths in sorted(by_ext.items()):
        for path in paths:
            cases.append(_case(
                f"extensions/{ext}/{os.path.basename(path)}",
                f"single {ext} file",
                [path],
                group="extensions",
                expected_kind=CSV_KIND if ext == "csv" else EXCEL_KIND,
            ))

    # One representative per extension, first by sorted name, so the mixed cases
    # are deterministic run to run.
    for combo in MIXED_COMBOS:
        picks = [by_ext[e][0] for e in combo if by_ext.get(e)]
        # A combination whose extensions aren't all present is dropped rather than
        # failed - an empty xls/ folder shouldn't manufacture a red row.
        if len(picks) < len(combo):
            continue
        cases.append(_case(
            "extensions/mixed/" + "+".join(combo),
            f"mixed batch: {', '.join(combo)}",
            picks,
            group="extensions-mixed",
        ))

    every = [p for paths in by_ext.values() for p in paths]
    if len(every) > 1:
        cases.append(_case(
            "extensions/mixed/all",
            f"every file in extensions/ as one batch ({len(every)} files)",
            sorted(every),
            group="extensions-mixed",
        ))
    return cases


def discover_workbook_cases(root=WORKBOOKS_ROOT):
    """Test 2: the workbook/sheet matrix.

    Layout is workbooks/<fmt>/<cell>/<example>/, four levels deep. The <cell>
    folder name carries the expectation and <example> is one batch, so a cell can
    hold many independent examples of the same shape.

    Each batch is filtered to its tree's own format. Without that, one stray file
    makes a 1wb cell look like a 2-workbook batch and reddens a matrix cell over a
    corpus problem rather than a code problem. Off-format files are reported, not
    silently dropped.
    """
    if not os.path.isdir(root):
        return []

    cases = []
    for fmt in sorted(os.listdir(root)):
        fmt_dir = os.path.join(root, fmt)
        if not os.path.isdir(fmt_dir):
            continue
        for cell in sorted(os.listdir(fmt_dir)):
            cell_dir = os.path.join(fmt_dir, cell)
            if not os.path.isdir(cell_dir):
                continue
            match = CELL_PATTERN.match(cell)
            if not match:
                cases.append(_case(
                    f"{fmt}/{cell}",
                    f"unrecognised cell folder name {cell!r}",
                    [],
                    group="workbooks",
                    unrecognised=True,
                ))
                continue

            count, at_least, sheet_class = match.groups()
            for example in sorted(os.listdir(cell_dir)):
                batch_dir = os.path.join(cell_dir, example)
                if not os.path.isdir(batch_dir):
                    continue
                names = _supported(os.listdir(batch_dir))
                matching = [n for n in names if n.lower().endswith("." + fmt)]
                off_format = [n for n in names if n not in matching]
                cases.append(_case(
                    f"{fmt}/{cell}/{example}",
                    f"{cell} as {fmt}: {example}",
                    [os.path.join(batch_dir, n) for n in matching],
                    group="workbooks",
                    expected_kind=EXCEL_KIND,
                    workbook_count=int(count),
                    workbook_count_is_minimum=at_least == "+",
                    sheet_class=sheet_class,
                    ignored_files=off_format,
                    cell=cell,
                    fmt=fmt,
                ))
    return cases


def discover_inbox_cases(root=INBOX_ROOT):
    """Test 3: whatever a developer dropped in.

    No convention: a loose file is one case, a subfolder is one batch of every
    supported file inside it (mixed extensions welcome - that's the point). Since
    nothing has been declared about these files, only the folder-independent
    invariants apply.
    """
    if not os.path.isdir(root):
        return []

    cases = []
    entries = sorted(os.listdir(root))

    for name in entries:
        path = os.path.join(root, name)
        if os.path.isfile(path) and name.lower().endswith(SUPPORTED_EXTENSIONS):
            cases.append(_case(
                f"inbox/{name}", "inbox: single file", [path], group="inbox",
            ))

    for name in entries:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        contents = sorted(os.listdir(folder))
        supported = _supported(contents)
        if not supported:
            continue
        ignored = [
            c for c in contents
            if c not in supported and os.path.isfile(os.path.join(folder, c))
        ]
        cases.append(_case(
            f"inbox/{name}/",
            f"inbox: batch of {len(supported)} file(s)",
            [os.path.join(folder, n) for n in supported],
            group="inbox",
            ignored_files=ignored,
        ))
    return cases


def discover_all():
    return {
        "extensions": discover_extension_cases(),
        "workbooks": discover_workbook_cases(),
        "inbox": discover_inbox_cases(),
    }


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------

def _probe_and_load(paths):
    """Run both halves of the pipeline over one batch.

    DataLoader and the probe both print progress lines; swallow them so a
    parametrised run stays readable.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        infos = [
            inspect_file(p, os.path.basename(p), os.path.getsize(p))
            for p in paths
        ]
        loader = DataLoader()
        loader.add_files(paths)
        tables = loader.tables()
        origins = dict(loader.origins)
    return infos, tables, origins


def _sheets_of(info):
    """Probe sheets for a workbook; one synthetic entry for a CSV, which has no
    sheets but still has a row/column count worth checking the same way."""
    if info.get("kind") == CSV_KIND:
        return [{
            "name": None,
            "rows": info.get("rows"),
            "columns": info.get("columns"),
            "empty": not info.get("rows"),
        }]
    return info.get("sheets", [])


def _expected_table_name(sheet_name, filename, multi):
    """Mirror of DataLoader._add_excel's naming, kept here as an independent
    statement of the rule rather than a call into the code under test."""
    if not multi:
        return filename
    stem, ext = os.path.splitext(filename)
    return f"{sheet_name} ({stem}){ext}"


def run_batch(case):
    """Execute one case and return a result dict.

    Never raises: a case that blows up comes back with `error` set and
    `passed` False, so one bad file can't abort a whole run - the same contract
    inspect_file itself honours.
    """
    started = time.time()
    result = {
        "case_id": case["id"],
        "description": case["description"],
        "group": case["group"],
        "files": [_rel(p) for p in case["files"]],
        "ignored_files": list(case["expectations"].get("ignored_files") or []),
        "checks": [],
        "error": None,
        "observed": {},
    }

    def check(name, passed, expected, actual, note=None, invariant=None):
        """`invariant` ties the check to a row of INVARIANTS, so the report can
        group by property rather than by the per-file check name."""
        result["checks"].append({
            "name": name, "passed": bool(passed), "invariant": invariant,
            "expected": expected, "actual": actual, "note": note,
        })

    exp = case["expectations"]

    if exp.get("unrecognised"):
        check("cell-folder-name-recognised", False,
              "matches <n>[+]wb-<1|multi>sheet", case["id"].rsplit("/", 1)[-1],
              "folder is not part of any matrix cell and was not tested",
              invariant="corpus")
        result["passed"] = False
        result["duration_ms"] = 0
        return result

    if not case["files"]:
        check("batch-not-empty", False, ">=1 supported file", 0,
              "no supported files found in this folder", invariant="corpus")
        result["passed"] = False
        result["duration_ms"] = 0
        return result

    try:
        infos, tables, origins = _probe_and_load(case["files"])
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        check("loads-without-raising", False, "no exception", result["error"],
              invariant="loads")
        result["passed"] = False
        result["duration_ms"] = int((time.time() - started) * 1000)
        return result

    check("loads-without-raising", True, "no exception", "no exception",
          invariant="loads")

    # ---- per-file invariants -------------------------------------------------
    batch_probe_rows = 0
    expected_names = []
    per_file = []

    for path, info in zip(case["files"], infos):
        filename = os.path.basename(path)
        ext = os.path.splitext(filename)[1].lower()

        # 1. Readable, and the right kind. A .xlsx reporting "csv" means the
        #    workbook failed to parse and fell through the CSV rescue in
        #    inspect_file - a plausible-looking row count hiding a broken file.
        check(f"{filename}: no probe error", "error" not in info,
              "no error", info.get("error", "none"), invariant="probe-ok")
        wanted_kind = exp.get("expected_kind") or (
            CSV_KIND if ext == ".csv" else EXCEL_KIND
        )
        check(f"{filename}: kind is {wanted_kind}", info.get("kind") == wanted_kind,
              wanted_kind, info.get("kind"), invariant="probe-ok")

        sheets = _sheets_of(info)
        populated = [s for s in sheets if not s.get("empty")]
        multi = len(populated) > 1
        batch_probe_rows += sum(s.get("rows") or 0 for s in populated)

        for sheet in populated:
            name = (
                filename if info.get("kind") == CSV_KIND
                else _expected_table_name(sheet["name"], filename, multi)
            )
            expected_names.append(name)

            # 3. The headline invariant: what the upload screen promises must equal
            #    what the analysis loads. This is why workbook_probe scans cells
            #    instead of trusting the stored dimension record.
            df = tables.get(name)
            # Asserted positively on every populated sheet rather than only when one
            # goes missing. The outcome is identical, but a check that fires solely
            # on failure emits nothing on a green run, and invariant 4 would then be
            # indistinguishable in the report from one the corpus never exercises.
            check(f"{name}: table produced", df is not None,
                  "a table", "missing" if df is None else "a table",
                  invariant="sheet-tables")
            if df is None:
                continue
            check(f"{name}: probe rows == loaded rows",
                  sheet["rows"] == len(df), sheet["rows"], len(df),
                  invariant="row-agreement")
            check(f"{name}: probe cols == loaded cols",
                  sheet["columns"] == len(df.columns),
                  sheet["columns"], len(df.columns), invariant="row-agreement")

        # 4. Empty sheets are skipped by DataLoader, so they must not appear.
        for sheet in sheets:
            if not sheet.get("empty") or info.get("kind") == CSV_KIND:
                continue
            name = _expected_table_name(sheet["name"], filename, multi)
            check(f"{filename}: empty sheet {sheet['name']!r} skipped",
                  name not in tables, "not loaded",
                  "loaded" if name in tables else "not loaded",
                  invariant="empty-skipped")

        per_file.append({
            "name": filename,
            "rel": _rel(path),
            "size": os.path.getsize(path),
            "kind": info.get("kind"),
            "sheets": [
                {k: s.get(k) for k in ("name", "rows", "columns", "empty")}
                for s in sheets
            ],
            "probe_rows": sum(s.get("rows") or 0 for s in populated),
        })

    # ---- batch invariants ----------------------------------------------------
    # 5. Naming: the exact set the rule predicts, no more and no less.
    check("table names match the naming rule",
          sorted(tables) == sorted(expected_names),
          sorted(expected_names), sorted(tables), invariant="naming")

    # 6. Uniqueness. The 2+wb cells exist for this: two workbooks holding a sheet
    #    of the same name must not collide, which is what the (stem) suffix buys.
    #
    #    Measured on the *loaded* tables, not on expected_names. Checking that the
    #    predicted names are distinct would be a tautology - this module generates
    #    them from (sheet, stem) pairs that are distinct by construction, so it
    #    could never fail. What can actually go wrong is two sheets landing on one
    #    key in DataLoader's dict and one silently overwriting the other, and that
    #    only shows up as a shortfall in the real table count.
    check("no tables lost to a name collision",
          len(tables) == len(expected_names),
          f"{len(expected_names)} tables", f"{len(tables)} tables",
          invariant="no-collision")

    # 7. Every table traceable to the file it came from.
    uploaded = {os.path.basename(p) for p in case["files"]}
    bad_origin = {t: origins.get(t) for t in tables if origins.get(t) not in uploaded}
    check("origins map every table to an uploaded file", not bad_origin,
          "all mapped", bad_origin or "all mapped", invariant="origins")

    # 8. Invariant 3 rolled up to the number the upload screen shows for a batch.
    loaded_rows = sum(len(df) for df in tables.values())
    check("batch rows: probe total == loaded total",
          batch_probe_rows == loaded_rows, batch_probe_rows, loaded_rows,
          invariant="batch-rows")

    # 9. Headers are stripped, but only the string ones - Excel headers can be
    #    numeric or blank and must survive untouched rather than crash.
    unstripped = [
        (t, c) for t, df in tables.items() for c in df.columns
        if isinstance(c, str) and c != c.strip()
    ]
    check("string headers are stripped", not unstripped,
          "none unstripped", unstripped or "none unstripped", invariant="headers")

    # 10. A table that loaded as nothing is not a table.
    hollow = {t: (len(df), len(df.columns)) for t, df in tables.items()
              if df.empty or len(df.columns) == 0}
    check("every table has rows and columns", not hollow,
          "all non-empty", hollow or "all non-empty", invariant="non-hollow")

    # ---- folder-derived expectations (Tests 1 and 2 only) --------------------
    if "workbook_count" in exp:
        actual = len(case["files"])
        wanted = exp["workbook_count"]
        if exp.get("workbook_count_is_minimum"):
            check(f"workbook count >= {wanted}", actual >= wanted,
                  f">= {wanted}", actual, invariant="workbook-count")
        else:
            check(f"workbook count == {wanted}", actual == wanted, wanted, actual,
                  invariant="workbook-count")

    if exp.get("sheet_class"):
        wants_multi = exp["sheet_class"] == "multi"
        for path, info in zip(case["files"], infos):
            populated = [s for s in _sheets_of(info) if not s.get("empty")]
            filename = os.path.basename(path)
            if wants_multi:
                check(f"{filename}: multiple populated sheets",
                      len(populated) > 1, ">1", len(populated),
                      invariant="sheet-class")
            else:
                check(f"{filename}: exactly one populated sheet",
                      len(populated) == 1, 1, len(populated),
                      invariant="sheet-class")
        # The naming branch each class is supposed to take. Asserted separately
        # from the name set above so a failure says which rule broke.
        if wants_multi:
            check("multisheet names carry the (stem) suffix",
                  all(" (" in n for n in expected_names),
                  "all suffixed", [n for n in expected_names if " (" not in n] or "all suffixed",
                  invariant="naming")
        else:
            check("single-sheet names collapse to the bare filename",
                  set(expected_names) == uploaded, sorted(uploaded), sorted(expected_names),
                  invariant="naming")

    result["observed"] = {
        "files": per_file,
        "tables": {t: {"rows": len(df), "columns": len(df.columns)}
                   for t, df in tables.items()},
        "table_count": len(tables),
        "probe_rows": batch_probe_rows,
        "loaded_rows": loaded_rows,
    }
    result["passed"] = all(c["passed"] for c in result["checks"])
    result["duration_ms"] = int((time.time() - started) * 1000)
    return result


def failures(result):
    """The checks that failed, for a test assertion message or a report row."""
    return [c for c in result["checks"] if not c["passed"]]


def describe_failures(result):
    if result.get("error"):
        return f"{result['case_id']}: {result['error']}"
    lines = [f"{result['case_id']}: {len(failures(result))} check(s) failed"]
    for c in failures(result):
        lines.append(f"  - {c['name']}: expected {c['expected']!r}, got {c['actual']!r}"
                     + (f" ({c['note']})" if c.get("note") else ""))
    return "\n".join(lines)


def summarise_checks(results):
    """Roll a run's checks up per INVARIANTS row: what was actually verified.

    Two things this answers that a case count cannot. Which properties carried the
    run - invariant 3 fires per sheet and dominates, invariant 6 fires once per
    batch. And, more to the point, which fired *not at all*: `exercised` False means
    no case in this corpus put that property to the test, so its green status is an
    absence of evidence rather than evidence of correctness.

    Any check carrying an unknown or missing id lands in `unclassified` rather than
    being dropped, so a check added later cannot quietly vanish from the summary -
    it shows up as an unnamed row instead.
    """
    known = {inv["id"] for inv in INVARIANTS}
    tally = {inv["id"]: {"checks": 0, "passed": 0, "failed": 0, "cases": set()}
             for inv in INVARIANTS}
    unclassified = {}

    for result in results:
        for c in result.get("checks", []):
            key = c.get("invariant")
            bucket = (tally[key] if key in known
                      else unclassified.setdefault(
                          key or "(untagged)",
                          {"checks": 0, "passed": 0, "failed": 0,
                           "cases": set(), "names": set()}))
            bucket["checks"] += 1
            bucket["passed" if c["passed"] else "failed"] += 1
            bucket["cases"].add(result["case_id"])
            if key not in known:
                bucket["names"].add(c["name"])

    rows = []
    for inv in INVARIANTS:
        t = tally[inv["id"]]
        rows.append({
            "error_only": False, **inv,
            "checks": t["checks"], "passed": t["passed"], "failed": t["failed"],
            "cases": len(t["cases"]), "exercised": t["checks"] > 0,
        })

    extra = [
        {"id": key, "number": None, "section": "unclassified",
         "label": ", ".join(sorted(t["names"])[:3]),
         "checks": t["checks"], "passed": t["passed"], "failed": t["failed"],
         "cases": len(t["cases"]), "exercised": True}
        for key, t in sorted(unclassified.items())
    ]

    return {
        "rows": rows,
        "unclassified": extra,
        "total_checks": sum(r["checks"] for r in rows) + sum(r["checks"] for r in extra),
        "total_failed": sum(r["failed"] for r in rows) + sum(r["failed"] for r in extra),
        "not_exercised": [r["id"] for r in rows
                          if not r["exercised"] and not r["error_only"]],
    }


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def sha256(path, _chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def baseline_path_for(group):
    """Inbox recordings are kept beside the (gitignored) inbox files. Letting them
    into the committed baseline would pin expectations against files nobody else
    has, so every other clone would fail on entries it cannot satisfy."""
    return INBOX_BASELINE_PATH if group == "inbox" else BASELINE_PATH


def load_baseline(path=BASELINE_PATH):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def baseline_entry(result):
    """One recordable entry per single-file case. Multi-file batches derive their
    numbers from their members, so recording them too would duplicate the same
    facts in two places and let them drift apart."""
    files = result["observed"].get("files") or []
    if len(files) != 1:
        return None
    f = files[0]
    return f["rel"], {
        "sha256": sha256(os.path.join(DATA_ROOT, f["rel"])),
        "kind": f["kind"],
        "rows": f["probe_rows"],
        "sheets": f["sheets"],
        "tables": result["observed"]["tables"],
    }


def baseline_status(result, baseline):
    """`recorded` / `unrecorded` / `stale`, plus any mismatch against the record.

    Keying on sha256 means a changed file reports `stale` rather than failing
    against numbers that describe a different file - a recorded expectation is
    only meaningful for the exact bytes it was recorded from.
    """
    entry = baseline_entry(result)
    if entry is None:
        return "n/a", []
    key, observed = entry
    recorded = baseline.get(key)
    if recorded is None:
        return "unrecorded", []
    if recorded.get("sha256") != observed["sha256"]:
        return "stale", []
    drift = [
        {"field": field, "expected": recorded.get(field), "actual": observed.get(field)}
        for field in ("kind", "rows", "sheets", "tables")
        if recorded.get(field) != observed.get(field)
    ]
    return "recorded", drift


def record_baseline(results, path=BASELINE_PATH):
    """Merge recordable entries into `path`, preserving anything already there for
    files this run didn't see."""
    data = load_baseline(path)
    written = 0
    for result in results:
        if not result.get("passed"):
            continue
        entry = baseline_entry(result)
        if entry is None:
            continue
        key, observed = entry
        data[key] = observed
        written += 1
    if written:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(data.items())), f, indent=2)
            f.write("\n")
    return written
