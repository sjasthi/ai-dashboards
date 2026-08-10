# The developer report browser

Re-opens a past analysis on the real Reports page — chart, KPIs, insights, data
table — without re-uploading the spreadsheet and without spending an LLM call.

Developer-only: no nav entry in the normal app, no user-facing endpoint, and the
whole thing is stripped from production builds (see [How it works](#how-it-works)).
It exists because `SESSIONS` is an in-memory dict — reports vanish on refresh, and
this is the only way to inspect what a past run produced.

## 1. Turn it on

Two settings in `.env`, both off by default (documented in `.env.example`):

```ini
SAVE_REPORT_HISTORY=true          # save each analysis so it can be re-opened
ADMIN_TOKEN=pick-something-long   # shared secret for the /api/admin/* routes
```

Restart the API after editing `.env`. Nothing analyzed before you turned this on
was saved — run a fresh analysis to get something to look at.

## 2. Open the browser

```bash
cd app/web && npm run dev
```

Go to `http://localhost:5173/?dev=1` (use `localhost`, not `127.0.0.1` — the Vite
dev server binds `::1` only). A **Dev** tab appears next to Settings.

## 3. Enter the token

The first screen asks for `ADMIN_TOKEN`. It's stored in `sessionStorage` (cleared
on browser close, or via **Forget token**).

| Message | Meaning |
|---|---|
| "ADMIN_TOKEN is not set on the server…" | Set it in `.env` and restart |
| "That token was rejected." | Wrong token — check `.env` for typos/whitespace |
| "SAVE_REPORT_HISTORY is off…" | Routes work, but nothing new is being saved |

## 4. Browse and replay

Each row is one saved session: id, source files, size, save date. Click **Generate
report** to open it on the real Reports tab — A/B/C switcher, compare view, table
and export panel all work normally; other letters rebuild on click.

- Rebuilding never calls the model — it's just the pandas pipeline, usually under
  a second.
- **JSON** rebuilds report A and saves it to a file without opening it.
- **Open a .json report** loads one of those files back (for handing a report to
  someone without server access).
- **Delete** removes the session's directory (manifest + retained workbooks).

A row whose source files were deleted shows "Source files deleted" and is disabled
but still listed. A replay sits over the Reports tab without disturbing your own
session — **Back to my session** returns you to it exactly as you left it.

## 5. Pruning

Nothing deletes on a timer — retention is manual.

| Control | Selects |
|---|---|
| Delete selected (N) | exactly the rows you ticked |
| older than [N] days | everything saved at least N days ago |
| keep newest [N] | everything except the N most recent |

"Only sessions whose source files are already gone" narrows any of the three to
dead manifests. Every action previews first (what would be deleted, size, count) —
nothing is touched until you confirm.

## 6. Where the data lives

```
session_data/<session_id>/
  source/<the files exactly as uploaded>
  manifest.json
```

Gitignored. Delete a directory to forget that session. **This retains real
spreadsheets with nothing expiring on its own** — fine on a dev machine, why the
feature is off by default.

## 7. Turning it off

Set `SAVE_REPORT_HISTORY=false` and restart — nothing further is saved, and
existing `session_data/` stays until deleted by hand. Remove `ADMIN_TOKEN` too and
the routes stop answering entirely (404).

## 8. Calling the API directly

All routes require `X-Admin-Token`.

```bash
TOK=your-admin-token

curl -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions
curl -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions/20260803_113839_c38cd9
curl -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions/20260803_113839_c38cd9/reports/A
curl -X DELETE -H "X-Admin-Token: $TOK" localhost:8000/api/admin/sessions/20260803_113839_c38cd9

# Prune previews unless dry_run is set false; exactly one criterion per call:
curl -X POST -H "X-Admin-Token: $TOK" -H "Content-Type: application/json" \
  -d '{"older_than_days": 30, "dry_run": false}' \
  localhost:8000/api/admin/sessions/prune
```

| Code | Meaning |
|---|---|
| 404 on every admin route | `ADMIN_TOKEN` not configured (deliberate — see below) |
| 401 | `ADMIN_TOKEN` set, wrong/missing token given |
| 404 on one session | Never saved, or deleted |
| 410 on one session | Manifest exists but `source/` was deleted |
| 422 on one report | The saved recommendation no longer executes against its own data — a real finding, not a viewer bug |

## How it works

The uploaded spreadsheet doesn't survive its own request — `/api/analyze-full`
loads it into a temp dir that's deleted when the request ends. This feature
persists the **source files** (not a copy of the rendered report) and
**regenerates** the report from them on replay, through the exact same
`_build_report()` function the live endpoint uses — so a replay can never show
something the app doesn't actually produce. Regeneration is cheap because the
pipeline is deterministic (no wall-clock reads on the report path) and table keys
are just file basenames, so a saved file replays correctly as long as its filename
is preserved.

Reasoning: persisting the source (not a pre-rendered report) keeps the retained
artifact auditable (`session_data/<id>/source/` is a file you can open), and
regenerating means a stale UI bug surfaces as a visible replay failure, not silently
served stale output.

`admin_token()` gates every `/api/admin/*` route via `X-Admin-Token`, compared with
`secrets.compare_digest`. An unset `ADMIN_TOKEN` answers 404 rather than 401, so a
deployment that never configured this doesn't advertise the routes exist (though
they're still visible in `/openapi.json`/`/docs` — disable those separately if that
matters for a deployment).

The frontend module (`app/web/src/dev/`) is gated by `import.meta.env.DEV` at build
time, so Vite eliminates it entirely from `npm run build` output — "developer only"
is a build-time fact, not a convention. The admin fetch helper lives in its own
`adminApi.js` (not the shared `api.js`) so `X-Admin-Token` is never attached to a
normal user request.

A replay renders on the real Reports page via its own `replay` state slot in
`App.jsx` (kept separate from live `sessionId`/`recommendations`/`reports` so it
can never trigger the live prefetch effect), with an amber banner marking it as a
replay and `onExitReplay` returning to the live session unchanged.

Retention is fully manual by design (no automatic expiry, no size cap) — the
listing shows size/age per session, and `older_than_days` lets you dry-run a
retention policy before committing to one.

## Verifying it yourself

| # | Check | How |
|---|---|---|
| 1 | Test suite | `python -m pytest -q` |
| 2 | Flag off saves nothing | run an analysis with `SAVE_REPORT_HISTORY` unset → no `session_data/<id>/source/` |
| 3 | Replay equivalence | generate A live, restart, replay A → `rows`/`stats`/`chart` identical (only `generated_at` differs) |
| 4 | The gate | wrong token → 401; `ADMIN_TOKEN` unset → 404 |
| 5 | No LLM call | replay with the Network tab open → no `POST /api/generate-report`, even clicking B/C |
| 6 | Graceful expiry | `rm -rf session_data/<id>/source` → lists as unreplayable, opening gives 410 |
| 7 | Not in production | `npm run build`, grep `dist/` for `DevReportBrowser`/`adminApi`/`X-Admin-Token` → nothing |
| 8 | Prune previews honestly | dry-run a criterion, note `freed_bytes`, commit it → same number returned |
| 9 | Prune can't run away | `POST /api/admin/sessions/prune -d '{}'` → 400, nothing deleted |

## Files

| Path | Role |
|---|---|
| [app/data/session_store.py](../app/data/session_store.py) | Writes/reads the snapshot; no HTTP, no report logic |
| [app/api.py](../app/api.py) | `save_snapshot`, `_build_report`, `rehydrate_session`, `admin_token`, the `/api/admin/*` routes |
| [app/web/src/dev/DevReportBrowser.jsx](../app/web/src/dev/DevReportBrowser.jsx) | Session list, pruning, hand-off to App |
| [app/web/src/dev/adminApi.js](../app/web/src/dev/adminApi.js) | Admin fetch helper and token storage |
| [app/web/src/App.jsx](../app/web/src/App.jsx) | The `replay` state slot and `requestReplayReport` |
| [app/web/src/components/Reportsdashboard.jsx](../app/web/src/components/Reportsdashboard.jsx) | Draws every report, live or replayed; owns the amber replay banner |
| [tests/test_session_store.py](../tests/test_session_store.py) | The store and the `/api/analyze-full` hook |
| [tests/test_admin_api.py](../tests/test_admin_api.py) | The gate, replay equivalence, degradation |
