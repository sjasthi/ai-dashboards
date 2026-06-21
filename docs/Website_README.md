# DataLens — AI Report Builder

A single-page web app for uploading spreadsheet data and generating AI-powered analysis reports.

---

## Deploying to Vercel

### Option A: Drag & drop (fastest)
1. Go to [vercel.com](https://vercel.com) and sign in (or create a free account).
2. On your dashboard, click **Add New → Project**.
3. Drag the project folder (containing `index.html` and `vercel.json`) into the upload area.
4. Click **Deploy**. You'll get a live URL in ~30 seconds.

### Option B: GitHub + Vercel (recommended for ongoing development)
1. Push this project to a GitHub repository.
2. Go to [vercel.com](https://vercel.com) → **Add New → Project** → **Import Git Repository**.
3. Select your repo. Vercel will auto-detect it as a static site.
4. Click **Deploy**. Every future push to `main` will auto-redeploy.

### Option C: Vercel CLI
```bash
npm install -g vercel
cd your-project-folder
vercel
```

---

## Project structure

```
index.html      ← Entire app (HTML + CSS + JS, single file)
vercel.json     ← Vercel routing config
README.md       ← This file
```

---

## Customizing the design

All visual tokens live at the top of `index.html` inside `:root { ... }`. Edit these to restyle everything:

```css
:root {
  /* Palette */
  --color-accent:       #2563EB;   /* Primary blue — change to brand color */
  --color-bg:           #F8F9FA;   /* Page background */
  --color-surface:      #FFFFFF;   /* Card backgrounds */
  --color-border:       #DEE2E6;   /* Borders and dividers */
  --color-text-primary: #111827;   /* Headings and body text */

  /* Typography */
  --font-ui:    'Inter', system-ui, sans-serif;
  --font-mono:  'JetBrains Mono', monospace;

  /* Layout */
  --content-max: 900px;            /* Max content width */
  --radius-md:   8px;              /* Button / card corner radius */
}
```

---

## Adding a logo

Find this comment in `index.html`:

```html
<!-- Logo placeholder — replace svg with <img src="logo.png"> when ready -->
```

Replace the `<svg>` inside `.navbar__logo-icon` with:

```html
<img src="logo.png" alt="Your Brand" style="width:28px; height:28px; object-fit:contain;" />
```

Or link to an external image:

```html
<img src="https://your-cdn.com/logo.svg" alt="Your Brand" style="height:28px;" />
```

---

## What's implemented

| Feature | Status |
|---|---|
| Navbar with page routing | ✅ Done |
| Drag & drop file upload | ✅ Done |
| File list with row/sheet counts | ✅ Done (simulated — see note below) |
| Analyze files summary bar | ✅ Done |
| Report type selection (3 options) | ✅ Done |
| Selected state + checkmark | ✅ Done |
| Generate report gating (must select first) | ✅ Done |
| Results page with placeholder content | ✅ Done |
| Export buttons (PDF / HTML / Email) | ✅ Buttons present, not wired |
| Settings page | ✅ Placeholder shown |
| User menu (sign in / sign out) | ✅ UI done, auth not wired |
| Footer (all pages) | ✅ Done |

---

## Next steps / roadmap

- **Real file parsing:** Replace `parseFileMeta()` in the `<script>` section with a real XLSX/CSV parser (e.g. [SheetJS](https://sheetjs.com/)).
- **Backend / AI analysis:** Wire the "Analyze files" button to your API endpoint.
- **Authentication:** Replace `fakeSignIn()` / `fakeSignOut()` with your auth provider (Clerk, Auth0, Supabase Auth, etc.).
- **Export:** Implement PDF generation (e.g. [jsPDF](https://github.com/parallax/jsPDF)) and email sending in `handleExport()`.
- **Settings page:** Build out the settings UI in `#page-settings`.
- **Real chart:** Replace the `chart-placeholder` bars with a chart library (Recharts, Chart.js, etc.).
