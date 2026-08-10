# Report Export — PDF / HTML / email

Orientation doc for the export feature, written 2026-07-29. Read this before exploring
the export code; it records the decisions and the non-obvious constraints, which are
the parts that cost time to rediscover.

Covers course brief requirement 3: *"users can download those reports as PDF or HTML
or send those reports to themselves or to others through email."*

**If you're here because a download doesn't match what the app shows on screen**,
that's a different doc: [`EXPORT_LIVE_SYNC.md`](EXPORT_LIVE_SYNC.md) covers why that
happens (two independent implementations of the same report) and the fast
no-LLM/no-browser loop for fixing it.

---

## What it does

The Reports page ends in an export panel. Tick any **generated** reports, then
`Download PDF` / `Download HTML`, or email them.

- **One report selected** → single-report document.
- **Two or more** → **one combined comparative document** with a comparison matrix.
  There is no "N separate files" mode, therefore no zip.

Previously this section was three cards ("Summary report" / "Full analysis" /
"Recommendations") × three disabled buttons. Those three labels named nothing the app
produces — the real units are the reports **A / B / C**, so selection is per report.

---

## Architecture, and the one decision that explains most of the code

```
ExportPanel (Reportsdashboard.jsx:549)
  -> export.js collectChartImages()      rasterises each selected chart IN THE BROWSER
  -> api.js exportReports()              POST /api/export/{sid}  (blob response)
  -> api.py _resolve_export()            validate session + letters
  -> export_builder.render_export_pdf()  Jinja -> xhtml2pdf
     export_builder.render_export_html()  Jinja -> standalone HTML
  -> triggerDownload()                   blob -> synthetic <a download>
```

**Chart images are rendered client-side and POSTed up. This is deliberate.**

`chart_builder.py` returns an *unstyled* Plotly `{data, layout}` dict. Every bit of a
chart's presentation — font, median reference line, min/max annotations, the extra
height a sideways bar chart needs — is applied by
[`buildLayout()`](../app/web/src/chartLayout.js) at draw time in the browser. So:

- Rendering server-side would produce a chart that **doesn't match what the user
  approved on screen**, unless `chartLayout.js` were ported to Python and kept in sync.
- It would also need Kaleido, which since v1 wants a Chromium binary fetched at runtime.

Instead [`chartPngDataUrl`](../app/web/src/export.js) draws into a detached offscreen
`<div>` with the real `buildLayout`, calls `Plotly.toImage`, and sends the data URL.
**Nothing in `export_builder.py` imports plotly.** Do not "optimise" this by moving it
server-side.

An offscreen node is used rather than passing a bare figure object to `toImage`,
because the figure-object form is version-fragile and ignores container sizing.

---

## Files

| File | Role |
|---|---|
| [app/data/export_builder.py](../app/data/export_builder.py) | Context building, Jinja env, PDF/HTML render, filenames, image validation |
| [app/data/emailer.py](../app/data/emailer.py) | SMTP send + typed failures |
| [app/data/templates/_macros.html](../app/data/templates/_macros.html) | **All** content markup, shared by both targets |
| [app/data/templates/export_pdf.html](../app/data/templates/export_pdf.html) | Print shell: `@page`, footer frame, table-based layout |
| [app/data/templates/export_web.html](../app/data/templates/export_web.html) | Standalone HTML shell: grid, sticky headers, `@media print` |
| [app/api.py](../app/api.py) | 3 routes + `ExportRequest`/`EmailExportRequest` |
| [app/web/src/export.js](../app/web/src/export.js) | `chartPngDataUrl`, `collectChartImages`, `triggerDownload` |
| [app/web/src/api.js](../app/web/src/api.js) | `postForBinary`, `exportReports`, `emailReports`, `fetchExportStatus` |
| [app/web/src/components/Reportsdashboard.jsx](../app/web/src/components/Reportsdashboard.jsx) | `ExportPanel` at :549, mounted at :131 |
| [tests/test_export_api.py](../tests/test_export_api.py) | 35 integration tests |
| [scripts/preview_export.py](../scripts/preview_export.py) | Renders a saved session straight to `.html`/`.pdf` — no LLM, no browser. See `EXPORT_LIVE_SYNC.md`. |

Both template shells `{% import '_macros.html' as m %}` and call the same macros in the
same order. That is the only thing stopping the PDF and HTML from drifting apart — and
the first thing that would drift is the provenance labelling.

### Routes

```
GET  /api/export/{session_id}/status  -> {generated: ["A","B"], email_configured: bool, export_available: bool}
POST /api/export/{session_id}         -> application/pdf | text/html  (binary body)
POST /api/export/{session_id}/email   -> {status: "sent", recipients, ...}
```

`status` exists so the UI can disable the email row **with the reason showing** before
the user types an address, rather than after.

POST rather than GET for downloads because the chart PNGs travel in the body.

---

## Non-obvious constraints — the expensive-to-rediscover list

### 1. `SESSIONS[sid]["reports"]` is keyed by letter ([api.py:540](../app/api.py#L540))

It used to be a single `SESSIONS[sid]["report"]` slot, overwritten on every generate —
so generating A then B left only B server-side while the browser still showed both, and
a combined export was impossible to fulfil. The legacy `["report"]` key is still set to
**the same dict object** (no memory doubling) because
`tests/test_generate_report_api.py:177` reads it.

### 2. xhtml2pdf has no flexbox, no grid, no `position`

Every tile row in `_macros.html` is a `<table>` with percentage `<td>`s. Also:

- Use `pt` / `%`, never `rem`. **`em` on `letter-spacing` is silently dropped** with a
  `getSize: Not a float` warning — use `pt`.
- No CSS variables; inline the hex from `tokens.css` and keep them in step.
- Wide tables **clip** rather than shrink → hence `MAX_APPENDIX_COLS = 8`.
- **`⚠` (U+26A0) has no glyph and renders as a black box.** Use the word "Warning".
  `· ± − → ▲ ▼ ✓ " " —` are all fine.
- `repeat="1"` on a `<thead>` repeats it across pages. Works.

### 3. HTML entities inside Jinja *expressions* double-escape

`bits | join(' &middot; ')` emits visible `&middot;` text. The bits hold user data
(file names), so the string can't be marked `|safe` — use the literal `·` character.
Entities in plain template *text* are fine.

### 4. A malformed chart PNG raises an uncaught `OSError` from inside reportlab

`broken data stream when reading image file`, thrown deep in the PDF writer. So
[`_sanitize_data_url`](../app/data/export_builder.py#L301) checks the prefix,
`b64decode(validate=True)`, the size cap, and `Image.open(...).verify()` — then **logs
and drops, never raises**. A dropped image costs a chart; raising costs the whole file.
This is what `pillow` is in `requirements.txt` for.

### 5. `pisa.CreatePDF(...).err` must be checked

Otherwise a failed render returns a **truncated-but-valid PDF with HTTP 200** — a
successful download of a broken file.

### 6. `Content-Disposition` needs `expose_headers` ([api.py:78](../app/api.py#L78))

It is not a CORS-safelisted response header, so `:5173 → :8000` reads `null` for the
filename without it. The old `window.location.href` download approach dodged this;
POST-blob does not. `filenameFromDisposition` returns `null` gracefully and the client
falls back to a locally built name.

### 7. Appendix rows are **raw pandas**, not JSON

`SESSIONS[sid]["reports"][L]["data"]` comes from `report_df.to_dict(orient="records")`,
so Timestamps, numpy scalars and `NaN` arrive as objects. Without the `cell` Jinja
filter the appendix prints `2023-01-01 00:00:00` and `nan` in every cell. The Jinja
filters (`compact`, `signed_pct`, `cell`, `human`, `clock`) are **ports of
[format.js](../app/web/src/format.js)** — an export whose numbers round differently
from the screen is a different document.

### 8. Only generated reports can be exported

Ungenerated letters have no `chart` dict in `App.jsx` state, and `Plot.jsx` purges its
DOM node on unmount, so there is physically nothing to rasterise. The checkbox is
disabled with the reason; server-side it's a 400 naming the letter.

### 9. `load_dotenv()` is read lazily inside `emailer._smtp_settings()`

`load_dotenv()` only runs as a side effect of importing `AI_Engine`, so a module-level
`os.getenv` would race the import order and see nothing — same workaround as
`report_builder._debug_files_enabled()`. Bonus: you can fill in `.env` and retry
without restarting uvicorn.

### 10. Export imports have their own `EXPORT_AVAILABLE` flag ([api.py:56](../app/api.py#L56))

Not the big `DATA_MODULES_AVAILABLE` try/except — a broken xhtml2pdf must not take
`/api/analyze-full` down with it.

---

## Document design (and why)

**Only one chip survives anywhere in the document: the outlier chip on the KPI
tiles.** Until 2026-08-10 every computed statistic carried a `computed` chip and
every pre-execution question or note carried `AI question` / `AI note`, mirroring
the dashboard's chip convention (see the header comment in `Reportsdashboard.jsx`).
A user-requested declutter pass removed all of that, first down to just `AI note`
on the "Why the model proposed this report" block, then — in a second pass the same
day — removed that last chip too, replacing it with a plain `Note:` label inside a
section renamed "AI notes". See the provenance comment at the top of `_macros.html`
and `docs/EXPORT_LIVE_SYNC.md`'s 2026-08-10 note for the full history. The cover no
longer carries a chip legend — there's nothing left in body text to explain, and the
"AI notes" section explains itself inline ("Written by the model *before* any data
was aggregated. Not a finding.").

> A previous, deleted implementation (commit `993e0fa`) rendered `rationale_bullets`
> under a heading reading **"Key Insights"**. Those are written by the model *before a
> single row is aggregated*. Do not reintroduce that. They now sit under "AI notes"
> with a plain `Note:` label and an explicit disclaimer.

### Single report
1. **Brand header** — the nav bar's logo mark plus "AI-Dashboard" wordmark
   (`brand_header()`), once per document above the title.
2. **Overview** — title, generated date, "Question asked:" (plain text, not
   a chip), optional warning banner, 4-up KPI table, chart.
3. **Distribution** — numeric-only in the PDF (`distribution_table()`); in the web
   export, also the Range/Center/Spread graphics ported from the live dashboard
   (box plot, skew bell-curve, variance meter — `distribution_card()`), since
   xhtml2pdf can't render the inline SVG the skew curve needs. In the PDF this
   stays on the same page as the KPI tiles and chart it explains — the page break
   sits immediately before Findings, not before Distribution.
4. **Findings, then AI notes** (page break, PDF) — the shape of the data comes
   first, then the claims about it. Findings is the three `InsightRail` blocks (Key
   finding, Outliers, Data quality), each amber *only* when something was detected;
   AI notes is the model's pre-execution rationale, kept visually distinct with a
   dotted border and the `Note:` label.
5. **How this report was built** — source files, row count, and the pipeline steps as
   plain-language bullets, not raw tokens run together. This is
   `report_method_block()`, the same macro the combined document's Method & Provenance
   section uses per report.
6. **Appendix** — data table, 200 rows / 8 columns, with a truncation footnote.

Footer on every page: `AI-Dashboard · session {sid} · page N of M`.

### Combined (2+)
Cover (report list + one generated timestamp, no legend) → **comparison matrix** →
Key findings per report → How these reports were built → each report in full →
appendices.

The matrix is placed **before** Key findings per report: it is the reason several
reports were exported together, and the summary is derived from the same numbers.
There is no "what to check next" list at the combined-summary level and no
Recommendations section — both were judged redundant with what's already on each
report's own page, and were removed in the same pass.

**Two rules in the matrix matter more than its row list:**

1. **Absent ≠ zero.** A report with no ordered axis prints
   `not measured — no ordered axis`, never a blank and never `0`. A blank cell in a
   comparison table reads as "we measured this and found nothing", which is a lie. See
   `_ABSENT_TREND` / `_ABSENT_CONC` in
   [export_builder.py](../app/data/export_builder.py#L410).
2. **Cross-measure hazard.** A/B/C almost always measure *different quantities*, and a
   matrix invites reading down a column comparing numbers that aren't the same thing.
   So `Measure` is a bold emphasised row near the top and an amber caption sits
   **above** the table. It was originally below, where it got orphaned onto the next
   page — i.e. read only after the reader had already made the mistake.

Key findings per report contains **no synthesised cross-report prose**. Nothing in
this codebase can honestly compute "Report B contradicts Report A", and inventing it
is exactly the failure the provenance labelling exists to prevent.

---

## Email

**Attachment only, both formats.** Gmail and Outlook strip `<style>` blocks and refuse
`data:` image sources, so an inline HTML body would arrive unstyled and chartless. The
`.html` attachment carries charts as data URIs and opens offline.

If inline HTML is ever wanted: it needs fully inlined `style=` attributes plus charts
re-attached as `multipart/related` CID parts, with the template taking a
`chart_src_mode: 'data' | 'cid'` knob. Not built.

Failure mapping — every case actionable, **none silently succeeding**:

| Cause | Status |
|---|---|
| `EmailNotConfigured` | 503 (names the env vars and `.env.example`) |
| Bad address syntax (checked *before* rendering) | 422 (names the address) |
| `SMTPAuthenticationError` | 502 (mentions Gmail app passwords) |
| No STARTTLS offered while credentials are set | 502 (refuses rather than sending the password in clear) |
| `SMTPRecipientsRefused` | 400 |
| timeout / `OSError` | 504 (20s timeout; without it a wrong host hangs for minutes) |
| `ExportRenderError` | 500 |

Config: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `.env`.
Port 465 → implicit TLS (`SMTP_SSL`); anything else → STARTTLS. Leave `SMTP_HOST` blank
to keep the feature off cleanly. `.env.example` carries recipes for Mailtrap, Yahoo,
Gmail and Brevo — **all of them SMTP, so switching provider is never a code change.**
Resist provider SDKs for that reason: they buy nothing this module doesn't already do
and cost a dependency plus the typed failure mapping.

The trade-off between them is who you're allowed to send to:

| Provider | Delivers to | Cost of setup |
|---|---|---|
| Mailtrap **Sandbox** | nobody — captured in a web inbox, any recipient accepted | account only |
| Mailtrap **Sending**, demo domain | only the Mailtrap account owner's own address | account only |
| Mailtrap **Sending**, own domain | anyone | needs a domain you own |
| Yahoo / Gmail | anyone | app password on a personal account |
| Brevo | anyone | verified sender in their dashboard |

Yahoo and Gmail reject a `SMTP_FROM` they don't own, so it must equal `SMTP_USER`.
Mailtrap Sandbox is the best development target — it accepts any recipient and lets you
download the attachment — but it is not a demo of delivery, because nothing leaves it.

### Credentials are optional, but all-or-nothing

`smtp_config_error()` is the single source of truth — the endpoint's pre-flight 503 and
`send_report_email`'s own guard both call it, so they cannot drift. Four states:

| `.env` | Result |
|---|---|
| no `SMTP_HOST` | off — the UI disables the row and says why |
| host + user + password | authenticated relay |
| host, **both** credentials blank | no-auth relay — a local mail catcher |
| host + exactly one credential | **503 naming the empty one** |

That last row is deliberate. A blank `SMTP_PASSWORD` next to a filled-in `SMTP_USER` is
a typo, and silently dropping the login because of it would send mail somewhere nobody
chose.

STARTTLS is **negotiated, not assumed** (`server.has_extn("starttls")`). Calling it
unconditionally is what made this module impossible to test without an account — mail
catchers offer no STARTTLS and reject the command. The downgrade is guarded: if the
server offers no STARTTLS *and* credentials are set, the send is refused rather than
putting `SMTP_PASSWORD` on the wire in clear.

### Verifying without credentials

The only way to see a real PDF arrive as a real attachment without an account:

```
pip install aiosmtpd && python -m aiosmtpd -n -l localhost:1025   # or run Mailpit
```

then `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025`, both credentials blank. Mailpit's UI on
`:8025` lets you download the attachment and open it — which is the one assertion no
unit test can make, since the tests fake `smtplib` and never render a real inbox.

---

## Filenames

```
single:   ai-dashboard-report-a-order-frequency-trend-20260729_143205.pdf
combined: ai-dashboard-reports-abc-combined-20260729_143205.pdf
```

ASCII-only slugs so `Content-Disposition` never needs RFC 5987 encoding. The stamp is
the session id **verbatim** (underscore preserved, not slugified) so files trace back
to `session_data/<id>/`. Letters are uppercased, de-duplicated and **sorted**, so the
same selection always yields the same filename and section order.

---

## Verification

```bash
ai-env\Scripts\python -m pytest tests/ -q          # 124 pass (40 export)
npm --prefix app/web run build
```

Tests use `TestClient` against a hand-built session — **no LLM quota, no browser**.
They reuse `make_session` / `orders_frame` / `recommendation` from
`test_generate_report_api.py`. The ones worth not breaking:

- garbage `chart_images["A"]` → still **200** (guards constraint 4)
- A-then-B → both `SESSIONS[sid]["reports"]` keys live (guards constraint 1)
- HTML export contains no `http://`, `https://` or `<script` (self-containment)
- export HTML contains `Question asked`, `AI notes`, `Note:`, and **not** `"Key Insights"`
- combined export: caption appears *before* `Chart type`; `not measured` present
- appendix cells: no `nan`, no `00:00:00` (guards constraint 7)

**Iterating on template layout:** do it against `render_export_pdf` / `render_export_html`
directly in a script, not through the browser — seconds per cycle.
`scripts/preview_export.py <session_id>` does exactly this — rehydrates a saved
session and renders both formats straight to disk, no LLM call, no browser. See
`EXPORT_LIVE_SYNC.md` for the full workflow, including how to visually check the
output without Puppeteer. (`scripts/replay_report.py` is the older, narrower tool:
chart-only, opens a Plotly preview in a browser tab, needs `SAVE_DEBUG_FILES=true`
on a prior run rather than `SAVE_REPORT_HISTORY=true`.)

**Running the app:** prefer uvicorn **without** `--reload` when testing export by hand —
a reload wipes the in-memory `SESSIONS` and every export depends on live session state.
This is the most likely thing to make the feature look broken when it isn't.

### What was verified in a real browser
Chrome (Puppeteer) driven through upload → generate A/B/C → export, with only
`/api/analyze-full` and `/api/generate-report` stubbed. Confirmed: three real Plotly
PNGs in the request body (1840px wide, ~290 DPI printed), zero console errors, panel
states correct for all-generated / partially-generated / email-off. Those captured
PNGs were then fed back through the renderer. The HTML export was loaded from `file://`
with all network aborted and rendered fully.

---

## Not implemented

- **CSV export.** `Settingsdashboard.jsx` still lists "Raw CSV" as a format; the
  dropdown is decorative. Cheap to add — `report_df.to_csv()`, no new dependencies.
- **Settings "Default export format"** is not wired to the export panel.
- **Email through an authenticated public relay** has not been run — that needs an
  account. A send over a real socket to a local `aiosmtpd` catcher *has*: the route
  delivered an 18.6 KB `%PDF` attachment with no `smtplib` faking anywhere, which
  proves the message construction and the protocol conversation. What remains unproven
  is only `login()` against a real provider and the deliverability that follows.
- **Inline HTML email body** — see the Email section.

## Dependencies added

```
jinja2==3.1.6
xhtml2pdf==0.2.17
pillow==12.2.0     # validates the chart PNGs the browser posts
```

All pure-Python wheels — no GTK/Cairo (WeasyPrint), no wkhtmltopdf, no browser download
(Playwright/Kaleido). `pip install -r requirements.txt` remains the whole story, which
is why xhtml2pdf was chosen over higher-fidelity engines. `reportlab`/`pypdf` are
transitive and deliberately not pinned here; `kaleido` is deliberately absent.
