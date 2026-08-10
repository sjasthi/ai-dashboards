"""
Render export_web.html / export_pdf.html straight from a saved session - no LLM
call, no browser, no uvicorn. This is the fast loop docs/EXPORT_FEATURE.md and
docs/EXPORT_LIVE_SYNC.md both point at for iterating on the export templates:
edit a .html template, rerun this script, reread the output. Seconds per cycle.

Requires a session saved to session_data/<id>/ (SAVE_REPORT_HISTORY=true on a
prior run). Every report the session's recommendations define is rebuilt and
included; pass --letters to render a subset.

Usage:
    python scripts/preview_export.py                        # newest session, all reports
    python scripts/preview_export.py 20260809_172303_279b25
    python scripts/preview_export.py 20260809_172303_279b25 --letters AB

Output lands in scripts/_export_previews/<session_id>/ (gitignored):
    combined.html, combined.pdf   - every rendered report, one comparative document
    <LETTER>.html, <LETTER>.pdf   - that report alone, single-report document

No chart images: chart PNGs are rasterised in the browser by export.js and
POSTed up (see docs/EXPORT_FEATURE.md - "Chart images arrive from the browser").
This script has no browser, so every chart prints the same "no chart could be
drawn" placeholder the real export shows when rasterisation fails. That's the
one part of the document this loop can't check; everything else - KPI tiles,
findings, distribution, comparison matrix, appendix - renders exactly as a real
export would.
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.api import rehydrate_session, _build_report  # noqa: E402
from app.data.export_builder import render_export_html, render_export_pdf  # noqa: E402

SESSION_DATA_DIR = REPO_ROOT / "session_data"
OUT_DIR = Path(__file__).parent / "_export_previews"


def newest_session_id() -> str:
    candidates = [
        p for p in SESSION_DATA_DIR.iterdir()
        if p.is_dir() and (p / "cleaned_response.json").exists()
    ]
    if not candidates:
        raise SystemExit(f"No session with cleaned_response.json found under {SESSION_DATA_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime).name


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_id", nargs="?", help="session_data/<id> to replay. Defaults to the most recently modified one.")
    parser.add_argument("--letters", default=None,
                         help="Report letters to render, e.g. AB. Defaults to every recommendation in the session.")
    args = parser.parse_args()

    session_id = args.session_id or newest_session_id()
    print(f"[preview_export] session: {session_id}")

    session = rehydrate_session(session_id)
    session["session_id"] = session_id
    session["reports"] = {}

    recs = (session.get("recommendations") or {}).get("recommendations", [])
    all_letters = [chr(ord("A") + i) for i in range(len(recs))]
    letters = list(args.letters.upper()) if args.letters else all_letters
    if not letters:
        raise SystemExit("Session has no recommendations to render.")

    for letter in letters:
        stored, diagnostics = _build_report(session, session_id, letter)
        session["reports"][letter] = stored
        print(f"[preview_export] {letter}: {stored.get('report_name')!r} "
              f"rows={stored.get('report_rows')} "
              f"stats_available={stored.get('stats', {}).get('available')} "
              f"diagnostics={diagnostics}")

    out_dir = OUT_DIR / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "combined.html").write_text(
        render_export_html(session, letters, chart_images=None), encoding="utf-8")
    (out_dir / "combined.pdf").write_bytes(
        render_export_pdf(session, letters, chart_images=None))
    print(f"[preview_export] wrote combined.html / combined.pdf ({len(letters)} report(s))")

    for letter in letters:
        (out_dir / f"{letter}.html").write_text(
            render_export_html(session, [letter], chart_images=None), encoding="utf-8")
        (out_dir / f"{letter}.pdf").write_bytes(
            render_export_pdf(session, [letter], chart_images=None))
    print(f"[preview_export] wrote single-report html/pdf for {', '.join(letters)}")
    print(f"[preview_export] output dir: {out_dir}")


if __name__ == "__main__":
    main()
