# Codebase Cleanup — 2026-07-28

Record of a dead-code and documentation cleanup pass. Tracked files went from **127 to
100**; 27 files deleted, 6 moved, 30 modified, ~1,700 lines removed.

**Verification run after every change:** `pytest` 84/84 pass · `ruff check --select
F401,F811,F841` clean · `vite build` succeeds (29 modules) · `app.api` imports cleanly ·
503 guard and debug-flag behaviour tested directly.

**Nothing in `session_data/` was touched**, as instructed.

---

## Why this was needed

The repo had been through two architectural pivots that were never cleaned up after:
PHP/LAMP → Python/FastAPI, and vanilla-JS → React. Roughly 53% of files under
`app/web/src/` were unreachable, two Python entry points were dead, and 3 of 4
"how to run this" documents gave commands that fail.

---

# Part 1 — Changes made

## 1.1 Files archived to `docs/archive/`

Moved with `git mv`, so history is preserved. `docs/archive/README.md` was added
explaining what each file was and why it was retired.

| File | Was | Reason |
|---|---|---|
| `docs/archive/Project_Planning.md` | `Project_Planning.md` | Its "Code Structure" section describes a **PHP/LAMP** stack (`index.php`, `dashboard.php`, `config/database.php`) that was never built. Names the Anthropic Claude API; the app uses Groq/Gemini/Ollama. Kept as the submitted FP3 record |
| `docs/archive/INTEGRATION_SUMMARY.md` | `INTEGRATION_SUMMARY.md` | All six endpoints it documents are fictional; the real API has three. Describes the AI integration as "pending" — that shipped weeks later |
| `docs/archive/prompt_handler_plan.md` | `docs/prompt_handler_plan.md` | Targets `prompt_builder.py` and `app/data_main.py`, neither of which exists. Its taxonomy is superseded — see §1.6 |
| `docs/archive/app.py` | `app/app.py` | Dead Flask prototype. Imports `flask`/`requests` (not project dependencies), renders `templates/index.html` (no such directory), posts to a non-existent Anthropic endpoint with a placeholder key |
| `docs/archive/main.py` | `app/main.py` | Dead tkinter harness. **Crashed when run:** `send_prompt()` returns a `dict`, but line 53 called `json.loads()` on it → `TypeError`, and the handler below only caught `json.JSONDecodeError` |

## 1.2 Files deleted

**Frontend — 18 files.** All proved unreachable by tracing from the real entry point
(`index.html:11` → `/src/main.jsx`) and confirmed by grepping the built bundle.

- `src/main.js`, `src/template.html` (zero references repo-wide)
- `src/js/` — all 8 modules: `api.js`, `export.js`, `files.js`, `reports.js`,
  `router.js`, `state.js`, `user.js`, `utils.js`
- `src/css/` — 7 unimported stylesheets: `base`, `navbar`, `footer`, `upload`,
  `analysis`, `results`, `settings`
- `public/icons.svg` — unreferenced sprite of Discord/Bluesky/X/GitHub icons from a
  starter template; unrelated to this app, but copied into `dist/` on every build

> `results.css` was the most valuable removal: it re-declared `.results-grid`,
> `.chart-card`, `.chart-card__title` and `.insight-card` — all four also defined in
> the live `dashboard.css` **with different values**. Harmless only because nothing
> imported it; re-adding that import would have broken the dashboard depending on
> import order.

**Docs — 2 files.**

- `docs/Website_README.md` — documented a single-file static app deployed to Vercel.
  `vercel.json` does not exist. Near-duplicate of `app/web/README.md` (same title, same
  `:root` block, same logo section) and the worse copy of the two.
- `docs/datasets.md` — documented 1 dataset; 8 exist, and the one documented was not
  among them. Its content was replaced by a complete table in `project_file_structure.md`.

**Bytecode — 7 files.** `app/__pycache__/` and `app/data/__pycache__/` were **tracked in
git** despite `.gitignore` listing `__pycache__/` — committed before the ignore rule
landed. Untracked with `git rm --cached` (files left on disk).

> Four of them were bytecode for modules that **no longer exist**:
> `csv_prompt_builder`, `data_explore`, `data_info`, and `data_main` — the last being
> the `app/data_main.py` that `README.MD` still told people to run.

## 1.3 Renamed

`requirements.txt` → **`docs/course_requirements.md`**

It was never a pip file — `pip install -r requirements.txt` fails on line 1. It is the
instructor's project brief (requirements, FP2–FP4 scope, the four upload scenarios).

**On standard practice:** a `requirements.txt` should contain only pip-installable
specifiers, one per line (`pandas==2.2.0`), so `pip install -r` works. The old file was
not that, which is why it became `docs/course_requirements.md` — that part still stands.

> **Superseded 2026-07-29.** The rest of this section originally argued that a
> `requirements.txt` was redundant because the project used Poetry. **Poetry has since
> been removed.** `pyproject.toml` and `poetry.lock` are deleted; dependencies now live
> in a real, exact-pinned `requirements.txt` (runtime) and `requirements-dev.txt`
> (`pytest`, `ruff`) at the repo root. Two things surfaced during the switch that this
> audit had missed:
>
> - **`pyproject.toml`'s constraints were never satisfied by any working environment.**
>   It declared `pandas >=3.0.3`, `numpy >=2.4.6` and `matplotlib >=3.11.0`, but `ai-env`
>   — the env that actually runs the app and the tests — has pandas 2.3.3, numpy 2.3.5
>   and matplotlib 3.10.0. `poetry install` did not build `ai-env`; pip did. The new pins
>   record what genuinely works.
> - **`python-multipart` was a missing runtime dependency.** `api.py` declares
>   `files: list[UploadFile] = File(...)`, which FastAPI cannot register without it. It
>   was installed in `ai-env` but absent from `pyproject.toml`, so the manifest could not
>   have stood up the upload endpoint on a clean clone. It is now listed explicitly.
>
> `kagglehub` was dropped rather than carried over — nothing in `app/`, `scripts/` or
> `tests/` imports it.

## 1.4 Behavioural code changes

**Mock fallback removed → HTTP 503** (`app/api.py`)

`/api/analyze-full` previously returned fabricated recommendations as
`status: "complete"` when the data modules failed to import — indistinguishable from a
real analysis. Now:

- `_mock_analyze_full_fallback()` deleted entirely
- A guard at the top of the endpoint raises **503** before any file I/O
- The import guard and `DATA_MODULES_AVAILABLE` flag remain, so the server still boots
  and `/health` still answers (chosen option)
- Pipeline failures still raise 502 as before
- Verified: `/health` → 200, `/api/analyze-full` with modules unavailable → 503

**Debug output env-gated, default off**

- `AI_Engine.SAVE_DEBUG_FILES` was hardcoded `True`; now reads `SAVE_DEBUG_FILES` from
  the environment, defaulting to off
- `report_builder._save_debug_output()` was **ungated entirely** — it wrote
  `session_data/<id>/report_debug_<type>.txt` on every report including failures. Now
  behind the same flag
- The self-labelled `# ==== TEMPORARY OUTPUT ====` block (a `final_df.head(15)` dump to
  stdout on every successful report) was **deleted**
- `SAVE_DEBUG_FILES` documented in `.env.example`, along with `LLM_MAX_RETRIES` which
  was undocumented

> **A bug I introduced and then fixed.** My first version read the flag at import time
> in `report_builder`. But `report_builder` is imported *during* `AI_Engine`'s imports,
> which is **before** `AI_Engine` calls `load_dotenv()` — so setting `SAVE_DEBUG_FILES`
> in `.env` (the documented way) enabled AI_Engine's writer but not report_builder's. I
> verified this empirically, then changed it to a lazy per-call read
> (`_debug_files_enabled()`). Both writers now agree whether the flag comes from `.env`
> or the shell.

**Sign-in button removed** (`App.jsx`)

The navbar button had no `onClick` and no auth behind it. Removed, along with the
`.app-nav__signin` CSS rule and the two comments in `dashboard.css` describing the nav
as having "three groups" (now two).

## 1.5 In-file dead code removed

**Python**

| What | Where |
|---|---|
| Unused imports: `JSONResponse`, `json`, `Optional` | `app/api.py` |
| Unused import: `Union` | `app/data/models.py` |
| Unused imports: `List`, `asdict`, `json` | `app/data/session_manager.py` |
| Unused import: `asdict` | `app/data/summary_builder.py` |
| `DataLoader.clear()` — zero callers | `app/data/data_loader.py` |
| `SessionManager.save_profiles()` / `save_prompt()` — only caller was the archived `main.py` | `app/data/session_manager.py` |
| Redundant alias `_humanize_column = humanize_column`; two call sites renamed | `app/data/chart_builder.py` |

**Frontend**

| What | Where |
|---|---|
| `fileSize()` — never imported | `src/format.js` |
| `ACCENT` constant + the dead `export { ACCENT, INK_MUTED }` | `src/chartLayout.js` |
| Unused polymorphic `as` prop — no `<Card as=...>` anywhere | `src/components/ui/Card.jsx` |
| Dead prop `files={files}` — `AnalysisDashboard` never destructured it | `src/App.jsx` |
| Dead 3-hop `sessionId` chain into `<ReportHeader>`, which doesn't accept it | `src/App.jsx`, `Reportsdashboard.jsx` |
| Unused default `import React` in 10 files (kept in `Reportsdashboard.jsx`, which uses `<React.Fragment>`) | across `src/` |

**Stale docstrings corrected**

- `api.py:5` — documented a `[UNUSED]` endpoint tag with no such endpoint; rewritten
- `api.py` — two docstrings claimed the mock fallback triggers on pipeline exceptions,
  which stopped being true when that path was changed to raise 502
- `report_stats.py` — claimed `pattern` "selects which optional block leads the
  narrative". It does not; block selection is driven by `_is_ordered_axis` and the
  labels check. `pattern` is pass-through metadata only

## 1.6 Documentation rewritten

| File | What changed |
|---|---|
| `README.MD` | Removed `poetry run python app/data_main.py` (file doesn't exist). Dependency table rebuilt from `pyproject.toml` — it had listed matplotlib/kagglehub while omitting the entire web stack. Added the web app, `.env` configuration, and a documentation index |
| `RUNNING_APP.md` | Python 3.9+ → 3.14+. Deleted three curl examples for endpoints that don't exist, and the "Next Steps: Integrating Your Code" section for work finished weeks ago. Replaced `pip install fastapi uvicorn` with `poetry install`. **Added the missing `.env` setup** — the biggest gap. Documented the real three endpoints with their 502/503 behaviour |
| `app/web/README.md` | Full rewrite. It described the vanilla-JS app as current and never mentioned React, while sitting where a frontend dev would find it first. Now documents the real React structure, state flow, and current gaps |
| `project_file_structure.md` | Regenerated from the tracked file list. The old version listed 4 modules that never existed (`file_handler.py`, `prompt_builder.py`, `result_analyzer.py`, `graph_builder.py`) plus `vercel.json`, and omitted 9 of 10 real `app/data/` modules. Now includes a data-flow diagram and the dataset table absorbed from `docs/datasets.md` |
| `docs/REACT_MIGRATION_SUMMARY.md` | Kept — the most accurate doc in the repo. Corrected one error (`tokens.css` was listed as orphaned but had been re-imported) and added a resolution note to its "Known Gap" section |
| `docs/archive/README.md` | **New.** Explains what each archived file was and why it was retired |

`weekly_deliverables_plan.md` was left untouched, as instructed.

> **Partly superseded 2026-07-29.** The Poetry and Python-version details in the two rows
> above describe this pass only. Both files were revised again when Poetry was removed:
> the install steps are now `python -m venv` + `pip install -r requirements.txt`, and the
> stated Python version is **3.13+**. See §1.3.

---

# Part 2 — Notes and open items

## 2.1 Export (FP7) — deliberately not changed

No changes were made to export functionality, as instructed. Recording what was found:

`src/js/export.js` was deleted along with the rest of the dead vanilla-JS tree, after
inspection showed it was **not a working implementation**:

- 38 lines, one function, whose entire behaviour was: call
  `GET /api/export/{sessionId}?format=` and then `alert()` the returned `download_url`
- **That endpoint has never existed.** `app/api.py` has exactly three routes. Wired up,
  it would 404 on the first click
- It contained **no export logic** — no PDF, HTML, CSV or email generation. The "email"
  branch literally alerted *"Report would be emailed"*
- FP7 asked for **CSV** export, which it did not cover at all

**Current state:** the export buttons in `Reportsdashboard.jsx` render `disabled` with a
"not yet available" chip. Unchanged.

**Recorded so it isn't lost** — the API contract the old client assumed:

```
GET /api/export/{session_id}?format=json|html|pdf
  -> 200 { session_id, format, status, download_url }
```

Implementing FP7 means writing the backend endpoint (the actual work) plus a small
client in `src/api.js`. The deleted file is in git history if you want it:
`git show HEAD:app/web/src/js/export.js`

## 2.2 `scripts/replay_report.py` — kept, needs review

Kept per your decision. **Flagging it for review**, since git can prove it was
maintained but not that it was used.

- Three commits, most recent 2026-07-26 — one day before the newest commit in the repo
- Tracks the *current* `generate_report(...)` and `resolve_plotly_axes(...)` signatures,
  including the newer `tables=` parameter
- Faithfully mirrors `api.py`'s loader flow, and reverse-engineers `DataLoader._add_excel`'s
  worksheet naming so multi-sheet replays resolve
- Its value: rebuilding a report **without spending LLM quota** — a live constraint
  (`LLM_MAX_RETRIES` defaults to `0` specifically to conserve it)

**Action needed:** it now requires `SAVE_DEBUG_FILES=true` in `.env` to have any input,
since it reads `session_data/<id>/cleaned_response.json`. Existing sessions on disk are
unaffected. It takes **no CLI arguments** — session, recommendation and file locations
are all chosen via GUI dialogs.

It also imports the private `_find_file_path` from `report_builder` — brittle coupling,
worth tidying if the script is kept long-term.

## 2.3 Two corrections to my earlier audit

**The virtualenv recommendation was backwards.** I told you `.venv` was current and
`ai-env` abandoned, based on Python versions. Running the tests disproved it:

| | Python | Packages | Has fastapi/plotly/groq? |
|---|---|---|---|
| `ai-env` | 3.13.9 | 265 | **Yes — this is the working env** |
| `.venv` | 3.14.3 | 171 | **No** — only pandas, pytest, ruff |

`.venv` cannot run the app or the tests. `ai-env` is what `.vscode/*` points at, and it
is correct to do so. **Recommendation reversed: keep `ai-env`.**

But this leaves a real inconsistency: `pyproject.toml` declares
`requires-python = ">=3.14"` while the only working environment is **3.13.9**. One of
the two is wrong. Either relax the constraint to `>=3.13`, or rebuild the env on 3.14
and reinstall (`poetry install`). Worth resolving before anyone else clones this.

> **Resolved 2026-07-29.** Settled in favour of the environment that demonstrably works.
> `pyproject.toml` is gone, and `README.MD` and `RUNNING_APP.md` now both state
> **Python 3.13+**. See the note in §1.3.

**`.vscode/` is not tracked.** I reported it as committed despite being gitignored.
`git ls-files .vscode` returns nothing — it is local-only. The stale configs in it
(both pointing at `app/data/AI_Engine.py`, which has no `__main__` block and does
nothing when run) are a personal annoyance, not a repo problem.

## 2.4 Items that still need to be addressed

Found and verified, not acted on — each needs a decision or is a larger change.

**Duplicated logic (4 pairs).** Left alone because deduplicating changes behaviour risk
for no functional gain, and the test coverage isn't there to catch a regression.

| A | B | What |
|---|---|---|
| `chart_builder.py:135 _fd_xbins()` | `report_builder.py:562 _freedman_diaconis_edges()` | Line-for-line identical Freedman-Diaconis binning; differ only in return shape |
| `data_loader.py:46` | `report_builder.py:827` | Same CSV encoding-fallback reader, same 5-encoding list. Its own docstring says *"(mirrors DataLoader)"* |
| `chart_builder.py:211` | `report_stats.py:195 _as_datetime()` | Same datetime probe; the second admits it in a comment |
| `summary_builder.py:195,228` | `response_validator.py:14,126` | Same join-overlap ratio, and the `0.05` threshold is a bare literal in one and a named constant in the other — **hand-synced** |

**Live-vs-live frontend duplication.** `Uploaddashboard.jsx:3` hardcodes the API base
URL and byte-copies the error-extraction logic from `src/api.js:14-17` instead of
importing it. Report-letter↔index mapping is encoded four different ways
(`String.fromCharCode(65+i)` appears twice in `Reportsdashboard.jsx`).

**`REPORT_TYPE_LETTERS` includes `'D'`** (`src/api.js:5`) but the model always returns
three recommendations. Unclear whether the backend accepts `report_type="D"`.

**Legacy schema handlers, probably dead.** `report_builder.py:684 _execute_sort()` and
the `filters` dict branch are unreachable from any validated response —
`models.Operation.operation_type` is a `Literal` that excludes `"sort"`, and the model
has no `filters` field. They survive only for `replay_report.py`'s unvalidated path.
Same for `models.py`'s two legacy validators. **Left in place** because confirming they
are dead requires inspecting `session_data/`, which was off-limits.

**`Settingsdashboard.jsx` is a non-functional mock.** Three controls held in local state
and a `handleSave` that only fires `alert("Settings saved successfully!")`. Nothing
persists. It currently tells the user something happened when nothing did — worth either
implementing or disabling.

**Inert classNames.** `Uploaddashboard.jsx:58` uses `className="upload-page"` and
`Analysisdashboard.jsx:103` uses `"analysis-page"`, but no such CSS rules exist —
whereas `.reports-page` does. So Upload and Analysis silently get no max-width container
while Reports does. A real (if subtle) visual inconsistency.

~~**`pyproject.toml` metadata.** `authors` is still the scaffold placeholder
`{name = "Your Name", email = "you@example.com"}`. Also `readme = "README.md"` while the
file is `README.MD` — fine on Windows, **breaks `poetry build` on Linux/CI**.~~
**Closed 2026-07-29** — `pyproject.toml` was deleted with the move to `requirements.txt`,
so both problems went with it. (MIT copyright stays `sjasthi` per your decision.)

**No `logging` module anywhere.** ~15 `print(f"[API] ...")` calls serve as the logging
mechanism. Fine for a capstone; worth knowing.

**Test coverage is the real constraint on further cleanup.** `report_builder.py` is 910
lines with **zero tests** — and the API tests explicitly stub it out
(`test_generate_report_api.py:87`). Also untested: `recommendation_requester.py`,
`summary_builder.py`, `response_validator.py`, `models.py`, `data_loader.py`,
`session_manager.py`, and `/api/analyze-full`. There is no `conftest.py` and no pytest
config, so imports resolve only when pytest runs from the repo root.

---

# Part 3 — Not reviewed (for a later pass)

Carried over from the audit plan. **Nothing in this section has been assessed.**

**Config & dependencies** (you scoped these out):
`package-lock.json` at the repo root (98 bytes, **no
matching `package.json`**) · `app/web/package.json` · `app/web/package-lock.json` ·
`app/web/vite.config.js` (read *only* to trace the entry point) · `.env` ·
`.env.example` (edited to add two variables, not reviewed) · `.gitignore` ·
`app/web/.gitignore` (verbatim Vite boilerplate, appears correct) ·
`.vscode/{launch,settings,tasks}.json` (flagged above, not audited) ·
`.claude/settings.json` · `.claude/settings.local.json`

**Generated / local-only artifacts** (scoped out entirely):
`session_data/` — **untouched, as instructed** · `app/web/dist/` (grepped only, to prove
what ships; rebuilt during verification) · `.venv/` · `ai-env/` · `.pytest_cache/` ·
`node_modules/`

**Data files** (excluded — no xlsx/xls/csv):
All of `datasets/` — 8 folders. Non-tabular members not assessed:
`datasets/lego/downloads_schema.png`, `datasets/Enterprise E-Commerce Intelligence/source.txt`.

**Partially reviewed:**
`LICENSE` (license type and copyright holder checked; nothing else) ·
`docs/website mockup.png` and `app/web/public/favicon.svg` (reference-checked only,
contents not assessed) · `weekly_deliverables_plan.md` (read during the audit, left
untouched per your instruction — note FP7 CSV export is undelivered and 8 of 9 rows have
elapsed).

---

# Appendix — How to verify

```bash
# Backend tests — expect 84 passed
./ai-env/Scripts/python.exe -m pytest -q

# Lint for unused imports/variables — expect "All checks passed!"
./.venv/Scripts/python.exe -m ruff check --select F401,F811,F841 app/ scripts/ tests/

# Frontend build — fails loudly on any broken import
npm --prefix app/web run build

# Full app, two terminals
uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
npm --prefix app/web run dev
```

Then upload a file from `datasets/excel tests/`, generate a report, and confirm the
chart, stat tiles and table render. Check the nav still looks right after the sign-in
button removal — brand left, tabs right, and tabs wrapping to their own row below 640px.
