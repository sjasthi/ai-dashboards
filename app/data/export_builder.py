"""
export_builder.py — renders generated reports into a downloadable PDF or a
standalone HTML file.

Selecting one report produces a single-report document; selecting two or more
produces one combined comparative document, because the reason to export several
at once is to compare them.

docs/EXPORT_FEATURE.md is the orientation doc for this feature - it records the
renderer's constraints and the document design, and is worth reading before changing
anything here.

Two decisions shape everything here:

1. Chart images arrive from the browser. chart_builder.py returns an unstyled
   Plotly dict - all the theming (fonts, the median reference line, the min/max
   labels, the height a sideways bar chart needs) lives in the frontend's
   chartLayout.js. Rendering the PNG server-side would therefore produce a chart
   that doesn't match the one the user approved on screen, and would need Kaleido,
   which since v1 wants a Chromium binary fetched at runtime. So export.js renders
   the PNG in the browser with the real layout and posts it up. Nothing in this
   module imports plotly.

2. The numbers are formatted by ports of the frontend's format.js. An export whose
   figures are rounded differently from the screen is a different document, and the
   discrepancy is the kind a reader notices and can't explain.
"""

import base64
import binascii
import datetime as _dt
import io
import math
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image
from xhtml2pdf import pisa

# Appendix caps. The point of the appendix is that a reader can check the chart
# against the numbers, not that the export reproduces a 50k-row dataset - and
# xhtml2pdf clips wide tables rather than shrinking them, so the column cap is a
# layout requirement rather than a preference.
MAX_APPENDIX_ROWS = 200
MAX_APPENDIX_COLS = 8

# A chart PNG at 920x520 scale 2 lands around 350 KB. Anything past 4 MB is not a
# chart this app produced.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

_DATA_URL_PREFIX = "data:image/png;base64,"

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


class ExportRenderError(RuntimeError):
    """The document could not be rendered."""


# ============================================================================
# FORMATTING — ports of app/web/src/format.js
# ============================================================================

def _is_missing(value: Any) -> bool:
    """None, NaN, NaT — anything that should print as an em dash."""
    if value is None:
        return True
    try:
        if isinstance(value, float) and math.isnan(value):
            return True
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        # pd.isna on an array-like raises rather than returning a scalar.
        return False


def _trim_zero(n: float) -> str:
    s = f"{n:.1f}"
    return s[:-2] if s.endswith(".0") else s


def fmt_compact(value: Any) -> str:
    """12148 -> "12,148"; 4200000 -> "4.2M". Mirrors format.js compactNumber."""
    if _is_missing(value):
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(n):
        return "—"

    a = abs(n)
    if a >= 1_000_000_000:
        return _trim_zero(n / 1_000_000_000) + "B"
    if a >= 1_000_000:
        return _trim_zero(n / 1_000_000) + "M"
    if a >= 100_000:
        return _trim_zero(n / 1_000) + "K"
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


def fmt_exact(value: Any) -> str:
    """Full precision with separators, for table cells. Mirrors exactNumber."""
    if _is_missing(value):
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(n):
        return str(value)
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.4f}".rstrip("0").rstrip(".")


def fmt_signed_pct(value: Any) -> str:
    """"+3.2%" / "−3.2%" — a real minus sign, as on screen."""
    if _is_missing(value):
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "+" if n > 0 else ("−" if n < 0 else "")
    return f"{sign}{abs(n):.1f}%"


def fmt_measure(value: Any, is_currency: Any = False) -> str:
    """"$4.2M" when `is_currency`, "4.2M" otherwise. Mirrors format.js compactMeasure.

    Unlike compactMeasure this returns the placeholder dash directly rather than
    passing a missing value through unformatted: the JS version leans on that to let
    StatTile's own null check apply its muted "empty" styling, but every Jinja call
    site here just wants a string to print.
    """
    formatted = fmt_compact(value)
    return f"${formatted}" if is_currency and formatted != "—" else formatted


_DIRECTION_GLYPHS = {"up": "▲", "down": "▼"}


def fmt_direction_glyph(direction: Any) -> str:
    """▲ / ▼ / — matched to a trend direction. Mirrors format.js directionGlyph."""
    return _DIRECTION_GLYPHS.get(direction, "—")


def delta_vs_average(value: Any, mean: Any) -> Optional[Dict[str, str]]:
    """How far a single value sits from the series mean, as a compact inline badge.

    Mirrors format.js deltaVsAverage. Returns None when there's no mean to compare
    against, or the value doesn't differ from it at whole-percent resolution -
    "+0% vs avg" beside an up/down arrow would claim a direction that isn't there.
    """
    try:
        v = float(value)
        m = float(mean)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or not math.isfinite(m) or m == 0:
        return None
    pct = round(((v - m) / abs(m)) * 100)
    if pct == 0:
        return None
    sign = "+" if pct > 0 else "−"
    return {"direction": "up" if pct > 0 else "down", "text": f"{sign}{abs(pct)}% vs avg"}


def fmt_clock(iso: Any) -> str:
    """An ISO timestamp as a readable local date and time.

    The screen shows time only, because a dashboard is read while it is being
    generated. An exported file gets forwarded and opened weeks later, so it needs
    the date too.
    """
    if not iso:
        return ""
    if isinstance(iso, (_dt.datetime, _dt.date)):
        dt = iso
    else:
        try:
            dt = _dt.datetime.fromisoformat(str(iso))
        except (TypeError, ValueError):
            return str(iso)
    if isinstance(dt, _dt.datetime):
        return dt.strftime("%d %b %Y at %H:%M")
    return dt.strftime("%d %b %Y")


_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):\d{2}")


def fmt_cell(value: Any) -> str:
    """Render an appendix cell.

    Wider than format.js cellValue because it reads raw pandas rather than JSON:
    the rows held server-side come straight from DataFrame.to_dict, so Timestamps,
    numpy scalars and NaN arrive as objects. Without this the appendix would print
    "2023-01-01 00:00:00" in every date cell and "nan" wherever a value is missing.
    """
    if _is_missing(value):
        return "—"

    # numpy scalars -> their Python equivalent
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes)):
        try:
            value = item()
        except (TypeError, ValueError):
            pass

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return fmt_exact(value)

    if isinstance(value, (_dt.datetime, pd.Timestamp)):
        # Midnight means the underlying value is a date, not a moment.
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")

    s = str(value)
    m = _ISO_RE.match(s)
    if m:
        date, hh, mm = m.group(1), m.group(2), m.group(3)
        return date if (hh == "00" and mm == "00") else f"{date} {hh}:{mm}"
    return s


_AGG_WORDS = {
    "mean": "avg", "sum": "total", "count": "count", "min": "min", "max": "max",
    "median": "median", "std": "std dev", "nunique": "unique count",
}


def fmt_human(name: Any) -> str:
    """"order_id_count" -> "Order Id (count)". Mirrors format.js humanizeColumn."""
    if not name:
        return ""
    name = str(name)
    base, qualifier = name, None
    for suffix, word in _AGG_WORDS.items():
        if name.endswith(f"_{suffix}"):
            base = name[: -(len(suffix) + 1)]
            qualifier = word
            break
    label = " ".join(w.capitalize() if w.islower() else w for w in base.replace("_", " ").split())
    return f"{label} ({qualifier})" if qualifier else label


# ============================================================================
# JINJA
# ============================================================================

# Absolute loader path so the templates resolve regardless of uvicorn's cwd.
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["compact"] = fmt_compact
_env.filters["exact"] = fmt_exact
_env.filters["signed_pct"] = fmt_signed_pct
_env.filters["clock"] = fmt_clock
_env.filters["cell"] = fmt_cell
_env.filters["human"] = fmt_human
_env.filters["measure"] = fmt_measure
_env.filters["direction_glyph"] = fmt_direction_glyph
_env.globals["delta_vs_average"] = delta_vs_average

# The nav bar's brand mark (App.jsx's `.app-nav__brand`: a 34px #2563eb rounded
# square holding a white three-bar icon, next to "AI-Dashboard" in bold) baked to a
# PNG data URI. xhtml2pdf has no inline-SVG support (confirmed by rendering, not
# assumed - see docs/EXPORT_LIVE_SYNC.md), so unlike the rest of this module's icons
# this one can't be markup-ported live; it's a fixed raster instead, generated once
# to match the live component's exact geometry.
_BRAND_ICON_DATA_URL = "data:image/png;base64," + (
    "iVBORw0KGgoAAAANSUhEUgAAARAAAAEQCAYAAAB4CisVAAAEgklEQVR42u3diXXiQBBAwZ55hGBCgMQgLJOYCQFywBHYSOiYqyoA"
    "HtYun+6Rd5ViAKfL4xWws/vtmHr/GZNQgLAMGRDBQFAERDSg0Zgk0QAx6TIgwgF1hySJBohJ8wERDmgvJFk8INyBbHECEQ5oexpJ"
    "wgFC0swKIx7Qz2criweISNUrjHBAnytNFg8wjVQZEPGAviOSxQNEpKqAiAeMEZEkHBAOV0tPIOIB400j2eUEigbE9AFjTiFZPEBE"
    "igREPGDsiGTxABHZNSDiASLiLgwQu64wpg8whXwUEPEAEfkoIOIBIuIMBIhdz0BMH2AKMYEA+04gpg8whXwUEPEAEbHCAPuuMKYP"
    "4F0LTCCA/5EMqCQg1hdgShNMIMB6E4jpA5g6hZhAAIeoQOGAWF+AOWuMCQSwwgAFA2J9AeauMSYQwAoDCAjQWkCcfwDxwTmICQSw"
    "wgACAggIICAA/0juwAAmEEBAAAEBBARAQAABAQQEEBBAQAAEBBAQQEAAAQEQEEBAAAEBBAQQEAABAQQEEBBAQAAEBBAQoA4Hl4AW"
    "/Hx/LX6N8/XpQppAEI+yr4OAMFg8RERAEA8RERAo/yEXEQEBBAQQEEBAAAQEEBBAQAABARAQQEAAAQEEBBAQAAEBBAQQEEBAAAQE"
    "EBAgPBeG8LwVTCB43goCgniICAKC560gIHjeCgICCAiAgAACAggIICCAgAAICCAggIAAAgIIiEsACAggIICAAAICICCAgAACAggI"
    "ICAAAgIICCAggIAA/OHgEoSn3oMJxFPvQUDEQ0QQEPEQEQQET70HAQEEBBAQQEAAAQEQEEBAAAEBBARAQAABAQQEEBBAQAAEBBAQ"
    "QEAAAQEQEEBAAAEBBAQQEAABAQQEEBBAQAAEBBAQQEAAAQEEBEBAAAEBBAQQEAABAQQEEBBAQAABARAQQEAAAQEEBEBAAAGpx/n6"
    "bOL1vc99Xl9AqOYv5dqv632Kh4AMEhEf9jbep4BgjPc+mSmdLo+XywCYQAABAQQEEBAAAQEEBBAQQEAAAQEQEEBAAAEBBARAQAAB"
    "AQQEEBBAQAAEBBAQQEAAAQEQEGC9gNxvx+QyAHPdb8dkAgGsMICAAAICCAjAu4C4EwPEzDswJhDACgMICNBqQJyDADHj/MMEAlhh"
    "gAoCYo0Bpq4vJhDACgNUEhBrDDBlfTGBAOuvMKYQYEoTTCCAQ1SgooBYY4B3LTCBANusMKYQMH0sOgMRERAPKwxQ5i6MKQRMHyYQ"
    "oMzvgZhCwPSxaAIRERCPRSuMiIB4OAMBosi/hTGFgOlj0QQiIjB2PBavMCIC48ZjlTMQEYEx47HaIaqIwHjxcBcGqCMgphAYa/qI"
    "iNjkQ3+6PF7+mKDfcGy6wphGoP94bHoGIiLQdzw2P0QVEeg3HrvchRER6DMeERsdoobDVeg6HEV+D8Q0An19xnLvPyCIRycrjJUG"
    "+vpSrmIaEBJoc5rPLgSIR9MTiGkE2vyyrfqbX0yg7gm9idVBSBAOARET6Ow8sOnDSzFBNAREUBAMAREWGO3XE34B2hx/HQ/pyA4A"
    "AAAASUVORK5CYII="
)
_env.globals["brand_icon_data_url"] = _BRAND_ICON_DATA_URL


# ============================================================================
# FILENAMES
# ============================================================================

def _slugify(text: Any, limit: int = 48) -> str:
    """ASCII-only slug, so Content-Disposition never needs RFC 5987 encoding."""
    if not text:
        return ""
    ascii_text = (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:limit].strip("-")


def _session_id(session: Dict[str, Any], letters: Sequence[str] = ()) -> str:
    """The session's own id.

    api.py keys SESSIONS by id without storing it inside the value, so the reliable
    copy is the one each stored report payload carries.
    """
    if session.get("session_id"):
        return str(session["session_id"])
    reports = session.get("reports") or {}
    for letter in list(letters) + sorted(reports):
        stored = reports.get(letter) or {}
        if stored.get("session_id"):
            return str(stored["session_id"])
    return ""


def export_filename(session: Dict[str, Any], report_types: Sequence[str], fmt: str) -> str:
    """A descriptive, sortable filename.

    The timestamp is the session id, which is already %Y%m%d_%H%M%S - so every file
    from one session groups together and traces back to session_data/<id>/.
    """
    letters = [t.upper() for t in report_types]
    # Not _slugify: that would turn the id's underscore into a hyphen, and the whole
    # point of using the session id verbatim is that it matches session_data/<id>/.
    stamp = re.sub(r"[^A-Za-z0-9_]", "", _session_id(session, letters))[:32]
    parts = ["ai-dashboard"]

    if len(letters) == 1:
        reports = (session.get("reports") or {})
        name = _slugify((reports.get(letters[0]) or {}).get("report_name"))
        parts += ["report", letters[0].lower()]
        if name:
            parts.append(name)
    else:
        parts += ["reports", "".join(letters).lower(), "combined"]

    if stamp:
        parts.append(stamp)
    return "-".join(p for p in parts if p) + ("." + fmt)


# ============================================================================
# CHART IMAGES
# ============================================================================

def _sanitize_data_url(value: Optional[str]) -> Optional[str]:
    """Validate a browser-supplied PNG data URL, or return None.

    Never raises. A truncated or non-image payload reaching reportlab surfaces as
    an uncaught OSError ("broken data stream when reading image file") from inside
    the PDF writer, which would turn one bad chart into a failed export. Dropping
    the image instead costs the reader a chart and a printed explanation; the
    statistics, findings and data table all still render.
    """
    if not value or not isinstance(value, str):
        return None

    value = value.strip()
    if not value.startswith(_DATA_URL_PREFIX):
        print("[export] Ignoring chart image: not a base64 PNG data URL")
        return None

    payload = value[len(_DATA_URL_PREFIX):]
    if len(payload) > MAX_IMAGE_BYTES * 4 // 3 + 8:
        print("[export] Ignoring chart image: payload exceeds the size cap")
        return None

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as e:
        print(f"[export] Ignoring chart image: undecodable base64 ({e})")
        return None

    if not raw or len(raw) > MAX_IMAGE_BYTES:
        print("[export] Ignoring chart image: empty or over the size cap")
        return None

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
            if img.format != "PNG":
                print(f"[export] Ignoring chart image: format is {img.format}, not PNG")
                return None
    except Exception as e:  # Pillow raises a wide range on malformed input
        print(f"[export] Ignoring chart image: not a readable PNG ({type(e).__name__}: {e})")
        return None

    return _DATA_URL_PREFIX + payload


# ============================================================================
# CONTEXT
# ============================================================================

def _appendix(report: Dict[str, Any], row_limit: int) -> Optional[Dict[str, Any]]:
    """The report's own rows, capped for print."""
    if row_limit <= 0:
        return None

    columns = list(report.get("data_columns") or [])
    if not columns:
        return None

    shown = columns[:MAX_APPENDIX_COLS]
    rows = (report.get("data") or [])[:row_limit]

    return {
        "columns": shown,
        "rows": rows,
        "omitted_columns": max(0, len(columns) - len(shown)),
    }


def _report_context(
    session: Dict[str, Any],
    letter: str,
    *,
    chart_images: Optional[Dict[str, str]],
    dist_images: Optional[Dict[str, str]],
    include_appendix: bool,
    appendix_row_limit: int,
) -> Dict[str, Any]:
    """One report, flattened for the templates."""
    report = ((session.get("reports") or {}).get(letter)) or {}
    image = _sanitize_data_url((chart_images or {}).get(letter))
    # Same idea as chart_image, same sanitizer, but for the Distribution card's
    # Center-cell skew curve: xhtml2pdf cannot draw the inline SVG SkewGlyph.jsx
    # uses, so the browser rasterises it (export.js's skewGlyphPngDataUrl) and
    # POSTs it up like the chart. Only ever consumed by the PDF shell - the HTML
    # export draws the real SVG itself, see distribution_card() in _macros.html.
    dist_image = _sanitize_data_url((dist_images or {}).get(letter))

    return {
        "letter": letter,
        "report_name": report.get("report_name") or f"Report {letter}",
        "pattern_used": report.get("pattern_used"),
        "question_answered": report.get("question_answered"),
        "rationale_bullets": report.get("rationale_bullets") or [],
        "chart_type": report.get("chart_type"),
        "chart_image": image,
        "dist_image": dist_image,
        "report_rows": report.get("report_rows"),
        "data_columns": report.get("data_columns") or [],
        "stats": report.get("stats") or {},
        "schema_warning": report.get("schema_warning"),
        "generated_at": report.get("generated_at"),
        "source_files": report.get("source_files") or [],
        "operations": report.get("operations") or [],
        "appendix": _appendix(report, appendix_row_limit) if include_appendix else None,
    }


# Rows of the comparison matrix. `absent` text is what prints when the statistic
# was never measured for that report - never a blank, never a zero.
_ABSENT_TREND = "not measured — no ordered axis"
_ABSENT_CONC = "not measured — not a ranking"


def _cell(text: Any, absent: bool = False) -> Dict[str, Any]:
    return {"text": text if (text not in (None, "")) else "—", "absent": absent}


def _comparison_rows(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The comparison matrix, one row per metric.

    Every builder below has to answer "was this measured?" separately from "what was
    the value?". A report with no ordered axis has no trend - not a trend of zero -
    and a matrix that renders the two the same way invites a false comparison.
    """
    def row(label, fn, emphasis=False):
        return {
            "label": label,
            "emphasis": emphasis,
            "cells": [fn(r, r.get("stats") or {}) for r in reports],
        }

    def trend(r, s):
        if "trend" not in (s.get("blocks") or []):
            return _cell(_ABSENT_TREND, absent=True)
        bits = [fmt_signed_pct(s.get("trend_pct_change"))]
        if s.get("trend_direction"):
            bits.append(str(s["trend_direction"]))
        if s.get("trend_strength"):
            bits.append(str(s["trend_strength"]))
        if s.get("trend_r2") is not None:
            bits.append(f"R² {s['trend_r2']}")
        return _cell(", ".join(bits))

    def concentration(r, s):
        if "concentration" not in (s.get("blocks") or []):
            return _cell(_ABSENT_CONC, absent=True)
        return _cell(f"top 3 = {s.get('top3_share')}% of {s.get('n_categories')} categories")

    def missing(r, s):
        n = s.get("null_count")
        if n is None:
            return _cell("not measured", absent=True)
        pct = s.get("null_pct")
        return _cell(f"{n:,}" + (f" ({pct}%)" if pct is not None else ""))

    def outliers(r, s):
        n = s.get("anomaly_count")
        if n is None:
            return _cell("not measured", absent=True)
        method = s.get("anomaly_method")
        return _cell(f"{n}" + (f" ({method})" if method else ""))

    def stat(key, formatter=None):
        def get(r, s):
            if not s.get("available") or s.get(key) is None:
                return _cell("not measured", absent=True)
            fmt = formatter or (lambda v: fmt_measure(v, s.get("measure_is_currency")))
            return _cell(fmt(s[key]))
        return get

    return [
        row("Report name", lambda r, s: _cell(r["report_name"])),
        row("Pattern", lambda r, s: _cell(r.get("pattern_used"))),
        row("Question asked", lambda r, s: _cell(r.get("question_answered"))),
        row("Source files", lambda r, s: _cell(", ".join(r.get("source_files") or []))),
        row("Rows analysed", lambda r, s: _cell(f"{r.get('report_rows') or 0:,}")),
        row("Measure", lambda r, s: _cell(s.get("measure_label")), emphasis=True),
        row("Headline",
            lambda r, s: _cell("not measured", absent=True) if not s.get("available")
            else _cell(f"{s.get('headline_label') or ''}: "
                       f"{fmt_measure(s.get('headline_value'), s.get('measure_is_currency'))}".strip(": "))),
        row("Peak",
            lambda r, s: _cell("not measured", absent=True) if not s.get("available")
            else _cell(f"{fmt_measure(s.get('peak_value'), s.get('measure_is_currency'))}"
                       + (f" at {s['peak_label']}" if s.get("peak_label") else ""))),
        row("Low",
            lambda r, s: _cell("not measured", absent=True) if not s.get("available")
            else _cell(f"{fmt_measure(s.get('trough_value'), s.get('measure_is_currency'))}"
                       + (f" at {s['trough_label']}" if s.get("trough_label") else ""))),
        row("Median", stat("median")),
        row("Mean ± std",
            lambda r, s: _cell("not measured", absent=True) if not s.get("available")
            else _cell(f"{fmt_measure(s.get('mean'), s.get('measure_is_currency'))} "
                       f"± {fmt_measure(s.get('std'), s.get('measure_is_currency'))}")),
        row("IQR", stat("iqr")),
        row("Spread (CV)", stat("cv", lambda v: f"{v}%")),
        row("Trend", trend),
        row("Concentration", concentration),
        row("Outliers", outliers),
        row("Missing values", missing),
        row("Chart type", lambda r, s: _cell(r.get("chart_type"))),
    ]


def build_export_context(
    session: Dict[str, Any],
    report_types: Sequence[str],
    *,
    chart_images: Optional[Dict[str, str]] = None,
    dist_images: Optional[Dict[str, str]] = None,
    include_appendix: bool = True,
    appendix_row_limit: int = MAX_APPENDIX_ROWS,
) -> Dict[str, Any]:
    """Everything both templates need, with no session objects left in it."""
    letters = [t.upper() for t in report_types]
    reports = [
        _report_context(
            session, letter,
            chart_images=chart_images,
            dist_images=dist_images,
            include_appendix=include_appendix,
            appendix_row_limit=appendix_row_limit,
        )
        for letter in letters
    ]

    all_files: List[str] = []
    for r in reports:
        for f in r["source_files"]:
            if f not in all_files:
                all_files.append(f)

    generated = [r["generated_at"] for r in reports if r["generated_at"]]

    return {
        "session_id": _session_id(session, letters),
        "reports": reports,
        "combined": len(reports) > 1,
        "all_source_files": all_files,
        "generated_at": max(generated) if generated else None,
        "comparison_rows": _comparison_rows(reports) if len(reports) > 1 else [],
        "include_appendix": include_appendix,
    }


# ============================================================================
# RENDER
# ============================================================================

def render_export_html(session: Dict[str, Any], report_types: Sequence[str], **kwargs) -> str:
    """A standalone HTML file — no external stylesheets, scripts, fonts or images.

    It has to render from the user's Downloads folder with the app shut down.
    """
    context = build_export_context(session, report_types, **kwargs)
    return _env.get_template("export_web.html").render(**context)


def render_export_pdf(session: Dict[str, Any], report_types: Sequence[str], **kwargs) -> bytes:
    """Render to PDF bytes.

    Raises:
        ExportRenderError: the renderer reported errors. Checked explicitly because
            pisa otherwise returns a truncated-but-valid PDF, which would reach the
            user as a successful download of a broken file.
    """
    context = build_export_context(session, report_types, **kwargs)
    html = _env.get_template("export_pdf.html").render(**context)

    buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=buffer, encoding="utf-8")
    if result.err:
        raise ExportRenderError(
            f"The PDF renderer reported {result.err} error(s) while building the document."
        )

    pdf = buffer.getvalue()
    if not pdf.startswith(b"%PDF"):
        raise ExportRenderError("The PDF renderer produced no output.")
    return pdf
