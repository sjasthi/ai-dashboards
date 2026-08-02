"""Run the file-loading test plan and write a self-contained HTML report.

    python scripts/run_test_plan.py              # run and report
    python scripts/run_test_plan.py --record     # re-record expectations, then run
    python scripts/run_test_plan.py --open       # also open the report in a browser

Every case is executed by tests.loading_checks.run_case -- the same function
`pytest` uses -- so this cannot disagree with the test suite about what a case
asserts. It exists because a demo needs a readable artifact and a live narrative,
which pytest's dot-per-test output does not give.

Output: test_results/test_report.html, with no external assets, so it can be
opened from disk or emailed as one file.
"""

import argparse
import datetime
import html
import json
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import loading_checks  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "test_results")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "test_report.html")

STATUS_LABEL = {
    "passed": "PASS",
    "failed": "FAIL",
    "skipped": "SKIP",
    "xfailed": "KNOWN ISSUE",
    "xpassed": "FIXED?",
}

# Ordered so the professor's required matrix is read first and the rest follows
# in increasing obscurity. Groups absent from the manifest are simply skipped.
GROUP_ORDER = [
    ("required-matrix", "Required Excel matrix",
     "The four topologies asked for: one or two workbooks, one or many sheets. "
     "Each runs in both .xlsx and .xls, because the two formats are read by "
     "different libraries."),
    ("extensions", "Extension coverage",
     ".csv, .xls and .xlsx alone and mixed in a single upload."),
    ("sheet-selection", "Sheet selection",
     "Choosing which worksheets to analyse, including the case where narrowing "
     "to one sheet changes the table's name, and the malformed payloads that "
     "must be refused rather than silently treated as 'analyse everything'."),
    ("naming-hazards", "Naming hazards",
     "Sheet names that break naive naming: parentheses, duplicates across "
     "workbooks, Excel's 31-character limit, and non-ASCII."),
    ("structure-hazards", "Structure hazards",
     "Blank sheets, header-only sheets, padded and numeric headers, and rows "
     "that carry formatting but no data."),
    ("encodings", "CSV encodings",
     "The encoding fallback ladder, including one case it gets wrong."),
    ("failure-modes", "Failure modes",
     "Files that cannot be read. The requirement is graceful degradation: a "
     "clear message, never a 500 and never a silently wrong table."),
    ("scale", "Scale",
     "A large workbook, built on demand rather than committed."),
]

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e3e3e3;--card:#f7f7f8;
--pass:#1a7f37;--fail:#b42318;--skip:#7a7a7a;--known:#8a5a00;--accent:#0b5cad}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e8e8e8;--muted:#a0a0a0;
--line:#2c3036;--card:#1c1f23;--pass:#4ac26b;--fail:#ff7b72;--skip:#9aa0a6;
--known:#e3b341;--accent:#6cb6ff}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem 4rem;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 .25rem}
.sub{color:var(--muted);margin:0 0 1.5rem}
.tiles{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:1.5rem}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:.7rem 1rem;min-width:104px}
.tile .n{font-size:1.5rem;font-weight:650;line-height:1.1}
.tile .l{color:var(--muted);font-size:.75rem;text-transform:uppercase;
letter-spacing:.04em}
.env{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:.75rem 1rem;margin-bottom:2rem;font-size:.85rem;color:var(--muted)}
.env code{color:var(--fg)}
h2{font-size:1.1rem;margin:2rem 0 .2rem;padding-top:.6rem;border-top:1px solid var(--line)}
h2 .count{color:var(--muted);font-weight:400;font-size:.85rem}
.blurb{color:var(--muted);margin:.2rem 0 .8rem;font-size:.9rem}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem}
th,td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--line);
vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
td.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
white-space:nowrap}
.st{font-weight:650;white-space:nowrap;font-size:.78rem}
.st.passed{color:var(--pass)}.st.failed{color:var(--fail)}
.st.skipped{color:var(--skip)}.st.xfailed{color:var(--known)}
.st.xpassed{color:var(--accent)}
.files{color:var(--muted);font-size:.8rem;font-family:ui-monospace,monospace}
.shape{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;
white-space:pre-wrap;margin:0}
.note{margin:.35rem 0 0;padding:.4rem .6rem;border-left:3px solid var(--known);
background:var(--card);font-size:.8rem;color:var(--muted);border-radius:0 4px 4px 0}
.note.bad{border-left-color:var(--fail)}
.dur{color:var(--muted);font-size:.78rem;text-align:right;white-space:nowrap}
footer{margin-top:3rem;color:var(--muted);font-size:.8rem}
"""


def shape_lines(shapes):
    if not shapes:
        return ""
    return "\n".join(
        f"{name}  {v['rows']}x{v['columns']}" for name, v in shapes.items()
    )


def render_html(results, versions, recorded=None):
    counts = loading_checks.summarize(results)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    by_group = {}
    for r in results:
        by_group.setdefault(r["group"], []).append(r)

    def tile(n, label):
        return f'<div class="tile"><div class="n">{n}</div><div class="l">{label}</div></div>'

    tiles = [tile(counts["total"], "cases"), tile(counts.get("passed", 0), "passed")]
    if counts.get("failed"):
        tiles.append(tile(counts["failed"], "failed"))
    if counts.get("xfailed"):
        tiles.append(tile(counts["xfailed"], "known issues"))
    if counts.get("xpassed"):
        tiles.append(tile(counts["xpassed"], "newly fixed"))
    if counts.get("skipped"):
        tiles.append(tile(counts["skipped"], "skipped"))
    tiles.append(tile(f'{counts["duration_ms"] / 1000:.1f}s', "duration"))

    env = " &middot; ".join(
        f"{k} <code>{html.escape(str(v))}</code>" for k, v in versions.items()
    )

    sections = []
    for group, title, blurb in GROUP_ORDER:
        rows = by_group.get(group)
        if not rows:
            continue
        n_pass = sum(1 for r in rows if r["status"] == "passed")
        body = []
        for r in rows:
            note = ""
            if r["problems"]:
                cls = "note" if r["status"] == "xfailed" else "note bad"
                items = "".join(f"<div>&bull; {html.escape(p)}</div>"
                                for p in r["problems"])
                note += f'<div class="{cls}">{items}</div>'
            if r["status"] == "xfailed" and r.get("known_issue"):
                note += (f'<div class="note">Known issue: '
                         f'{html.escape(r["known_issue"])}</div>')
            if r["status"] == "skipped" and r.get("reason"):
                note += f'<div class="note">{html.escape(r["reason"])}</div>'
            if r["status"] == "xpassed":
                note += ('<div class="note">This case now passes. The defect looks '
                         'fixed - remove its xfail flag in the manifest.</div>')

            body.append(
                "<tr>"
                f'<td class="id">{html.escape(r["id"])}</td>'
                f'<td><div>{html.escape(r["description"])}</div>'
                f'<div class="files">{html.escape(", ".join(r["files"]))}</div>{note}</td>'
                f'<td><pre class="shape">{html.escape(shape_lines(r.get("expected_tables")))}</pre></td>'
                f'<td><pre class="shape">{html.escape(shape_lines(r.get("actual_tables")))}</pre></td>'
                f'<td class="st {r["status"]}">{STATUS_LABEL[r["status"]]}</td>'
                f'<td class="dur">{r["duration_ms"]:.0f} ms</td>'
                "</tr>"
            )

        sections.append(
            f'<h2>{html.escape(title)} '
            f'<span class="count">&mdash; {n_pass}/{len(rows)} passed</span></h2>'
            f'<p class="blurb">{html.escape(blurb)}</p>'
            '<div class="scroll"><table><thead><tr>'
            "<th>Case</th><th>What it checks</th><th>Expected</th><th>Actual</th>"
            "<th>Result</th><th>Time</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div>"
        )

    banner = ""
    if recorded:
        banner = (
            '<div class="note">Expectations were re-recorded this run: '
            f'{html.escape(", ".join(recorded))}. Review the manifest diff before '
            "committing.</div>"
        )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>File Loading Test Report</title>"
        f"<style>{CSS}</style></head><body><div class=\"wrap\">"
        "<h1>File Loading Test Report</h1>"
        f'<p class="sub">Generated {now} &middot; '
        f"{counts['total']} golden cases over "
        f"{len({f for r in results for f in r['files']})} fixture files</p>"
        f'{banner}<div class="tiles">{"".join(tiles)}</div>'
        f'<div class="env">{env}</div>'
        + "".join(sections)
        + "<footer>Each case is executed by tests/loading_checks.py, the same code "
          "<code>pytest</code> runs, so this report and the test suite cannot "
          "disagree about what a case asserts. Expected values are hand-checked "
          "against tests/fixtures/manifest.json.</footer>"
        "</div></body></html>"
    )


def print_console(results):
    width = max(len(r["id"]) for r in results) + 2
    current = None
    for r in results:
        if r["group"] != current:
            current = r["group"]
            print(f"\n{current}")
        label = STATUS_LABEL[r["status"]]
        line = f"  {label:<12} {r['id']:<{width}} {r['duration_ms']:>6.0f} ms"
        print(line)
        for p in r["problems"]:
            print(f"      - {p}")

    counts = loading_checks.summarize(results)
    print("\n" + "-" * 60)
    parts = [f"{counts.get(k, 0)} {k}" for k in
             ("passed", "failed", "skipped", "xfailed", "xpassed") if counts.get(k)]
    print(f"  {counts['total']} cases: " + ", ".join(parts)
          + f"  ({counts['duration_ms'] / 1000:.1f}s)")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", action="store_true",
                        help="rewrite recordable expectations from observed output, "
                             "then run. Never touches xfail cases.")
    parser.add_argument("--open", action="store_true", dest="open_report",
                        help="open the HTML report when finished")
    parser.add_argument("--group", action="append",
                        help="only run cases in this group (repeatable)")
    args = parser.parse_args()

    recorded = None
    if args.record:
        recorded, skipped = loading_checks.record_manifest()
        print(f"recorded {len(recorded)} case(s); left {len(skipped)} alone")
        for case_id in recorded:
            print(f"  updated  {case_id}")
        if not recorded:
            print("  (nothing changed - the manifest already matches observed output)")

    cases = loading_checks.load_cases()
    if args.group:
        wanted = set(args.group)
        cases = [c for c in cases if c.get("group") in wanted]
        if not cases:
            parser.error(f"no cases in group(s): {', '.join(sorted(wanted))}")

    print(f"running {len(cases)} golden cases...")
    results = loading_checks.run_all(cases)
    counts = print_console(results)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(render_html(results, loading_checks.library_versions(), recorded))
    print(f"\n  report: {OUTPUT_PATH}")

    if args.open_report:
        webbrowser.open(f"file:///{OUTPUT_PATH.replace(os.sep, '/')}")

    # An unexpected pass is a failure too: it means a case flagged as a known
    # defect now works, and the flag is lying about the state of the code.
    return 1 if (counts.get("failed") or counts.get("xpassed")) else 0


if __name__ == "__main__":
    sys.exit(main())
