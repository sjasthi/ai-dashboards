"""Print what the usage database has recorded.

The counters and the raw event log, on the console, with no dependencies and no
server running. This exists because the alternatives are all worse: the sqlite3
CLI isn't installed on Windows by default, GET /api/stats only ever returns
aggregates and needs uvicorn up, and the full-log admin endpoint requires
ADMIN_TOKEN to be configured.

    python scripts/show_usage.py                    counters, breakdowns, last 20 events
    python scripts/show_usage.py --events 100       more of the log
    python scripts/show_usage.py --event analysis_completed
    python scripts/show_usage.py --files --reports  the per-file and per-report tables
    python scripts/show_usage.py --json             machine-readable, for piping
    python scripts/show_usage.py --db path/to/other.db

Reads only. Nothing here can modify or clear the database.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.data import telemetry  # noqa: E402


def rule(title):
    print(f"\n{title}")
    print("-" * max(len(title), 40))


def table(rows, columns):
    """Fixed-width table sized to its content. Values are truncated, never wrapped:
    one row per record is what makes a log skimmable."""
    if not rows:
        print("  (none)")
        return

    widths = {}
    for c in columns:
        longest = max((len(_cell(r.get(c))) for r in rows), default=0)
        widths[c] = min(max(longest, len(c)), 44)

    print("  " + "  ".join(c.ljust(widths[c]) for c in columns))
    print("  " + "  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  " + "  ".join(_cell(r.get(c))[: widths[c]].ljust(widths[c]) for c in columns))


def _cell(value):
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def breakdown(title, mapping):
    """A count per key, with a proportional bar. The bar is scaled to the largest
    value rather than the total, so a dominant category doesn't flatten the rest
    into invisibility."""
    rule(title)
    if not mapping:
        print("  (none)")
        return
    largest = max(mapping.values())
    width = max(len(k) for k in mapping)
    for key, count in mapping.items():
        bar = "#" * max(1, round((count / largest) * 24)) if largest else ""
        print(f"  {key.ljust(width)}  {str(count).rjust(5)}  {bar}")


def summary(stats):
    rule("Counters")
    for label, key in (
        ("Users", "users"),
        ("Sessions", "sessions"),
        ("Files processed", "files_processed"),
        ("Reports built", "reports_built"),
    ):
        print(f"  {label.ljust(18)} {str(stats[key]).rjust(8)}")

    breakdown("Files by extension", stats["ext_breakdown"])
    breakdown("Reports by pattern", stats["pattern_breakdown"])

    rule("Daily activity")
    if not stats["daily"]:
        print("  (none)")
    else:
        largest = max(d["events"] for d in stats["daily"])
        for day in stats["daily"]:
            bar = "#" * max(1, round((day["events"] / largest) * 24))
            print(f"  {day['date']}  {str(day['events']).rjust(5)}  {bar}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--events", type=int, default=20, metavar="N",
                        help="how many recent events to show (default 20, 0 to hide)")
    parser.add_argument("--event", metavar="NAME",
                        help="show only this event name, e.g. analysis_completed")
    parser.add_argument("--files", action="store_true",
                        help="also show the per-uploaded-file table")
    parser.add_argument("--reports", action="store_true",
                        help="also show the per-report table")
    parser.add_argument("--json", action="store_true",
                        help="emit everything as JSON instead of tables")
    parser.add_argument("--db", metavar="PATH",
                        help="read this database instead of $TELEMETRY_DB / usage.db")
    args = parser.parse_args()

    # Set before any telemetry call: the module reads TELEMETRY_DB per call, which
    # is what makes this override work at all.
    if args.db:
        os.environ["TELEMETRY_DB"] = args.db

    path = telemetry.db_path()
    exists = os.path.exists(path)

    stats = telemetry.stats() if exists else telemetry.empty_stats()
    events = telemetry.recent_events(args.events, event=args.event) if (exists and args.events) else []
    files = telemetry.recent_files() if (exists and args.files) else []
    reports = telemetry.recent_reports() if (exists and args.reports) else []

    if args.json:
        print(json.dumps({
            "db": path, "exists": exists, "stats": stats,
            "events": events, "files": files, "reports": reports,
        }, indent=2))
        return 0

    print(f"Usage database: {path}")
    if not exists:
        # Distinguish "nobody has used the app" from "you are pointed somewhere
        # else". Both show zeros, and only one of them is a problem.
        print("\nThis database does not exist yet - nothing has been recorded.")
        print("It is created on the first telemetry write, which happens the first")
        print("time an instrumented endpoint is called.")
        return 0

    print(f"Size: {os.path.getsize(path):,} bytes")
    summary(stats)

    if args.events:
        title = f"Recent events ({args.event})" if args.event else "Recent events"
        rule(f"{title} - newest first")
        table(events, ["ts", "event", "client_id", "session_id", "props"])

    if args.files:
        rule("Uploaded files - newest first")
        table(files, ["ts", "session_id", "ext", "kind", "rows", "columns",
                      "sheet_count", "sheets_selected", "load_ok", "name_hash"])

    if args.reports:
        rule("Reports - newest first")
        table(reports, ["ts", "session_id", "letter", "pattern", "chart_type",
                        "rows_returned", "build_ms", "ok"])

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
