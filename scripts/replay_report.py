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
import sys
from pathlib import Path
from tkinter import Tk, Listbox, Button, Label, SINGLE, END, filedialog, messagebox

import plotly.graph_objects as go

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.data.report_builder import generate_report, report_type_to_index, _find_file_path  # noqa: E402
from app.data.chart_builder import build_chart_figure  # noqa: E402

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


def resolve_file_paths(root: Tk, filenames: list) -> dict:
    """Auto-resolve via datasets/ where possible; prompt for anything else."""
    file_paths = {}
    for name in filenames:
        found = _find_file_path(name, None)
        if found:
            file_paths[name] = found
            continue
        chosen = filedialog.askopenfilename(
            parent=root,
            title=f"Select the source file for '{name}'",
            initialdir=str(REPO_ROOT),
        )
        if chosen:
            file_paths[name] = chosen
        else:
            print(f"[replay_report] No file chosen for '{name}' - steps needing it may fail.")
    return file_paths


def run_one(recommendations: dict, report_type: str, file_paths: dict, session_id: str, rec: dict):
    print(f"\n{'=' * 80}\nReplaying report {report_type}: {rec.get('report_name', 'Untitled')}\n{'=' * 80}")
    df = generate_report(recommendations, report_type=report_type, file_paths=file_paths, session_id=session_id)
    if df.empty:
        print(f"[replay_report] Report {report_type} produced no data - skipping chart.")
        return

    print(df.head(15).to_string())
    print(f"Shape: {df.shape}")

    plotly_config = rec.get("plotly_config") or {}
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
        file_paths = resolve_file_paths(root, filenames)
        try:
            run_one(recommendations, report_type, file_paths, session_id, rec)
        except Exception as e:
            print(f"[replay_report] Report {report_type} failed: {e}")

    root.destroy()


if __name__ == "__main__":
    main()
