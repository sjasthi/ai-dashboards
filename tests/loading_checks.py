"""Shared definitions for the file-loading test cases.

Deliberately not a test module: both pytest (tests/test_file_loading.py,
tests/test_upload_api.py) and the standalone demo runner
(scripts/run_test_plan.py) import this, so there is exactly one definition of
where the fixtures live and what each case asserts. The demo runner is not
executed by pytest and cannot use its fixtures, which is why this lives in a
plain module rather than in conftest.py -- conftest delegates here instead.
"""

import contextlib
import io
import json
import os
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(TESTS_DIR, "fixtures")
EXCEL_DIR = os.path.join(FIXTURE_DIR, "excel")
CSV_DIR = os.path.join(FIXTURE_DIR, "csv")
BROKEN_DIR = os.path.join(FIXTURE_DIR, "broken")
MANIFEST_PATH = os.path.join(FIXTURE_DIR, "manifest.json")

# Searched in order when a case names a file. A case is resolved by name alone
# rather than by extension, because a fixture's extension deliberately lies in
# some cases (a CSV saved as .xlsx) and because mixed-batch cases name files
# from more than one directory at once.
SEARCH_DIRS = (EXCEL_DIR, CSV_DIR, BROKEN_DIR)

# Bumped when the manifest's shape changes in a way that older readers would
# misinterpret. Adding cases is not a shape change.
SUPPORTED_SCHEMA_VERSION = 1


def load_manifest(path=MANIFEST_PATH):
    """Read manifest.json and return the parsed dict.

    Fails loudly on an unexpected schema_version rather than silently reading
    fields that may have moved: a manifest this suite cannot interpret should
    stop the run, not produce green results from misread expectations.
    """
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    version = manifest.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version {version!r} is not supported "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )
    return manifest


def load_cases(path=MANIFEST_PATH):
    """The manifest's case list, validated for the fields every case needs.

    Checked here rather than in each test so a typo in the manifest surfaces as
    one clear error at collection time instead of a confusing KeyError inside
    whichever assertion happened to touch the missing field first.
    """
    cases = load_manifest(path)["cases"]

    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not case_id:
            raise ValueError(f"{path}: a case is missing its 'id'")
        if case_id in seen:
            raise ValueError(f"{path}: duplicate case id {case_id!r}")
        seen.add(case_id)

        for field in ("description", "files", "expect"):
            if field not in case:
                raise ValueError(f"{path}: case {case_id!r} is missing {field!r}")
        if not case["files"]:
            raise ValueError(f"{path}: case {case_id!r} lists no files")

    return cases


def case_levels(case):
    """Which suites should run this case. Both, unless the case says otherwise.

    A few cases only exist at the HTTP boundary -- a malformed `selections`
    string cannot be handed to DataLoader.add_files, which takes an already
    parsed dict -- so they are marked ["api"].
    """
    return tuple(case.get("levels") or ("unit", "api"))


def rows_must_agree(case):
    """Whether workbook_probe's row total must equal what DataLoader loaded.

    True only when the whole workbook is loaded. Narrowing the selection makes
    the two diverge by design, so asserting agreement there would be asserting a
    bug. An explicit `expect.rows_agree` overrides this.
    """
    expect = case.get("expect", {})
    if "rows_agree" in expect:
        return bool(expect["rows_agree"])
    return case.get("selections") is None


def is_xfail(case):
    """True when `expect` records correct-but-not-yet-implemented behaviour."""
    return bool(case.get("xfail"))


def is_optional(case):
    """True when the case's fixtures are not committed and may be absent."""
    return bool(case.get("optional"))


def find_fixture(name, search_dirs=SEARCH_DIRS):
    """Absolute path of a fixture, or None if no search dir holds it.

    Raises on an ambiguous name: two fixtures sharing a basename in different
    directories would make a case's meaning depend on SEARCH_DIRS order, which
    is exactly the kind of quiet mismatch this suite exists to catch.
    """
    hits = [
        os.path.join(d, name) for d in search_dirs
        if os.path.exists(os.path.join(d, name))
    ]
    if len(hits) > 1:
        raise ValueError(f"fixture {name!r} is ambiguous: {hits}")
    return hits[0] if hits else None


def case_paths(case, search_dirs=SEARCH_DIRS):
    """Absolute paths for a case's files, in the order the case lists them.

    Order matters: /api/inspect returns one result per file positionally, and
    the manifest's `expect.inspect` list is matched against it index by index.
    """
    paths = []
    for name in case["files"]:
        path = find_fixture(name, search_dirs)
        if path is None:
            raise FileNotFoundError(
                f"case {case['id']!r} names {name!r}, which is not in any of "
                f"{[os.path.basename(d) for d in search_dirs]}"
            )
        paths.append(path)
    return paths


def missing_files(case, search_dirs=SEARCH_DIRS):
    """Names from `case['files']` that are not on disk.

    Fixtures are committed, but the scale fixture is gitignored and built on
    demand, so a case can legitimately be unrunnable on a fresh clone. Callers
    skip rather than fail when this is non-empty.
    """
    return [
        name for name in case["files"]
        if find_fixture(name, search_dirs) is None
    ]


# --------------------------------------------------------------------------
# Running a case
#
# run_case collects every mismatch rather than stopping at the first, because
# both consumers want the whole picture: pytest reports them together instead of
# forcing a fix-and-rerun cycle per assertion, and the HTML report shows what
# actually differed. It never raises for an assertion failure -- a failure is
# data in the returned dict. Only a broken manifest raises.
# --------------------------------------------------------------------------

def _load(paths, selections):
    """DataLoader.add_files with its progress prints muted.

    DataLoader prints a line per sheet, which would bury the test output.
    """
    from app.data.data_loader import DataLoader

    loader = DataLoader()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        loader.add_files(paths, selections)
        tables = loader.tables()
    return tables, dict(loader.origins)


def _inspect(paths):
    from app.data import workbook_probe

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return [
            workbook_probe.inspect_file(p, os.path.basename(p), os.path.getsize(p))
            for p in paths
        ]


def _compare_inspect(actual, expected):
    """Compare probe output field by field, ignoring keys the case doesn't pin.

    A case asserts only the fields it cares about: `size` is a byte count nobody
    should hand-maintain, and error *wording* is checked via has_error rather
    than pinned verbatim so it can be reworded without breaking the suite.
    """
    problems = []
    if len(actual) != len(expected):
        return [f"inspect: got {len(actual)} entries, expected {len(expected)}"]

    for got, want in zip(actual, expected):
        label = want.get("name", "?")
        for key, value in want.items():
            if key == "has_error":
                has = bool(got.get("error"))
                if has != value:
                    problems.append(
                        f"inspect[{label}]: error present={has}, expected {value}"
                    )
            elif key == "sheets":
                got_sheets = got.get("sheets") or []
                if len(got_sheets) != len(value):
                    problems.append(
                        f"inspect[{label}].sheets: got {len(got_sheets)}, "
                        f"expected {len(value)}"
                    )
                    continue
                for g, w in zip(got_sheets, value):
                    for k, v in w.items():
                        if g.get(k) != v:
                            problems.append(
                                f"inspect[{label}].sheet[{w.get('name')}].{k}: "
                                f"{g.get(k)!r} != {v!r}"
                            )
            elif got.get(key) != value:
                problems.append(f"inspect[{label}].{key}: {got.get(key)!r} != {value!r}")
    return problems


def run_case(case, search_dirs=SEARCH_DIRS):
    """Run one manifest case at unit level.

    Returns a result dict: id, group, description, status
    ('passed' | 'failed' | 'skipped' | 'xfailed' | 'xpassed'), problems,
    duration_ms, and a skip/expected-failure reason. Never raises on a failed
    assertion -- the caller decides what a failure means.
    """
    started = time.perf_counter()
    result = {
        "id": case["id"],
        "group": case.get("group"),
        "description": case.get("description", ""),
        "files": list(case["files"]),
        "problems": [],
        "reason": None,
        "known_issue": case.get("known_issue"),
        # Kept for the HTML report, which shows expected beside actual rather than
        # only the diff -- a reviewer reading a failure wants both sides.
        "expected_tables": case["expect"].get("tables"),
        "actual_tables": None,
    }

    def finish(status):
        result["status"] = status
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    if "unit" not in case_levels(case):
        result["reason"] = "case is API-level only"
        return finish("skipped")

    absent = missing_files(case, search_dirs)
    if absent:
        if is_optional(case):
            result["reason"] = (
                f"optional fixture not built: {', '.join(absent)} "
                "(python scripts/make_test_fixtures.py --scale)"
            )
            return finish("skipped")
        result["problems"].append(f"missing fixtures: {', '.join(absent)}")
        return finish("failed")

    expect = case["expect"]
    paths = case_paths(case, search_dirs)
    problems = result["problems"]

    # A case may expect the loader itself to blow up. Assert that first: there is
    # nothing else to compare if no tables were ever produced.
    expected_exc = expect.get("loader_raises")
    tables = origins = None
    try:
        tables, origins = _load(paths, case.get("selections"))
        if expected_exc:
            problems.append(
                f"expected DataLoader to raise {expected_exc}, but it succeeded "
                f"with {len(tables)} table(s)"
            )
    except Exception as exc:
        actual_exc = type(exc).__name__
        if not expected_exc:
            problems.append(f"DataLoader raised {actual_exc}: {exc}")
        elif actual_exc != expected_exc:
            problems.append(
                f"DataLoader raised {actual_exc}, expected {expected_exc}"
            )

    if tables is not None:
        actual = {
            name: {"rows": len(df), "columns": len(df.columns)}
            for name, df in tables.items()
        }
        result["actual_tables"] = actual
        if "tables" in expect:
            if actual != expect["tables"]:
                problems.extend(_diff_tables(actual, expect["tables"]))

        if "origins" in expect and origins != expect["origins"]:
            problems.append(f"origins: {origins!r} != {expect['origins']!r}")

        for name, want_cols in (expect.get("columns") or {}).items():
            if name not in tables:
                problems.append(f"columns: no table named {name!r}")
                continue
            got_cols = [str(c) for c in tables[name].columns]
            if got_cols != [str(c) for c in want_cols]:
                problems.append(f"columns[{name}]: {got_cols!r} != {want_cols!r}")

    if expect.get("inspect") is not None:
        problems.extend(_compare_inspect(_inspect(paths), expect["inspect"]))

    # Probe/loader row agreement. The single most valuable assertion here: it
    # needs no hand-labelling, so it holds for any file added later, and it
    # targets the documented bug class where a sheet's recorded extent overstates
    # its real data.
    if tables is not None and expect.get("status") == 200:
        probe_rows = sum((i.get("rows") or 0) for i in _inspect(paths))
        loaded_rows = sum(len(df) for df in tables.values())
        agree = probe_rows == loaded_rows
        if rows_must_agree(case) and not agree:
            problems.append(
                f"probe reported {probe_rows} rows but DataLoader loaded "
                f"{loaded_rows} - the UI would promise rows the analysis "
                f"never delivers"
            )
        elif not rows_must_agree(case) and agree and expect.get("tables"):
            problems.append(
                f"expected probe ({probe_rows}) and loaded ({loaded_rows}) row "
                "counts to diverge because the selection was narrowed, but they "
                "match - has rows_agree gone stale?"
            )

    if is_xfail(case):
        result["reason"] = case.get("known_issue") or "expected failure"
        return finish("xfailed" if problems else "xpassed")
    return finish("failed" if problems else "passed")


def _diff_tables(actual, expected):
    """Readable table-map difference: missing, unexpected, then shape mismatches."""
    problems = []
    for name in expected:
        if name not in actual:
            problems.append(f"tables: missing {name!r} (expected {expected[name]})")
    for name in actual:
        if name not in expected:
            problems.append(f"tables: unexpected {name!r} ({actual[name]})")
    for name in expected:
        if name in actual and actual[name] != expected[name]:
            problems.append(
                f"tables[{name}]: {actual[name]} != {expected[name]}"
            )
    return problems


def run_all(cases=None, search_dirs=SEARCH_DIRS):
    """Run every case, returning the list of result dicts."""
    if cases is None:
        cases = load_cases()
    return [run_case(case, search_dirs) for case in cases]


def observe_case(case, search_dirs=SEARCH_DIRS):
    """Build an `expect` block from what the pipeline currently does.

    Used only by `run_test_plan.py --record`, to add new fixtures without
    hand-transcribing their shapes. Recording is not verification: the point of
    --record is to turn "20 new files" into a reviewable diff, and the review is
    where the expectation is actually established.
    """
    paths = case_paths(case, search_dirs)
    tables, origins = _load(paths, case.get("selections"))
    inspects = _inspect(paths)

    expect = {
        "status": 200,
        "tables": {
            name: {"rows": len(df), "columns": len(df.columns)}
            for name, df in tables.items()
        },
        "origins": origins,
        "inspect": [],
    }
    for i in inspects:
        entry = {"name": i["name"], "kind": i["kind"], "rows": i["rows"]}
        if i["kind"] == "csv":
            entry["columns"] = i["columns"]
            entry["sheets"] = []
        else:
            entry["sheets"] = [
                {"name": s["name"], "rows": s["rows"],
                 "columns": s["columns"], "empty": s["empty"]}
                for s in i["sheets"]
            ]
        expect["inspect"].append(entry)
    return expect


def record_manifest(path=MANIFEST_PATH, search_dirs=SEARCH_DIRS):
    """Rewrite recordable cases' expectations in place; return what changed.

    Refuses to touch xfail cases. Their `expect` blocks hold the CORRECT
    behaviour, which by definition differs from what the code does now -- so
    recording over them would erase the bug report and replace it with the bug.
    """
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    changed, skipped = [], []
    for case in manifest["cases"]:
        if is_xfail(case):
            skipped.append((case["id"], "xfail: expectation is hand-written"))
            continue
        if "unit" not in case_levels(case):
            skipped.append((case["id"], "API-level only"))
            continue
        if case["expect"].get("status") != 200:
            skipped.append((case["id"], "expects a non-200 status"))
            continue
        if missing_files(case, search_dirs):
            skipped.append((case["id"], "fixture not present"))
            continue

        fresh = observe_case(case, search_dirs)
        for key in ("columns", "rows_agree", "loader_raises", "detail_contains"):
            if key in case["expect"]:
                fresh[key] = case["expect"][key]
        if case["expect"].get("inspect") is None:
            fresh["inspect"] = None
        if fresh != case["expect"]:
            changed.append(case["id"])
            case["expect"] = fresh

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=True)
            fh.write("\n")
    return changed, skipped


def library_versions():
    """Versions the results depend on, for the report header.

    A green report is only meaningful against known reader versions: openpyxl and
    xlrd are where the format-specific behaviour lives.
    """
    import platform

    versions = {"python": platform.python_version(), "platform": platform.platform()}
    for module in ("pandas", "openpyxl", "xlrd", "xlwt"):
        try:
            mod = __import__(module)
        except ImportError:
            versions[module] = "not installed"
            continue
        # xlwt exposes __VERSION__, not __version__. Reporting "not installed"
        # for a package that is present would send someone chasing a dependency
        # problem that does not exist.
        for attr in ("__version__", "__VERSION__", "VERSION"):
            value = getattr(mod, attr, None)
            if value:
                versions[module] = str(value)
                break
        else:
            versions[module] = "installed (version unknown)"
    return versions


def summarize(results):
    """Count results by status, for a report header or a console summary."""
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    counts["total"] = len(results)
    counts["duration_ms"] = round(sum(r["duration_ms"] for r in results), 1)
    return counts
