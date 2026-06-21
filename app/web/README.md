# DataLens — AI Report Builder

A Vite-powered web app for uploading spreadsheet data and generating AI-powered analysis reports.

---

## What is Vite?

Vite is a **build tool and development server** for web projects. It does two things:

**1. In development** — runs a local server (`localhost:5173`) with instant hot reload.
When you save a file, only that module is swapped in the browser — no full page refresh.
This makes editing CSS and JS feel nearly instantaneous.

**2. At build time** — bundles your source files into an optimized `dist/` folder.
CSS gets combined and minified. JS gets bundled and fingerprinted for cache-busting.
That `dist/` folder is what you deploy to Vercel (or anywhere else).

You write in many small, readable files. Your users receive one fast, optimized file.

---

## Prerequisites

- [Node.js](https://nodejs.org/) v18 or higher
- npm (comes with Node)

Check your versions:
```bash
node --version   # should be v18+
npm --version    # should be v9+
```

---

## Getting started (local development)


```bash
# 1. Install dependencies (only needed once, or after pulling new changes)
npm --prefix app/web ci

# 2. Start the dev server
npm --prefix app/web run dev
```

Open `http://localhost:5173` in your browser. The page reloads automatically when you save any file.

**To stop the dev server:** press `Ctrl + C` in the terminal.

---

## Building for production

```bash
npm run build
```

This creates a `dist/` folder with your optimized app. You can preview it locally first:

```bash
npm run preview
# → http://localhost:4173
```

---

## Deploying to Vercel

Vercel reads `vercel.json` and knows to run `npm run build` and serve the `dist/` folder.

### Option A — GitHub (recommended, auto-deploys on every push)

1. Push this project to a GitHub repository.
2. Go to [vercel.com](https://vercel.com) → **Add New → Project** → **Import Git Repository**.
3. Select your repo. Vercel auto-detects Vite — no configuration needed.
4. Click **Deploy**.

Every future `git push` to `main` triggers an automatic redeploy.

### Option B — Vercel CLI

```bash
npm install -g vercel
vercel          # first deploy (follow the prompts)
vercel --prod   # subsequent production deploys
```

### Option C — Drag & drop

Run `npm run build` locally, then drag the `dist/` folder into the Vercel dashboard.

---

## Project structure

```
datalens/
├── index.html              ← App shell: navbar, page sections, footer
├── vercel.json             ← Vercel build config
├── package.json            ← Project metadata and npm scripts
├── vite.config.js          ← Vite configuration (if needed)
│
└── src/
    ├── main.js             ← Entry point: imports CSS + JS, wires event listeners
    │
    ├── css/
    │   ├── tokens.css      ← Design tokens (colors, fonts, spacing) — edit here to restyle
    │   ├── base.css        ← Reset, body, shared utilities (buttons, cards)
    │   ├── navbar.css      ← Navbar and user menu
    │   ├── footer.css      ← Footer
    │   ├── upload.css      ← Step 1: Upload page
    │   ├── analysis.css    ← Step 2: Analysis / report selection
    │   ├── results.css     ← Step 3: Results, chart, export
    │   └── settings.css    ← Settings page
    │
    └── js/
        ├── state.js        ← Shared app state (files, selectedReport, auth)
        ├── router.js       ← navigate() — shows/hides pages, syncs nav
        ├── files.js        ← Drag & drop, file parsing, file list rendering
        ├── reports.js      ← Report card selection logic
        ├── export.js       ← Export button handlers (PDF, HTML, Email)
        ├── user.js         ← User menu dropdown and auth stubs
        └── utils.js        ← Shared helpers (escHtml, etc.)
```

---

## Customizing the design

All colors, fonts, spacing, and radii live in one place: **`src/css/tokens.css`**.

```css
:root {
  --color-accent:       #2563EB;   /* Change to your brand color */
  --color-bg:           #F8F9FA;   /* Page background */
  --color-surface:      #FFFFFF;   /* Card/panel backgrounds */
  --font-ui:            'Inter', system-ui, sans-serif;
  --content-max:        900px;     /* Max content width */
  --radius-md:          8px;       /* Card/button corner radius */
}
```

Changing `--color-accent` updates every button, highlight, and active state simultaneously.

---

## Adding a logo

In `index.html`, find:
```html
<!-- Logo: replace this <svg> with <img src="/logo.png"> when ready -->
```

Replace the inner `<svg>` with:
```html
<img src="/logo.png" alt="DataLens" style="height:28px; object-fit:contain;" />
```

Put your logo file in the `public/` folder — Vite copies everything there to `dist/` automatically.

---

## Adding npm packages

With Vite you can install any npm package and import it directly:

```bash
# Example: add SheetJS for real file parsing
npm install xlsx

# Example: add Chart.js for real charts
npm install chart.js
```

Then import in the relevant module:
```js
// src/js/files.js
import * as XLSX from 'xlsx';
```

Vite handles the bundling automatically — no CDN links needed.

---

## What's implemented

| Feature | Status |
|---|---|
| Navbar with page routing | ✅ Done |
| Drag & drop file upload | ✅ Done |
| File list with row/sheet counts | ✅ Done (simulated — see `files.js`) |
| Analyze files summary bar | ✅ Done |
| Report type selection (3 options) | ✅ Done |
| Selected state + checkmark | ✅ Done |
| Generate report gating | ✅ Done |
| Results page with placeholders | ✅ Done |
| Export buttons (PDF/HTML/Email) | ✅ Buttons wired, logic not implemented |
| Settings page | ✅ Placeholder shown |
| User menu (sign in/out) | ✅ UI done, auth not wired |
| Footer (all pages) | ✅ Done |

---

## Next steps

| Task | Where to edit | Notes |
|---|---|---|
| Real file parsing | `src/js/files.js` → `parseFileMeta()` | Use [SheetJS](https://sheetjs.com/) |
| Real charts | `index.html` chart section | Use [Chart.js](https://www.chartjs.org/) or [Recharts](https://recharts.org/) |
| Authentication | `src/js/user.js` → `signIn()` / `signOut()` | [Clerk](https://clerk.com), [Auth0](https://auth0.com), or [Supabase Auth](https://supabase.com/docs/guides/auth) |
| PDF export | `src/js/export.js` → `handleExport()` | [jsPDF](https://github.com/parallax/jsPDF) |
| AI analysis API | Wire up `analyzeBtn` in `main.js` | POST files to your backend |
| Settings UI | `index.html` `#page-settings` + `src/css/settings.css` | Build out preference controls |
