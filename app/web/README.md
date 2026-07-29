# AI-Dashboard — Frontend

A **React 18 + Vite** single-page app for uploading spreadsheet data and viewing
AI-generated analysis reports.

> This app is only half the system. It calls a FastAPI backend on
> `http://localhost:8000`, which must be running for anything beyond the static UI to
> work. See [RUNNING_APP.md](../../RUNNING_APP.md) for the full two-terminal setup.

---

## Prerequisites

- [Node.js](https://nodejs.org/) v18 or higher
- npm (comes with Node)

---

## Getting started

All commands use `--prefix app/web` so they work from the repository root. If you `cd`
into `app/web` first, drop the prefix.

```bash
# 1. Install dependencies (once, or after pulling new changes)
npm --prefix app/web ci

# 2. Start the dev server on http://localhost:5173
npm --prefix app/web run dev

# 3. Production build -> app/web/dist/
npm --prefix app/web run build

# 4. Preview the production build locally
npm --prefix app/web run preview
```

Remember to start the backend in a second terminal, or every upload will fail:

```bash
uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

---

## Project structure

```
app/web/
├── index.html                  # App shell. Loads /src/main.jsx and the Plotly CDN
│                               # script; the entire UI mounts into <div id="root">
├── vite.config.js              # Vite + @vitejs/plugin-react
├── package.json
├── public/
│   └── favicon.svg             # Copied verbatim into dist/
└── src/
    ├── main.jsx                # Entry point: createRoot -> <App>, imports both stylesheets
    ├── App.jsx                 # Nav, tab routing, and ALL shared session state
    │                           # (files, sessionId, recommendations, reports cache)
    ├── api.js                  # fetch client: API_BASE, generateReport, REPORT_TYPE_LETTERS
    ├── format.js               # compactNumber / exactNumber / humanizeColumn / clockTime
    ├── chartLayout.js          # Plotly layout theming applied in Plot.jsx
    ├── components/
    │   ├── Uploaddashboard.jsx    # Step 1 — file picker; POSTs /api/analyze-full
    │   ├── Analysisdashboard.jsx  # Step 2 — recommendation cards
    │   ├── Reportsdashboard.jsx   # Step 3 — chart, stat tiles, data table, compare view
    │   ├── Settingsdashboard.jsx  # Settings — UI only, nothing is persisted yet
    │   └── ui/                    # Shared primitives
    │       ├── Card.jsx           #   The white surface every panel sits on
    │       ├── DataTable.jsx      #   Sortable report table
    │       ├── Plot.jsx           #   Plotly wrapper (reads window.Plotly from the CDN)
    │       ├── Section.jsx        #   Titled section wrapper
    │       ├── Sparkline.jsx      #   Inline trend line for stat tiles
    │       └── StatTile.jsx       #   Single KPI tile
    └── css/
        ├── tokens.css          # Design tokens: colors, spacing, radii, fonts
        └── dashboard.css       # All component styling
```

Only these two stylesheets exist, and both are imported by `main.jsx`. There is no
per-page CSS layer — the seven page-specific stylesheets that used to live here
belonged to the pre-React app and were removed on 2026-07-28.

---

## State and data flow

`App.jsx` owns all cross-tab state; the dashboards are presentational and receive it
as props.

```
Upload tab      Uploaddashboard POSTs /api/analyze-full
                  -> setSessionId, setRecommendations, setFileProfiles
                  -> switches to the Analysis tab

Analysis tab    Recommendation cards. Choosing one calls requestReport(letter)

requestReport   POSTs /api/generate-report unless that letter is already cached
                in the `reports` map, so switching between A/B/C — or opening the
                compare view — never re-requests a report the server already built

Reports tab     Renders reports[activeType]: chart, stat tiles, table, compare view
```

A new upload calls `startNewSession()`, which clears the report cache — reports built
from the previous dataset are invalid.

---

## Styling

Design tokens live in `src/css/tokens.css` and are consumed both by `dashboard.css`
and directly in a few components via `var(--color-...)`. To restyle the app, start
there.

```css
:root {
  --color-accent: #2563EB;
  --color-text: #111827;
  /* ... */
}
```

Note that a fair amount of layout is still written as inline `style={{}}` objects in
the JSX (a holdover from the React migration). Anything that must respond to a
breakpoint has to live in `dashboard.css` instead — inline styles beat media queries.

---

## Charts

Plotly is loaded from a **CDN script tag** in `index.html`, not bundled — `Plot.jsx`
reads `window.Plotly`. The figure itself (traces, axes, colors) is built server-side
in `app/data/chart_builder.py`; `chartLayout.js` only applies presentation theming on
top of what the backend sends.

---

## Current gaps

| Feature | Status |
|---|---|
| Upload, analysis, report generation | Working end-to-end |
| Chart, stat tiles, data table, compare view | Working |
| Settings | UI renders but nothing is saved — the controls are local state only |
| Export (PDF / HTML / CSV / email) | **Not implemented.** The buttons render disabled; there is no `/api/export` endpoint on the backend |
| Sign-in | Removed 2026-07-28. There was an inert button with no auth behind it |

---

## History

This app was migrated from vanilla JS to React on 2026-07-17. The dead vanilla files
(`main.js`, `src/js/*`, `template.html`, and seven stylesheets) were removed on
2026-07-28. See [docs/REACT_MIGRATION_SUMMARY.md](../../docs/REACT_MIGRATION_SUMMARY.md).
