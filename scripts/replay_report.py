"""
Replay a past session's saved LLM recommendation through report_builder and
chart_builder directly - no LLM call, no web server.

Every session run through the app already has its LLM output saved at
session_data/<session_id>/cleaned_response.json. This script loads that
file, lets you pick which recommendation to build, and re-runs the exact
same pipeline (report_builder.generate_report -> chart_builder.build_chart_figure)
that the web app would run, opening the resulting chart in your browser.

Usage:
    python scripts/replay_report.py

Every choice - which session, which recommendation, and (if a referenced
spreadsheet isn't one of the bundled datasets/ samples) where that file
lives on disk - is made through GUI dialogs.
"""

import json
import re
import sys
from pathlib import Path
from tkinter import Tk, Listbox, Button, Label, SINGLE, END, filedialog, messagebox

import plotly.graph_objects as go

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.data.data_loader import DataLoader  # noqa: E402
from app.data.report_builder import generate_report, report_type_to_index, resolve_plotly_axes, _find_file_path  # noqa: E402
from app.data.chart_builder import build_chart_figure  # noqa: E402

# A worksheet from a multi-sheet workbook is named "<sheet> (<workbook stem>).<ext>"
# by DataLoader (see DataLoader._add_excel), so a referenced name that isn't a real
# file on disk still tells us which workbook to load. The greedy prefix makes the
# LAST parenthesised group the workbook stem, tolerating sheet names that themselves
# contain parentheses.
WORKSHEET_RE = re.compile(r"^(?P<sheet>.*) \((?P<stem>[^()]*)\)(?P<ext>\.[^.]+)$")

SESSION_DATA_DIR = REPO_ROOT / "session_data"
PREVIEW_WIDTH = 900
PREVIEW_HEIGHT = 550


def pick_session_dir(root: Tk) -> Path:
    initial = str(SESSION_DATA_DIR) if SESSION_DATA_DIR.exists() else str(REPO_ROOT)
    chosen = filedialog.askdirectory(
        parent=root,
        title="Select a session_data/<session_id> folder to replay",
        initialdir=initial,
    )
    if not chosen:
        raise SystemExit("No session folder selected.")
    return Path(chosen)


def pick_recommendation(root: Tk, recs: list) -> str:
    """Returns a report_type letter, or 'ALL' to run every recommendation."""
    result = {}

    dialog = _build_toplevel(root)
    dialog.title("Select recommendation to replay")

    Label(dialog, text="Pick a recommendation (double-click), or choose 'Run all':").pack(padx=10, pady=(10, 4))

    listbox = Listbox(dialog, width=80, height=min(10, len(recs) + 1), selectmode=SINGLE)
    labels = [f"{chr(ord('A') + i)}: {rec.get('report_name', 'Untitled')}" for i, rec in enumerate(recs)]
    for label in labels + ["Run all"]:
        listbox.insert(END, label)
    listbox.pack(padx=10, pady=4)
    listbox.selection_set(0)

    def on_choose():
        selection = listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        result["choice"] = "ALL" if idx == len(labels) else chr(ord("A") + idx)
        dialog.destroy()

    listbox.bind("<Double-Button-1>", lambda _e: on_choose())
    Button(dialog, text="Select", command=on_choose).pack(pady=(0, 10))

    dialog.wait_window()
    if "choice" not in result:
        raise SystemExit("No recommendation selected.")
    return result["choice"]


def _build_toplevel(root: Tk):
    from tkinter import Toplevel

    top = Toplevel(root)
    top.grab_set()
    return top


def collect_filenames(operations: list) -> list:
    names = []
    for op in operations:
        for name in op.get("files_involved", []) or []:
            if name not in names:
                names.append(name)
    return names


def resolve_source_files(root: Tk, filenames: list) -> list:
    """Turn the worksheet/file names a recommendation references into the list of
    real source files to load. A worksheet name collapses to its parent workbook,
    so a workbook is loaded once no matter how many of its sheets a report uses.
    Auto-resolves via datasets/ where possible; prompts for anything else."""
    source_paths = {}  # source basename -> path on disk (dedups, preserves order)
    for name in filenames:
        # A name that already resolves to a real file (a CSV, or a single-sheet
        # workbook) is loaded as-is.
        direct = _find_file_path(name, None)
        if direct:
            source_paths.setdefault(name, direct)
            continue

        # Otherwise treat it as a worksheet and load its parent workbook instead.
        match = WORKSHEET_RE.match(name)
        source_name = f"{match.group('stem')}{match.group('ext')}" if match else name
        if source_name in source_paths:
            continue

        found = _find_file_path(source_name, None)
        if not found:
            title = (
                f"Select the workbook '{source_name}' (needed for sheet '{name}')"
                if match else f"Select the source file for '{name}'"
            )
            found = filedialog.askopenfilename(parent=root, title=title, initialdir=str(REPO_ROOT))
        if found:
            source_paths[source_name] = found
        else:
            print(f"[replay_report] No file chosen for '{name}' - steps needing it may fail.")

    return list(source_paths.values())


def load_session_tables(source_files: list) -> dict:
    """Load every source file through DataLoader exactly as the live app does
    (app/api.py: loader.add_files -> loader.tables), so multi-sheet workbooks
    expand into the same worksheet-named tables the recommendation was written
    against. generate_report prefers these in-memory tables over file_paths."""
    loader = DataLoader()
    loader.add_files(source_files)
    tables = loader.tables()
    print(f"[replay_report] Loaded {len(tables)} table(s): {sorted(tables)}")
    return tables


def run_one(recommendations: dict, report_type: str, tables: dict, session_id: str, rec: dict):
    print(f"\n{'=' * 80}\nReplaying report {report_type}: {rec.get('report_name', 'Untitled')}\n{'=' * 80}")
    df = generate_report(recommendations, report_type=report_type, tables=tables, session_id=session_id)
    if df.empty:
        print(f"[replay_report] Report {report_type} produced no data - skipping chart.")
        return

    print(df.head(15).to_string())
    print(f"Shape: {df.shape}")

    plotly_config = resolve_plotly_axes(df, rec.get("plotly_config") or {}, rec.get("required_operations", []))
    chart_dict = build_chart_figure(df, plotly_config)
    if chart_dict is None:
        print(f"[replay_report] chart_builder returned no chart for report {report_type}.")
        return

    # The real app renders this chart inside a sized CSS container; the
    # standalone browser preview has no such container and otherwise fills
    # the whole viewport, so pin a reasonable preview size here.
    fig = go.Figure(chart_dict)
    fig.update_layout(width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT)
    fig.show()


def main():
    root = Tk()
    root.withdraw()

    session_dir = pick_session_dir(root)
    session_id = session_dir.name
    response_path = session_dir / "cleaned_response.json"
    if not response_path.exists():
        messagebox.showerror("replay_report", f"No cleaned_response.json found in {session_dir}")
        raise SystemExit(f"Missing {response_path}")

    recommendations = json.loads(response_path.read_text(encoding="utf-8"))
    recs = recommendations.get("recommendations", [])
    if not recs:
        messagebox.showerror("replay_report", "cleaned_response.json has no recommendations.")
        raise SystemExit("No recommendations in cleaned_response.json")

    choice = pick_recommendation(root, recs)
    targets = [chr(ord("A") + i) for i in range(len(recs))] if choice == "ALL" else [choice]

    for report_type in targets:
        idx = report_type_to_index(report_type)
        rec = recs[idx]
        filenames = collect_filenames(rec.get("required_operations", []))
        tables = load_session_tables(resolve_source_files(root, filenames))
        try:
            run_one(recommendations, report_type, tables, session_id, rec)
        except Exception as e:
            print(f"[replay_report] Report {report_type} failed: {e}")

    root.destroy()


if __name__ == "__main__":
    main()
