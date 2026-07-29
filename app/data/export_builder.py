"""
export_builder.py

Builds the three export documents (Summary Report, Full Analysis, Recommendations)
in HTML or PDF form, using the real data already sitting in a session:
  - session["report"]        (set by /api/generate-report: rows, columns, data,
                               chart, stats — see api.py)
  - session["recommendations"] (set by /api/analyze-full: the AI's report
                               recommendations, incl. rationale_bullets,
                               question_answered, data_quality_warning)

No new AI calls are made here - this only formats data that's already been
computed, so exporting is fast and can't fail due to AI/network issues.

Usage (from api.py):
    from app.data.export_builder import build_export

    html_or_pdf_bytes, mime_type, filename = build_export(session, export_type, format)
"""

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Tuple

from fpdf import FPDF

VALID_EXPORT_TYPES = {"summary", "full", "recommendations"}
VALID_FORMATS = {"pdf", "html"}


def build_export(session: Dict[str, Any], export_type: str, fmt: str) -> Tuple[bytes, str, str]:
    """
    Returns (content_bytes, mime_type, filename) for the requested export.

    Raises:
        ValueError: unknown export_type/format, or the session has no report yet.
    """
    if export_type not in VALID_EXPORT_TYPES:
        raise ValueError(f"Unknown export_type {export_type!r}, expected one of {VALID_EXPORT_TYPES}")
    if fmt not in VALID_FORMATS:
        raise ValueError(f"Unknown format {fmt!r}, expected one of {VALID_FORMATS}")

    report = session.get("report")
    if not report:
        raise ValueError("No report has been generated for this session yet")

    ctx = _build_context(session, report, export_type)
    html = _render_html(ctx, export_type)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{export_type}_{stamp}"

    if fmt == "html":
        return html.encode("utf-8"), "text/html", f"{base_name}.html"

    pdf_bytes = _html_context_to_pdf(ctx, export_type)
    return pdf_bytes, "application/pdf", f"{base_name}.pdf"


def _build_context(session: Dict[str, Any], report: Dict[str, Any], export_type: str) -> Dict[str, Any]:
    """Pulls together whatever each export type actually needs, from data
    that's already sitting in the session - nothing here is recomputed."""
    stats = report.get("stats") or {}
    recommendations = session.get("recommendations") or {}
    recs_list = recommendations.get("recommendations", [])

    rec_idx = None
    for i, r in enumerate(recs_list):
        if isinstance(r, dict) and r.get("report_name") == _selected_report_name(report, recs_list):
            rec_idx = i
            break
    selected_rec = recs_list[rec_idx] if rec_idx is not None else (
        recs_list[0] if recs_list and isinstance(recs_list[0], dict) else {}
    )

    return {
        "report_name": selected_rec.get("report_name") or "Report",
        "generated_at": report.get("generated_at") or datetime.now().isoformat(),
        "rows": report.get("rows", 0),
        "columns": report.get("columns", []),
        "data": report.get("data", []),
        "stats": stats,
        "question_answered": selected_rec.get("question_answered"),
        "rationale_bullets": selected_rec.get("rationale_bullets", []),
        "data_quality_warning": selected_rec.get("data_quality_warning"),
        "dataset_overview": recommendations.get("dataset_overview"),
    }


def _selected_report_name(report, recs_list):
    # report_type on its own ("A"/"B"/"C") isn't stored in `report`, only its
    # rows/columns/etc are - but every recommendation's report_name is unique
    # enough in practice that we don't need it. Kept as its own helper so this
    # matching logic has one place to improve later (e.g. if report_type gets
    # stored on the report dict too).
    return report.get("report_name")


def _render_html(ctx: Dict[str, Any], export_type: str) -> str:
    stats = ctx["stats"]

    def stat_row(label, value):
        if value is None:
            return ""
        return f"<tr><td class='label'>{label}</td><td>{value}</td></tr>"

    stats_table = ""
    if stats.get("available"):
        stats_table = f"""
        <table class="stats">
            {stat_row("Data points", stats.get("count"))}
            {stat_row("Mean", stats.get("mean"))}
            {stat_row("Median", stats.get("median"))}
            {stat_row("Min", stats.get("min"))}
            {stat_row("Max", stats.get("max"))}
            {stat_row("Sum", stats.get("sum"))}
            {stat_row("Peak", f"{stats.get('peak_value')} at {stats.get('peak_label')}" if stats.get("peak_label") else stats.get("peak_value"))}
        </table>
        """

    insight_html = f"""
    <div class="card">
        <div class="card-title insight">Top Insight</div>
        <p>{stats.get("top_insight_text") or ctx.get("question_answered") or "No insight available."}</p>
    </div>
    <div class="card">
        <div class="card-title anomaly">Anomaly Detected</div>
        <p>{stats.get("anomaly_text") or ctx.get("data_quality_warning") or "No anomalies flagged."}</p>
    </div>
    <div class="card">
        <div class="card-title recommendation">Recommendation</div>
        <p>{stats.get("recommendation_text") or (ctx["rationale_bullets"][0] if ctx["rationale_bullets"] else "No recommendation available.")}</p>
    </div>
    """

    if export_type == "summary":
        body = f"""
        <h2>{ctx['report_name']}</h2>
        <p class="meta">Generated {ctx['generated_at']} &middot; {ctx['rows']} rows &middot; {len(ctx['columns'])} columns</p>
        {stats_table}
        {insight_html}
        """

    elif export_type == "recommendations":
        bullets_html = "".join(f"<li>{b}</li>" for b in ctx["rationale_bullets"]) or "<li>No rationale provided.</li>"
        overview_html = f"<p>{ctx['dataset_overview']}</p>" if ctx.get("dataset_overview") else ""
        body = f"""
        <h2>Recommendations — {ctx['report_name']}</h2>
        {overview_html}
        <p><strong>Question answered:</strong> {ctx.get('question_answered') or '—'}</p>
        <ul>{bullets_html}</ul>
        {insight_html}
        """

    else:  # full
        col_headers = "".join(f"<th>{c}</th>" for c in ctx["columns"])
        row_html_parts = []
        for row in ctx["data"][:500]:  # cap so a huge report doesn't produce a multi-hundred-page PDF
            cells = "".join(f"<td>{row.get(c, '')}</td>" for c in ctx["columns"])
            row_html_parts.append(f"<tr>{cells}</tr>")
        rows_html = "".join(row_html_parts)
        truncated_note = (
            f"<p class='meta'>Showing first 500 of {ctx['rows']} rows.</p>"
            if ctx["rows"] > 500 else ""
        )
        body = f"""
        <h2>Full Analysis — {ctx['report_name']}</h2>
        <p class="meta">Generated {ctx['generated_at']} &middot; {ctx['rows']} rows &middot; {len(ctx['columns'])} columns</p>
        {stats_table}
        {insight_html}
        {truncated_note}
        <table class="data">
            <thead><tr>{col_headers}</tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{ctx['report_name']}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; color: #1e293b; margin: 32px; }}
  h2 {{ margin-bottom: 4px; }}
  .meta {{ color: #64748b; font-size: 13px; margin-top: 0; }}
  .card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 18px; margin: 14px 0; }}
  .card-title {{ font-weight: 700; font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 6px; }}
  .card-title.insight {{ color: #94a3b8; }}
  .card-title.anomaly {{ color: #d97706; }}
  .card-title.recommendation {{ color: #16a34a; }}
  table.stats {{ border-collapse: collapse; margin: 14px 0; }}
  table.stats td {{ padding: 4px 12px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
  table.stats td.label {{ color: #64748b; font-weight: 600; }}
  table.data {{ border-collapse: collapse; width: 100%; margin-top: 14px; font-size: 13px; }}
  table.data th, table.data td {{ border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }}
  table.data th {{ background: #f8fafc; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _html_context_to_pdf(ctx: Dict[str, Any], export_type: str) -> bytes:
    """Renders the same context directly with fpdf2 rather than converting HTML
    to PDF (no headless-browser dependency needed). Keeps formatting simple:
    headings, a stats table, the three insight cards as labeled paragraphs, and
    (for 'full') a data table capped to keep page count sane."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    title = ctx["report_name"] if export_type != "recommendations" else f"Recommendations - {ctx['report_name']}"
    pdf.multi_cell(0, 8, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 116, 139)
    pdf.multi_cell(0, 6, _pdf_safe(f"Generated {ctx['generated_at']}  |  {ctx['rows']} rows  |  {len(ctx['columns'])} columns"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    stats = ctx["stats"]
    if stats.get("available") and export_type in ("summary", "full"):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Key Statistics", ln=True)
        pdf.set_font("Helvetica", "", 10)
        rows = [
            ("Data points", stats.get("count")),
            ("Mean", stats.get("mean")),
            ("Median", stats.get("median")),
            ("Min", stats.get("min")),
            ("Max", stats.get("max")),
            ("Sum", stats.get("sum")),
        ]
        for label, value in rows:
            if value is None:
                continue
            pdf.cell(0, 6, _pdf_safe(f"{label}: {value}"), ln=True)
        pdf.ln(3)

    def insight_block(heading, text):
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _pdf_safe(heading), ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _pdf_safe(text or "Not available."), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    if export_type == "recommendations":
        if ctx.get("dataset_overview"):
            insight_block("Dataset Overview", ctx["dataset_overview"])
        insight_block("Question Answered", ctx.get("question_answered"))
        bullets = ctx["rationale_bullets"] or ["No rationale provided."]
        insight_block("Rationale", "\n".join(f"- {b}" for b in bullets))

    insight_block("Top Insight", stats.get("top_insight_text") or ctx.get("question_answered"))
    insight_block("Anomaly Detected", stats.get("anomaly_text") or ctx.get("data_quality_warning"))
    insight_block("Recommendation", stats.get("recommendation_text") or (ctx["rationale_bullets"][0] if ctx["rationale_bullets"] else None))

    if export_type == "full" and ctx["columns"] and ctx["data"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Data", ln=True)

        # Cap columns/rows so a wide or huge report stays printable rather than
        # producing overlapping text or a thousand-page PDF.
        columns = ctx["columns"][:8]
        col_width = min(190 / max(len(columns), 1), 45)
        row_limit = 200

        pdf.set_font("Helvetica", "B", 8)
        for c in columns:
            pdf.cell(col_width, 6, _pdf_safe(str(c))[:18], border=1)
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for row in ctx["data"][:row_limit]:
            for c in columns:
                pdf.cell(col_width, 6, _pdf_safe(str(row.get(c, "")))[:18], border=1)
            pdf.ln()

        if ctx["rows"] > row_limit or len(ctx["columns"]) > len(columns):
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(100, 116, 139)
            pdf.cell(0, 6, f"Showing {min(row_limit, ctx['rows'])} of {ctx['rows']} rows, "
                            f"{len(columns)} of {len(ctx['columns'])} columns.", ln=True)

    return bytes(pdf.output())


def _pdf_safe(text) -> str:
    """fpdf2's built-in Helvetica font is latin-1 only - strip anything outside
    that range (smart quotes, emoji, etc.) rather than letting the PDF build
    crash on a stray character from AI-generated text."""
    if text is None:
        return ""
    return str(text).encode("latin-1", errors="ignore").decode("latin-1")