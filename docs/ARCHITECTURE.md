# Architecture Reference

Technical walkthrough of the full pipeline, end to end. Not a tutorial — assumes
familiarity with the domain, points at real files/functions for detail.

Two processes: a **FastAPI backend** (`app/api.py`) and a **React + Vite frontend**
(`app/web/`). All state during a session lives server-side in memory; nothing is
required on disk for normal operation (see [Persistence](#persistence) below).

---

## 1. Upload — `POST /api/analyze-full`

`app/data/data_loader.py` (`DataLoader`) reads each uploaded CSV/Excel file.
- CSVs: read with encoding detection.
- Excel workbooks: one entry per worksheet (multi-sheet and multi-workbook both
  supported); optional `selections` narrows which sheets load.
- A single bad file (locked, corrupt) is recorded in `load_failures` rather than
  aborting the whole batch.
- `DataLoader.origins` tracks which uploaded file each resulting table came from,
  since a worksheet's display name (`"Orders (sales).xlsx"`) can't be parsed back
  to its source reliably.

## 2. Summarization

`app/data/summary_builder.py` (`SummaryGenerator`) profiles every loaded table:
column dtype, role (`primary_key` / `temporal` / `measure` / `categorical`), null
rate, cardinality, and — for numeric columns — mean/std/min/max for distribution
reasoning. It also detects cross-file join keys by comparing normalized column
names and value overlap (`MIN_JOIN_OVERLAP` threshold), producing a list of
candidate relationships passed into the prompt.

## 3. Prompt construction

`app/data/recommendation_requester.py` (`RecommendationRequester`) builds two
separate messages, not one:
- **System prompt** (`build_system_prompt`) — static analysis guidance + output
  JSON contract. Byte-identical every request, sent as a separate system message
  so providers can treat it as a cacheable prefix.
- **User prompt** (`build_request_prompt`) — the per-dataset payload: file
  profiles + detected relationships, rebuilt every call.

Recommendations are constrained to six named patterns (`REPORT_PATTERNS`): RANKING,
DISTRIBUTION, COMPOSITION, TREND, COMPARISON, OUTLIER — each with explicit data
requirements the LLM must match against the actual profiles, rather than letting it
invent arbitrary chart ideas. The output contract requires exactly 3 recommendations.

## 4. LLM call

`app/data/AI_Engine.py` (`send_prompt`) tries providers in order, falling through
on failure:
1. **Gemini** (if `GEMINI_API_KEY` set) — separate free-tier quota, tried first so
   spending it doesn't touch the shared Groq quota.
2. **Groq** primary model, then **Groq fallback model** (`GROQ_MODEL` /
   `GROQ_FALLBACK_MODEL`) — two attempts against Groq's per-model quota.
3. **Local Ollama** — last resort, no quota limit.

All three request schema-constrained JSON output where the provider supports it
(`RecommendationsResponse.model_json_schema()`), backstopped by `json_repair` for
whatever still comes back malformed. On validation failure (`get_validated_recommendations`),
the error is appended to the prompt and retried up to `LLM_MAX_RETRIES` times
(default `0` — no retry, to conserve quota).

## 5. Response validation

`app/data/response_validator.py` (`parse_and_validate`) checks the LLM's JSON against
the `app/data/models.py` Pydantic schema (`RecommendationsResponse`), then verifies
every proposed join actually resolves against the real loaded data — not just that
the JSON is well-formed. It also repairs near-miss filenames the LLM commonly mangles
(dropped parens, underscores for spaces, bare sheet names).

**What comes back:** `dataset_overview` (narrative) + exactly 3 `ReportRecommendation`s,
each with a `pattern_used`, `justification`, an ordered `required_operations` pipeline
(filter → derive → groupby → sort_limit → join), an `expected_output_schema`, and a
`plotly_config` (chart type + axes). These render as the recommendation cards on the
Analysis page.

## 6. Report generation — `POST /api/generate-report`

- `app/data/report_builder.py` executes the chosen recommendation's
  `required_operations` as one ordered pipeline (only the steps present run) into a
  report DataFrame.
- `app/data/chart_builder.py` builds the corresponding Plotly figure dict — no
  styling/theming here, that's applied client-side (`app/web/src/chartLayout.js`).
- `app/data/report_stats.py` computes real descriptive/trend/outlier statistics
  and a prose narrative **from the report's own rows** (not the LLM's pre-execution
  guesses), so every number on the dashboard traces back to actual data. Outlier
  detection uses the Iglewicz-Hoaglin modified z-score (median/MAD-based, resistant
  to the outlier it's trying to detect).

## 7. Export — `POST /api/export/{session_id}`

- `app/web/src/export.js` rasterizes each selected chart to PNG **in the browser**,
  using the same layout code the screen used (`chartLayout.js`) — so the export
  matches what the user approved, and no server-side Kaleido/Chromium dependency
  is needed.
- `app/data/export_builder.py` renders Jinja templates (`app/data/templates/`)
  through `xhtml2pdf` for PDF, or a standalone HTML shell with charts embedded as
  data URIs (opens offline, no external requests). Selecting 2+ reports produces
  one combined document with a comparison section instead of separate files.
- Number formatting is a Python port of the frontend's `format.js`, so exported
  figures match on-screen rounding exactly.

## 8. Email — `POST /api/export/{session_id}/email`

`app/data/emailer.py` sends the same export as an SMTP attachment (not inline —
Gmail/Outlook strip `<style>` and `data:` images from HTML bodies). Requires
`SMTP_HOST` + `SMTP_USER` + `SMTP_PASSWORD` in `.env`; `SMTP_FROM` optional
(defaults to `SMTP_USER`). Credentials are all-or-nothing — a relay with no auth
is supported (leave user/password both blank), but a half-filled pair is rejected
as a likely typo rather than silently sending unauthenticated. Left entirely blank,
the export panel disables the email row and states why; PDF/HTML download still work.

## 9. Usage / user metadata capture

`app/data/telemetry.py` writes to `usage.db` (stdlib `sqlite3`, 3 tables: `events`,
`files`, `reports`) via `track()`/`track_file()`/`track_report()` calls sprinkled
through `api.py`. Users are identified only by an anonymous UUID the frontend
generates and sends as `X-Client-Id` — no accounts. Filenames/column names are
hashed by default (`TELEMETRY_STORE_NAMES=1` stores plaintext, dev-only). Every
write swallows its own exceptions — telemetry can never fail a user request. Fully
independent of the `session_data/` mechanisms below; see `docs/DATABASE_SCHEMA.md`
for the table layout.

## Persistence

Three distinct, independently-toggled layers — none required for normal operation:

| Layer | What | Default | Purpose |
|---|---|---|---|
| `SESSIONS` dict (`app/api.py`) | In-memory: loaded DataFrames, profiles, recommendations | Always on, in-memory only | Backs every live request in a running session; lost on restart |
| `session_data/` debug artifacts | `raw_response.txt`, `cleaned_response.json`, `report_debug_<type>.txt` | **Off** (`SAVE_DEBUG_FILES=false`) | Lets `scripts/replay_report.py` rebuild a report with no LLM call |
| `session_data/` snapshots | Copied source files + manifest | **Off** (`SAVE_REPORT_HISTORY=false`) | Lets the token-gated `/api/admin/*` routes re-open a past session |
| `usage.db` | Aggregate counts/shapes, no cell values | Always on | Survives restarts; answers "how many people used it" |

Turning the two `session_data/` flags on/off has no effect on normal analyze/report/
export/email flows — those only ever read `SESSIONS`.
