# React Frontend Migration Summary

## What Was Done

### 1. Activated the React entry point (previously dead code)

`app/web/index.html` was still loading the old vanilla-JS `main.js` with static HTML sections, even though `main.jsx`/`App.jsx`/the `*dashboard.jsx` components already existed. Switched the script tag to `/src/main.jsx`, added a `<div id="root">`, and stripped the old static markup (React renders its own nav/tabs/content now).

### 2. Installed missing dependencies

`react` and `react-dom` weren't in `package.json` at all — the JSX files couldn't actually run. Added them, plus `@vitejs/plugin-react` (had to pin to `^6.0.3` since the project's Vite is v8 and the plugin's older majors only support Vite ≤7), and created `vite.config.js` to register the plugin.

### 3. Fixed a crash in `Analysisdashboard.jsx`

It called `useState` with no `import { useState } from 'react'` — threw a `ReferenceError` on render, and with no error boundary in `App.jsx`, that blanked the entire app once you hit the Analysis tab. Added the import.

### 4. (Separate, backend-side) Fixed the empty-report bug

Not a React wiring issue, but hit while testing the flow end-to-end: `report_builder.py`'s regex-extract step crashed on AI-generated patterns lacking a capture group, silently returning 0 rows to the Reports page. Made it retry by auto-wrapping the pattern.

**Net effect:** Upload → Analysis → Reports now actually flows through the mounted React app instead of the orphaned old JS.

---

## New Dependencies

Three new packages were added to `app/web/package.json`:

**Dependencies**
- `react` `^18.3.1`
- `react-dom` `^18.3.1`

**DevDependencies**
- `@vitejs/plugin-react` `^6.0.3` (pinned to v6 instead of the default v4 latest, since the project's Vite is v8 and plugin-react v4/v5 only support Vite ≤7)

`npm install` pulled in 6 packages total (those 3 plus their transitive deps), on top of the `vite` dependency that was already there.

---

## Orphaned Files

The following files under `app/web/src/` are no longer imported by `main.jsx`, `App.jsx`, or any of the `*dashboard.jsx` components. None have been deleted — they're just no longer reachable from `index.html`.

**Entry point**
- `src/main.js` — superseded by `main.jsx`

**Old vanilla-JS modules (`src/js/`)**
- `router.js` — page switching, replaced by `App.jsx`'s `activeTab` state
- `files.js` — dropzone/file-list logic, replaced by `Uploaddashboard.jsx`
- `reports.js` — report selection/generation, replaced by `Analysisdashboard.jsx`/`Reportsdashboard.jsx`
- `export.js` — export button wiring (React `ExportCard` in `Reportsdashboard.jsx` has no click handlers yet — this logic isn't ported)
- `user.js` — user-menu dropdown (no React equivalent exists yet — the React navbar's "Sign in" button in `App.jsx` is inert)
- `state.js`, `utils.js` — support modules for the above
- `api.js` — fetch wrapper; the React components (`Uploaddashboard.jsx`, `Analysisdashboard.jsx`) reimplemented their own `fetch` calls inline instead of reusing this

**Old stylesheets (`src/css/`)**
- `tokens.css`, `base.css`, `navbar.css`, `footer.css`, `upload.css`, `analysis.css`, `results.css`, `settings.css` — the React components use inline `style={{}}` objects instead

**Other**
- `src/template.html` — not referenced anywhere, in either the old or new setup

### Known Gap

`export.js` and `user.js` had real functionality (export buttons, sign-in menu) that hasn't been reimplemented in React yet, so those two features are currently non-functional in the new UI — not just dead code.
