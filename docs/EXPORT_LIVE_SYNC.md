# Keeping the export in sync with the live report

Read this before touching `app/data/templates/` or `app/data/export_builder.py` in
response to "the download doesn't match the report." `docs/EXPORT_FEATURE.md` is
the orientation doc for the export feature generally; this one is specifically
about the fact that it has a twin, and the twin drifts.

---

## The one fact this whole doc is about

**There are two independent implementations of the report, not one.**

| | Live (on screen) | Export (download) |
|---|---|---|
| Content markup | `app/web/src/components/Reportsdashboard.jsx`, `StatTile.jsx`, `SkewGlyph.jsx`, `RangeSummary.jsx` (React) | `app/data/templates/_macros.html` (Jinja, shared by both shells) |
| Number formatting | `app/web/src/format.js` | Python ports in `app/data/export_builder.py`, registered as Jinja filters |
| Layout/CSS | `app/web/src/css/dashboard.css` (flexbox/grid, real browser) | `<style>` blocks inside `export_web.html` (flexbox OK, real browser) **and** `export_pdf.html` (no flexbox, no grid, no `position` — xhtml2pdf) |
| Chart pixels | `chartLayout.js` + Plotly, live in the DOM | Same `chartLayout.js`, rasterised client-side by `export.js` and POSTed up — **this one auto-propagates**, see below |

Both sides read the **same** `stats` dict — the one `report_stats.py` computes
server-side and stores in `SESSIONS[sid]["reports"][letter]["stats"]`. Adding a
field there reaches both UIs for free. What does **not** propagate automatically is
*how a component chooses to display* an existing or new field — that's independent
code on each side, and only one side gets edited when someone says "update the UI."

### The one exception: chart images

`chart_builder.py` returns an *unstyled* Plotly figure; all presentation (median
line, min/max labels, legend, height) is applied by `chartLayout.js` at draw time —
in the browser, for both the live chart **and** the exported PNG (`export.js`
calls the same `buildLayout()`). So a `chartLayout.js`-only change (e.g. the pie
legend added in `ea9a4e6`) needs **no template edit** — it's already correct in
every export generated after that commit. Confirm this is still true before
assuming a chart-visuals change needs a template change too.

Everything else in this doc is about the parts that don't self-propagate.

---

## How to tell if the export has drifted

```bash
# Newest commit that touched the export side:
git log -1 --oneline -- app/data/templates/ app/data/export_builder.py

# Commits on the live side since then:
git log --oneline <that-sha>..HEAD -- \
  app/web/src/components/Reportsdashboard.jsx \
  app/web/src/components/ui/StatTile.jsx \
  app/web/src/components/ui/RangeSummary.jsx \
  app/web/src/components/ui/SkewGlyph.jsx \
  app/web/src/format.js \
  app/data/report_stats.py
```

Anything in that second list is a candidate for drift. Read its diff — most
`report_stats.py` changes are new/renamed fields (harmless until something reads
them), but a diff touching `StatTile.jsx`/`Reportsdashboard.jsx`/`format.js` almost
always changes what gets *shown*, which is exactly what the export needs ported.

---

## The verification loop — no LLM call, no browser, seconds per cycle

`docs/EXPORT_FEATURE.md` already says to do this in a script rather than through
the app. `scripts/preview_export.py` is that script:

```bash
ai-env\Scripts\python scripts\preview_export.py                       # newest session, every report
ai-env\Scripts\python scripts\preview_export.py 20260809_172303_279b25 --letters AB
```

It rehydrates a saved session from `session_data/<id>/` (needs
`SAVE_REPORT_HISTORY=true` on whatever run produced it — `session_data/` is not
empty in this repo, so there's almost always something to replay already) and
calls `render_export_html` / `render_export_pdf` directly — the exact same code
path `/api/export/{sid}` runs. Output lands in `scripts/_export_previews/<id>/`
(gitignored): `combined.html`/`.pdf` plus one single-report pair per letter.

Charts print a "no chart could be drawn" placeholder in every preview — chart PNGs
are rasterised in a real browser by `export.js` and POSTed up, and this loop has no
browser. That's the one part it can't check. Everything else (KPI tiles, findings,
distribution, comparison matrix, appendix, provenance) renders exactly as a real
export would, because it *is* a real export.

### Reading the output without Puppeteer

The `chrome-devtools` MCP does not attach in this environment. Two things that do
work here, and don't need Puppeteer or an MCP:

**PDF → PNG**, so the `Read` tool can look at a real rendered page:
```python
import fitz  # PyMuPDF, already installed
doc = fitz.open("scripts/_export_previews/<id>/A.pdf")
doc[0].get_pixmap(dpi=200).save("page1.png")
```
(`pypdf`'s `extract_text()` is faster for a pure text/wording check, but it
collapses adjacent inline elements with no whitespace between them — e.g. a name
and its delta badge extract as `"GTX▲ +120%"` with no space even though the CSS
margin renders one visibly. Don't read a missing space in extracted text as a
layout bug; render the page to confirm.)

**HTML → PNG**, using the system Chrome install directly (no puppeteer package
needed, no dev server needed — `export_web.html` is self-contained):
```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --headless=new --disable-gpu --no-sandbox \
  --window-size=1400,1600 --screenshot=out.png \
  "file:///C:/full/path/to/A.html"
```
Tune `--window-size` height to whatever's needed; the shot is the viewport, not
the full scroll height, so a tall document needs a tall window to capture past the
fold. For checking one isolated widget (e.g. just the KPI row for a hand-built
edge case), it's often faster to render a tiny standalone HTML fragment — pull the
relevant `<style>` rules out of `export_web.html`, call the one macro you're
testing via `_env.get_template("_macros.html").module.kpi_table(report)` — than to
scroll a full document screenshot to the right spot.

---

## Porting a live-side change into the export

1. **Read the live diff fully first.** Get the exact prop/field names and the
   exact conditional logic (when is something shown vs. omitted vs. falls back to
   something else) — `git show <sha> -- <file>`. Guessing the wording from the
   rendered screen is how the export ends up subtly wrong.

2. **Number formatting → `export_builder.py`, mirroring `format.js`.** Every
   `format.js` export function should have a same-named-in-spirit Python twin
   registered as a Jinja filter or global at the bottom of the "FORMATTING" /
   Jinja-env section of `export_builder.py`. Existing pattern to copy:

   | format.js | export_builder.py | Jinja |
   |---|---|---|
   | `compactNumber` | `fmt_compact` | `\| compact` |
   | `exactNumber` | `fmt_exact` | `\| exact` |
   | `signedPercent` | `fmt_signed_pct` | `\| signed_pct` |
   | `compactMeasure` | `fmt_measure` | `\| measure(is_currency)` |
   | `directionGlyph` | `fmt_direction_glyph` | `\| direction_glyph` |
   | `deltaVsAverage` | `delta_vs_average` | `delta_vs_average(value, mean)` (global fn, not a filter — takes two independent args) |
   | `humanizeColumn` | `fmt_human` | `\| human` |

   One deliberate divergence: `fmt_measure`/`fmt_compact` etc. return the literal
   `"—"` placeholder for a missing value, where their JS counterparts return the
   raw `null`/`undefined` through. That's because the JS side leans on that to let
   React's own `isEmpty` check apply "muted empty" styling — a Jinja concern that
   doesn't exist here. Don't chase that difference; it's intentional (see the
   docstring on `fmt_measure`).

3. **Content markup → `_macros.html` only.** Never write the same markup into
   `export_web.html` and `export_pdf.html` separately — that's exactly how the two
   would drift from *each other*, which `docs/EXPORT_FEATURE.md` calls out as the
   first thing that goes wrong. Add a parameter to the relevant macro (see
   `kpi_cell`'s `note`/`note_title`/`name`/`delta`/`warn` params for the shape of
   this) rather than forking the macro.

4. **CSS → both shells, differently.** `export_web.html` is a real browser: flex,
   grid, `gap`, CSS vars all work, and the existing `.dist__label-row` /
   `.stat-tile__label-row` pattern (label left, badge/chip right,
   `justify-content: space-between`) is the one to copy for "two things on one
   row." `export_pdf.html` is xhtml2pdf: pt units, literal hex (no `var(--x)`), no
   flexbox/grid/`position`. For "two inline things on one row" there, don't reach
   for flex — two adjacent `<span>`s with `margin-left` on the second one is
   sufficient and is what the outlier chip and the name/delta pair both do now.
   Never `em` on `letter-spacing` — silently dropped by xhtml2pdf, use `pt`.
   Never the U+26A0 ⚠ glyph — renders as a black box; use a plain word instead
   (`"Outlier"`, `"Warning"`). `· ± − → ▲ ▼ ✓ " " —` are all confirmed fine.

5. **A bordered box containing a `<ul>` will split at a page boundary in the PDF,
   redrawing its border/background around each fragment separately** — confirmed by
   rendering, not assumed; `page-break-inside: avoid` does **not** prevent this in
   xhtml2pdf for a `<div>`, even though it looks like it should. The fix used for
   `ai_rationale`'s box: make the box itself a single-cell `<table>` rather than a
   `<div>` — xhtml2pdf keeps a table atomic where it won't keep a div+list atomic,
   which is the same reason every "row of tiles" elsewhere in this file is already a
   table. A `<div>` that only ever holds a `<p>` (no list) doesn't have this problem
   and doesn't need the table treatment — `ai_note()`'s box is proof, it never split.
   Separately, `-pdf-keep-with-next: true` on `.h2` *is* reliably honoured and stops
   a heading being stranded alone at the bottom of a page — cheap insurance to apply
   globally rather than something to reach for only when a heading gets orphaned.

6. **A `title="…"` tooltip is an acceptable place for hover-only detail**, even
   though it's inert in the PDF (no cursor, no tooltip on paper). This is already
   the established pattern for the Distribution card's std-dev/IQR explanations
   (`.dist__pair--spread`) and is what the Trend tile's R² explanation now does
   too, matching how the live `StatTile` hides it in a `title` on the note span.
   Don't feel obliged to invent a print-only visible fallback — but do reconsider
   per-case whether the hidden info is something a PDF reader actually needs, since
   nothing else in the *single-report* export currently surfaces it if the tooltip
   is the only place it lives. (The combined document's comparison matrix
   spells R² out in visible text regardless, as one data point in favour of
   "visible beats hidden" when a table row has the room.)

7. **Decide deliberately whether a change reaches the comparison matrix too.**
   `_comparison_rows()` in `export_builder.py` is a third, *independent* rendering
   of a subset of the same stats — it has no on-screen equivalent (the closest
   thing, `CompareView`'s "All reports" grid, is a much lighter card and doesn't
   share code with it either). When the 2026-08-10 currency-prefix change landed,
   the matrix's Headline/Peak/Low/Median/Mean±std/IQR rows were deliberately
   updated to use `fmt_measure` too, for internal consistency within one exported
   document (a single-report page showing `$1.9M` right above a matrix on another
   page in the same file showing `1.9M` reads as a bug even though nothing forced
   the two to match). Apply the same judgement call again: match the matrix when
   the alternative is a document that visibly contradicts itself; don't chase
   every live-side change into it automatically, since it's genuinely a separate
   design surface with its own constraints (`_ABSENT_TREND`/`_ABSENT_CONC`, the
   "different measures" caption — see `docs/EXPORT_FEATURE.md`).

8. **Two adjacent inline elements with `margin-left` on the second one is the
   documented pattern for point 4 above, but it is not universally reliable** — it
   worked for `.kpi__delta` and the outlier chip, but silently rendered as zero gap
   for the brand mark's icon-then-wordmark lockup (confirmed by rendering, not
   assumed: same CSS property, different result). When a margin-left on an inline
   sibling doesn't show up in a render, don't debug the margin value — switch the
   pair to a `<table><tr><td>…</td><td>…</td></tr></table>` with padding on the
   `<td>`s instead, which is what `brand_header()` does and what every other
   "two things side by side" case in this file already does for the same reason.

9. **An unwidthed `<table>` stretches to the full available page width in
    xhtml2pdf, unlike a browser, which shrink-wraps it to its content** —
    confirmed by rendering the table-ized `brand_header()` with no `width` set: the
    icon landed far left and the wordmark far right, overlapping the title below
    it. Give any table that's meant to size to its content (a lockup, not a
    grid/list) an explicit `width` in the PDF CSS — `.brand { width: 150pt; }` is
    the current example. This is a PDF-only concern; the same unwidthed table in
    `export_web.html` shrink-wraps correctly because it's a real browser.

10. **Verify, then run `tests/test_export_api.py`.** It doesn't assert exact
   wording for most of this (a deliberate choice in that file — see its own
   comments), so a passing suite is necessary but not sufficient; the visual/text
   check above is what actually catches wording drift. Run it anyway — it does
   catch structural breakage (missing content blocks, `not measured` text,
   self-containment). When wording changes on purpose (a heading renamed, a chip
   removed), the handful of assertions in that file that do pin exact text need a
   deliberate update to match — that's not the suite catching a regression, that's
   the suite doing its job of noticing the contract moved.
