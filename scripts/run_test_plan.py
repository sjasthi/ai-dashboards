"""Run the file-loading test plan and write a report.

Same checks as the pytest suites - both import tests/loading_checks - but this one
is built to be *shown*: a live table on the console and a self-contained HTML page
that walks the matrix cell by cell with the generated table names visible.

    python scripts/run_test_plan.py                 run everything, write the report
    python scripts/run_test_plan.py --strict        a missing required corpus is an error
    python scripts/run_test_plan.py --record        pin observed numbers into the baseline
    python scripts/run_test_plan.py --only workbooks

The HTML has no external assets, so it can be emailed or opened from a USB stick.
"""

import argparse
import datetime as dt
import html
import json
import os
import platform
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import loading_checks  # noqa: E402

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_results"
)
HTML_PATH = os.path.join(OUTPUT_DIR, "file_loading_report.html")
JSON_PATH = os.path.join(OUTPUT_DIR, "file_loading_report.json")

# inbox/ is exempt from --strict: gitignored by design, so empty is its normal
# state on every machine but the one that filled it.
REQUIRED_GROUPS = ("extensions", "workbooks")

CELL_ORDER = ("1wb-1sheet", "1wb-multisheet", "2+wb-1sheet", "2+wb-multisheet")


def library_versions():
    versions = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("pandas", "openpyxl", "xlrd"):
        try:
            versions[name] = __import__(name).__version__
        except Exception:
            versions[name] = "not installed"
    return versions


def collect(only=None):
    groups = loading_checks.discover_all()
    if only:
        groups = {k: v for k, v in groups.items() if k == only}
    return groups


def run(groups, record=False):
    results = {}
    for group, cases in groups.items():
        collected = []
        for case in cases:
            result = loading_checks.run_batch(case)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"  [{status}] {result['case_id']}  ({result['duration_ms']} ms)")
            if not result["passed"]:
                for failure in loading_checks.failures(result):
                    print(f"          {failure['name']}: "
                          f"expected {failure['expected']!r}, got {failure['actual']!r}")
            collected.append(result)
        results[group] = collected

    if record:
        for group, collected in results.items():
            path = loading_checks.baseline_path_for(group)
            written = loading_checks.record_baseline(collected, path)
            if written:
                print(f"\nRecorded {written} entr{'y' if written == 1 else 'ies'} "
                      f"-> {os.path.relpath(path)}")
    return results


def annotate_baselines(results):
    """Tag each result with its baseline status so the report can show which files
    are pinned to exact numbers and which are only invariant-checked.

    A `recorded` file also gets drift folded back in as a real check. Drift only
    arises when the sha *matches* and the numbers moved anyway - the file is
    unchanged, so the code changed - which is a regression, not a note. Appending it
    to `checks` rather than handling it separately means the badge, the failure
    detail, the summary table and the exit code all pick it up with no further
    plumbing, and the report stops showing a green `recorded` chip over a red fact.

    Runner-only: pytest never calls this, so `test_matches_recorded_baseline` keeps
    its own independent assertion and nothing here can weaken it.
    """
    for group, collected in results.items():
        baseline = loading_checks.load_baseline(loading_checks.baseline_path_for(group))
        for result in collected:
            status, drift = loading_checks.baseline_status(result, baseline)
            result["baseline_status"] = status
            result["baseline_drift"] = drift
            if status != "recorded":
                continue
            result["checks"].append({
                "name": "matches recorded baseline",
                "passed": not drift,
                "invariant": "baseline",
                "expected": ({d["field"]: d["expected"] for d in drift} if drift
                             else "recorded values"),
                "actual": ({d["field"]: d["actual"] for d in drift} if drift
                           else "recorded values"),
                "note": ("the file is byte-identical to its recording, so these "
                         "numbers moved because the code changed" if drift else None),
            })
            result["passed"] = all(c["passed"] for c in result["checks"])


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def e(value):
    return html.escape(str(value))


def badge(passed):
    """Status colour never travels alone - the glyph and the word carry the meaning
    for anyone who can't separate the two hues."""
    return (f'<span class="badge {"pass" if passed else "fail"}">'
            f'{"&#10003;" if passed else "&#10007;"} {"PASS" if passed else "FAIL"}</span>')


def tile(label, value, tone=""):
    return (f'<div class="tile {tone}"><div class="tile-label">{e(label)}</div>'
            f'<div class="tile-value">{e(value)}</div></div>')


def failure_detail(result):
    if result["passed"]:
        return ""
    rows = []
    if result.get("error"):
        rows.append(f"<li><code>{e(result['error'])}</code></li>")
    for failure in loading_checks.failures(result):
        note = f" &mdash; {e(failure['note'])}" if failure.get("note") else ""
        rows.append(
            f"<li><strong>{e(failure['name'])}</strong>{note}<br>"
            f"<span class='muted'>expected</span> <code>{e(failure['expected'])}</code> "
            f"<span class='muted'>got</span> <code>{e(failure['actual'])}</code></li>"
        )
    return f'<ul class="failures">{"".join(rows)}</ul>'


def file_rows(results):
    rows = []
    for result in results:
        observed = result.get("observed") or {}
        for f in observed.get("files", []):
            sheets = f["sheets"]
            sheet_text = (
                "&mdash;" if f["kind"] == "csv"
                else ", ".join(f"{e(s['name'])} ({s['rows']}&times;{s['columns']})"
                               for s in sheets)
            )
            rows.append(
                "<tr>"
                f"<td>{badge(result['passed'])}</td>"
                f"<td class='mono'>{e(f['rel'])}</td>"
                f"<td class='num'>{f['size']:,}</td>"
                f"<td>{e(f['kind'])}</td>"
                f"<td>{sheet_text}</td>"
                f"<td class='num'>{f['probe_rows']:,}</td>"
                f"<td><span class='chip {e(result.get('baseline_status', 'n/a'))}'>"
                f"{e(result.get('baseline_status', 'n/a'))}</span></td>"
                f"<td class='num'>{result['duration_ms']}</td>"
                "</tr>"
            )
            rows.append(f"<tr class='detail'><td></td><td colspan='7'>"
                        f"{failure_detail(result)}</td></tr>"
                        if not result["passed"] else "")
    return "".join(r for r in rows if r)


def batch_rows(results):
    rows = []
    for result in results:
        observed = result.get("observed") or {}
        rows.append(
            "<tr>"
            f"<td>{badge(result['passed'])}</td>"
            f"<td class='mono'>{e(result['case_id'])}</td>"
            f"<td>{e(', '.join(os.path.basename(f) for f in result['files']))}</td>"
            f"<td class='num'>{observed.get('table_count', 0)}</td>"
            f"<td class='num'>{observed.get('probe_rows', 0):,}</td>"
            f"<td class='num'>{observed.get('loaded_rows', 0):,}</td>"
            f"<td class='num'>{result['duration_ms']}</td>"
            "</tr>"
        )
        if not result["passed"]:
            rows.append(f"<tr class='detail'><td></td><td colspan='6'>"
                        f"{failure_detail(result)}</td></tr>")
    return "".join(rows)


def matrix_section(results):
    """The 2x2 grid per format, with each cell's generated table names spelled out.

    The names are the point of showing this at all: they are the visible output of
    the naming rule in DataLoader, and the difference between the single-sheet and
    multi-sheet branches is legible at a glance.
    """
    by_cell = {}
    for result in results:
        parts = result["case_id"].split("/")
        if len(parts) < 3:
            continue
        by_cell.setdefault((parts[0], parts[1]), []).append(result)

    formats = sorted({fmt for fmt, _ in by_cell})
    blocks = []
    for fmt in formats:
        cells = []
        for cell in CELL_ORDER:
            batches = by_cell.get((fmt, cell), [])
            if not batches:
                cells.append(f'<div class="cell empty"><h4>{e(cell)}</h4>'
                             f'<p class="muted">no batches</p></div>')
                continue
            ok = all(b["passed"] for b in batches)
            items = []
            for batch in batches:
                observed = batch.get("observed") or {}
                names = "".join(
                    f"<li class='mono'>{e(n)} "
                    f"<span class='muted'>{v['rows']:,}&times;{v['columns']}</span></li>"
                    for n, v in sorted((observed.get("tables") or {}).items())
                )
                items.append(
                    f"<div class='batch'>{badge(batch['passed'])} "
                    f"<span class='batch-name'>{e(batch['case_id'].split('/')[-1])}</span>"
                    f"<span class='muted'> &middot; {len(batch['files'])} workbook(s)"
                    f" &rarr; {observed.get('table_count', 0)} table(s)</span>"
                    f"<ul class='names'>{names}</ul>"
                    f"{failure_detail(batch)}</div>"
                )
            cells.append(
                f'<div class="cell {"ok" if ok else "bad"}">'
                f'<h4>{e(cell)} {badge(ok)}</h4>{"".join(items)}</div>'
            )
        blocks.append(f'<h3>.{e(fmt)}</h3><div class="matrix">{"".join(cells)}</div>')
    return "".join(blocks)


def summary_section(every, summary):
    """The closing "what was checked" table - one row per property, not per case.

    The rest of the report answers "did it pass". This answers "what does passing
    mean", which on a green run is otherwise invisible: run_batch's checks are
    rendered only when they fail, so a clean run shows badges and no substance.

    The column that earns the section is `Checks`. A zero there is not a pass - it
    is a property nothing in the corpus exercised, the same shape of hole TEST_PLAN
    13.1 records for .xls extent scanning, where a real regression sailed through a
    green suite. Flagging it here means the next one is visible on the day rather
    than at the next mutation run.
    """
    rows = []
    by_section = {}
    for row in summary["rows"] + summary["unclassified"]:
        by_section.setdefault(row["section"], []).append(row)

    headings = list(loading_checks.INVARIANT_SECTIONS)
    if summary["unclassified"]:
        headings.append(("unclassified", "Unclassified - checks carrying no "
                                         "invariant id, listed so none goes missing"))

    for key, heading in headings:
        section_rows = by_section.get(key)
        if not section_rows:
            continue
        rows.append(f"<tr class='group'><td colspan='5'>{e(heading)}</td></tr>")
        for row in section_rows:
            if not row["exercised"] and row.get("error_only"):
                verdict = "<span class='chip'>none to report</span>"
            elif not row["exercised"]:
                verdict = "<span class='chip gap'>&#9888; NOT EXERCISED</span>"
            elif row["failed"]:
                verdict = f"{badge(False)} <span class='muted'>{row['failed']} failed</span>"
            else:
                verdict = badge(True)
            rows.append(
                "<tr>"
                f"<td class='num muted'>{e(row['number']) if row['number'] else '&mdash;'}</td>"
                f"<td>{e(row['label'])}<br>"
                f"<span class='mono muted'>{e(row['id'])}</span></td>"
                f"<td class='num'>{row['cases']:,}</td>"
                f"<td class='num'>{row['checks']:,}</td>"
                f"<td>{verdict}</td>"
                "</tr>"
            )

    parts = [
        "<h2>What was checked</h2>",
        '<p class="sub">Every check <code>run_batch</code> emitted, grouped by the '
        "property it verifies. Numbering follows TEST_PLAN &sect;6.1. Counts are of "
        "individual checks, not cases &mdash; invariant 3 fires twice per populated "
        "sheet, so it carries most of the run.</p>",
        "<div class='scroll'><table><thead><tr>"
        "<th class='num'>&sect;6.1</th><th>Property</th><th class='num'>Cases</th>"
        "<th class='num'>Checks</th><th>Result</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>",
    ]

    if summary["not_exercised"]:
        parts.append(
            '<div class="note"><strong>Not exercised.</strong> '
            + e(", ".join(summary["not_exercised"])) +
            " ran <strong>zero</strong> checks in this run. That is not a pass: no "
            "file in the corpus put the property in a position to fail, so it is "
            "currently guarding nothing. This is the failure mode TEST_PLAN "
            "&sect;13.1 documents, where a deliberate regression survived the entire "
            "suite because nothing in the corpus could detect it. Closing it means "
            "adding a file that exhibits the condition.</div>"
        )

    counts = {}
    for result in every:
        counts[result.get("baseline_status", "n/a")] = (
            counts.get(result.get("baseline_status", "n/a"), 0) + 1
        )
    shown = [f"<span class='chip {e(k)}'>{e(k)}</span> {v}"
             for k, v in sorted(counts.items()) if k != "n/a"]
    if shown:
        parts.append(
            '<p class="sub">Baseline coverage across single-file cases: '
            + " &middot; ".join(shown) +
            ". A <code>recorded</code> case is checked against its exact pinned "
            "numbers and fails on any drift; <code>unrecorded</code> is "
            "invariant-checked only.</p>"
        )

    return "".join(parts)


CSS = """
:root{color-scheme:light dark}
.viz-root{
  --surface-1:#fcfcfb; --plane:#f9f9f7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
  --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10);
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])) .viz-root{
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
  }
}
:root[data-theme="dark"] .viz-root{
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10);
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--text-primary);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.viz-root{max-width:1180px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:40px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--grid)}
h3{font-size:15px;margin:24px 0 8px;color:var(--text-secondary)}
h4{font-size:13px;margin:0 0 10px;display:flex;align-items:center;gap:8px}
.sub{color:var(--text-secondary);margin:0 0 24px}
.muted{color:var(--muted)}
.mono{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;font-size:12px}
.num{text-align:right;font-variant-numeric:tabular-nums}
code{font-family:ui-monospace,Consolas,monospace;font-size:12px;
  background:var(--plane);padding:1px 4px;border-radius:3px}

.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 8px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:8px;
  padding:12px 16px;min-width:120px}
.tile-label{font-size:12px;color:var(--text-secondary)}
.tile-value{font-size:26px;font-weight:600;margin-top:2px}
.tile.good .tile-value{color:var(--good)}
.tile.bad .tile-value{color:var(--critical)}

.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;
  letter-spacing:.03em;padding:2px 7px;border-radius:99px;white-space:nowrap;
  border:1px solid var(--border)}
.badge.pass{color:var(--good)}
.badge.fail{color:var(--critical)}
.chip{font-size:11px;padding:1px 6px;border-radius:99px;border:1px solid var(--border);
  color:var(--text-secondary)}
.chip.recorded{color:var(--good)}
.chip.unrecorded{color:var(--text-secondary)}
.chip.stale{color:var(--warning)}
/* An invariant nothing exercised. Deliberately not green and not red - it is an
   absence of evidence, and reading as either would be the lie this row exists to
   prevent. */
.chip.gap{color:var(--warning);font-weight:600}

table{width:100%;border-collapse:collapse;background:var(--surface-1);
  border:1px solid var(--border);border-radius:8px;overflow:hidden}
.scroll{overflow-x:auto}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--grid)}
th.num{text-align:right}
td{padding:7px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:none}
tr.detail td{background:var(--plane);padding-top:0}
tr.group td{background:var(--plane);font-size:11px;text-transform:uppercase;
  letter-spacing:.04em;color:var(--muted);font-weight:600}

/* Literally 2x2 - the grid IS the claim being demonstrated, so it should read as
   one, not reflow into three columns and hide the shape. */
.matrix{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start}
@media (max-width:720px){.matrix{grid-template-columns:1fr}}
.cell{background:var(--surface-1);border:1px solid var(--border);border-radius:8px;padding:14px}
.cell.bad{border-color:var(--critical)}
.batch{padding:8px 0;border-top:1px solid var(--grid)}
.batch:first-of-type{border-top:none}
.batch-name{font-weight:600;font-size:12px}
ul.names{margin:6px 0 0;padding-left:18px}
ul.names li{margin:1px 0}
ul.failures{margin:6px 0 0;padding-left:18px;color:var(--critical)}
ul.failures li{margin:3px 0}
.note{background:var(--surface-1);border:1px solid var(--border);border-left:3px solid var(--warning);
  border-radius:6px;padding:10px 14px;margin:12px 0}
"""


def render_html(results, versions, elapsed, ignored):
    every = [r for group in results.values() for r in group]
    passed = sum(1 for r in every if r["passed"])
    failed = len(every) - passed
    files = sum(len((r.get("observed") or {}).get("files", [])) for r in every)
    summary = loading_checks.summarise_checks(every)

    ext = results.get("extensions", [])
    singles = [r for r in ext if r["group"] == "extensions"]
    mixed = [r for r in ext if r["group"] == "extensions-mixed"]
    workbooks = results.get("workbooks", [])
    inbox = results.get("inbox", [])

    parts = [
        f'<title>File loading test report</title><style>{CSS}</style>',
        '<div class="viz-root">',
        "<h1>File loading test report</h1>",
        f'<p class="sub">{e(dt.datetime.now().strftime("%Y-%m-%d %H:%M"))} '
        f'&middot; corpus <code>{e(os.path.relpath(loading_checks.DATA_ROOT))}</code> '
        f'&middot; Python {e(versions["python"])}, pandas {e(versions["pandas"])}, '
        f'openpyxl {e(versions["openpyxl"])}, xlrd {e(versions["xlrd"])}</p>',
        '<div class="tiles">',
        tile("Cases", len(every)),
        tile("Passed", passed, "good" if passed else ""),
        tile("Failed", failed, "bad" if failed else ""),
        tile("Files checked", files),
        tile("Checks", f"{summary['total_checks']:,}"),
        tile("Duration", f"{elapsed:.1f}s"),
        "</div>",
    ]

    if ignored:
        parts.append(
            '<div class="note"><strong>Files ignored.</strong> These sat inside a '
            "tested folder but were not read &mdash; off-format files in a workbook "
            "batch, or unsupported extensions in the inbox. They are listed rather "
            "than dropped silently, because a file nobody tested is worse than one "
            "that failed.<ul>"
            + "".join(f"<li class='mono'>{e(k)}: {e(', '.join(v))}</li>"
                      for k, v in sorted(ignored.items()))
            + "</ul></div>"
        )

    if singles or mixed:
        parts.append("<h2>Test 1 &mdash; extension coverage</h2>")
    if singles:
        parts.append("<h3>Every file, individually</h3><div class='scroll'><table><thead><tr>"
                     "<th>Result</th><th>File</th><th class='num'>Bytes</th><th>Kind</th>"
                     "<th>Sheets (rows&times;cols)</th><th class='num'>Rows</th>"
                     "<th>Baseline</th><th class='num'>ms</th>"
                     "</tr></thead><tbody>" + file_rows(singles) + "</tbody></table></div>")
    if mixed:
        parts.append("<h3>Mixed-extension batches</h3><div class='scroll'><table><thead><tr>"
                     "<th>Result</th><th>Case</th><th>Files</th><th class='num'>Tables</th>"
                     "<th class='num'>Probe rows</th><th class='num'>Loaded rows</th>"
                     "<th class='num'>ms</th>"
                     "</tr></thead><tbody>" + batch_rows(mixed) + "</tbody></table></div>")

    if workbooks:
        parts.append(
            "<h2>Test 2 &mdash; workbook / sheet matrix</h2>"
            '<p class="sub">Each cell is a shape; each batch inside it is one upload. '
            "The table names below are generated by DataLoader &mdash; a multi-sheet "
            "workbook produces <code>Sheet (stem).xlsx</code>, a single-sheet one "
            "collapses to the bare filename.</p>"
            + matrix_section(workbooks)
        )

    parts.append("<h2>Test 3 &mdash; inbox</h2>")
    if inbox:
        parts.append("<div class='scroll'><table><thead><tr>"
                     "<th>Result</th><th>Case</th><th>Files</th><th class='num'>Tables</th>"
                     "<th class='num'>Probe rows</th><th class='num'>Loaded rows</th>"
                     "<th class='num'>ms</th>"
                     "</tr></thead><tbody>" + batch_rows(inbox) + "</tbody></table></div>")
    else:
        parts.append('<p class="sub">Empty. Drop a <code>.csv</code>, <code>.xls</code> '
                     "or <code>.xlsx</code> into <code>tests/data/inbox/</code> to have "
                     "it checked &mdash; no naming convention, no code change. Its "
                     "contents are gitignored, so empty is the expected state on a "
                     "fresh clone.</p>")

    parts.append(summary_section(every, summary))
    parts.append("</div>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true",
                        help="write observed numbers into the baseline")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if a required corpus is missing or empty")
    parser.add_argument("--only", choices=("extensions", "workbooks", "inbox"),
                        help="run one group")
    args = parser.parse_args()

    groups = collect(args.only)
    print(f"Corpus: {loading_checks.DATA_ROOT}")
    for group, cases in groups.items():
        print(f"  {group}: {len(cases)} case(s)")

    missing = [g for g in REQUIRED_GROUPS
               if g in groups and not groups[g]]
    if missing and args.strict:
        print(f"\nERROR (--strict): required corpus empty or missing: "
              f"{', '.join(missing)}", file=sys.stderr)
        return 2
    for group in missing:
        print(f"  WARNING: {group} is empty - nothing to test there")

    started = time.time()
    print()
    results = run(groups, record=args.record)
    elapsed = time.time() - started
    annotate_baselines(results)

    ignored = {
        r["case_id"]: r["ignored_files"]
        for group in results.values() for r in group if r["ignored_files"]
    }

    every = [r for group in results.values() for r in group]
    summary = loading_checks.summarise_checks(every)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(render_html(results, library_versions(), elapsed, ignored))
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "versions": library_versions(),
            "duration_s": round(elapsed, 2),
            "ignored_files": ignored,
            "summary": summary,
            "results": results,
        }, f, indent=2, default=str)

    failed = [r for r in every if not r["passed"]]
    print(f"\n{len(every) - len(failed)}/{len(every)} passed in {elapsed:.1f}s")
    print(f"  {summary['total_checks']:,} checks across "
          f"{len(loading_checks.INVARIANTS)} properties")
    if summary["not_exercised"]:
        print(f"  WARNING: no checks ran for: {', '.join(summary['not_exercised'])}"
              f" - see 'What was checked' in the report")
    print(f"  {os.path.relpath(HTML_PATH)}")
    print(f"  {os.path.relpath(JSON_PATH)}")
    if ignored:
        print(f"  {len(ignored)} case(s) had files ignored - see the report")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
