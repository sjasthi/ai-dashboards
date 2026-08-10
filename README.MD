# AI-Dashboards

## Description

An AI-powered dashboard system that ingests one or more CSV or Excel files,
analyzes them, and produces visualizations, reports, summaries, and recommendations.

The app is two processes: a **FastAPI backend** (`app/api.py`) that runs the data
pipeline and talks to the LLM, and a **React + Vite frontend** (`app/web/`) that
renders the dashboard. Both must be running to use the app.

---

# For Users

## Requirements

- Python 3.13+
- Node.js 18+ (for the frontend)
- An LLM API key — see [Configuration](#configuration)

## Key Dependencies

| Package                 | Purpose                                     |
|-------------------------|---------------------------------------------|
| fastapi / uvicorn       | Backend API and server                      |
| python-multipart        | Multipart form parsing — required for file upload |
| pandas / numpy          | Data loading, profiling and transformation  |
| plotly                  | Chart figure construction                   |
| groq / google-genai / ollama | LLM providers (hosted, then local fallback) |
| json_repair             | Recovering malformed JSON from LLM output   |
| openpyxl / xlrd         | Excel (.xlsx / .xls) reading                |
| jinja2 / xhtml2pdf      | Report export — HTML templating and PDF rendering |
| pillow                  | Validates the chart images the browser posts for an export |
| python-dotenv           | Loads `.env` configuration                  |
| react / vite            | Frontend (declared in `app/web/package.json`) |

Dev-only (`requirements-dev.txt`): `pytest`, `ruff`, `datasette`.

## Setup

1. Clone the repo
```bash
git clone https://github.com/sjasthi/ai-dashboards.git
cd ai-dashboards
```

2. Create and activate a virtual environment
```bash
python -m venv ai-env
ai-env\Scripts\activate          # Windows
source ai-env/bin/activate       # macOS / Linux
```

3. Install Python dependencies
```bash
pip install -r requirements.txt
```

Use `requirements-dev.txt` instead if you also want the test and lint tools.

4. Install frontend dependencies
```bash
npm --prefix app/web ci
```

## Configuration

Copy `.env.example` to `.env` and add at least one LLM key. Without this the
analysis endpoint will fail — there is no offline mock mode.

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `AI_BACKEND` | `groq` or `ollama` (default) for a fully local run |
| `GROQ_API_KEY` | Free key at [console.groq.com/keys](https://console.groq.com/keys) |
| `GEMINI_API_KEY` | Optional, tried first. Free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `LLM_MAX_RETRIES` | Validation-failure retries. Default `0` — each retry costs quota |

Emailing a report additionally needs `SMTP_HOST`, `SMTP_USER` and `SMTP_PASSWORD`
(Gmail requires an [app password](https://myaccount.google.com/apppasswords), not your
account password). Leave them blank to skip it — the export panel then disables the
email row and says why, and PDF/HTML downloads still work.

## Running the App

Two terminals, both with the virtual environment activated:

```bash
# Terminal 1 — backend on http://127.0.0.1:8000
uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

```bash
# Terminal 2 — frontend on http://localhost:5173
npm --prefix app/web run dev
```

Then open http://localhost:5173.

**Expected backend output:** `[API] Data modules loaded successfully` followed by
`Uvicorn running on http://127.0.0.1:8000`. If you instead see `[API] Data modules
not available: ...`, the install is incomplete — every analysis request will 503
until it's fixed.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `/api/analyze-full` returns 503 | The backend couldn't import its data modules — check the startup console for the missing package, confirm the virtual environment is activated, `pip install -r requirements.txt` |
| `/api/analyze-full` returns 502 | The LLM call or validation failed — check the server console; common causes are a missing API key or an exhausted free-tier quota |
| "Connection refused" in the browser | The backend isn't running, or isn't on port 8000 |
| "CORS error" in the browser console | CORS is configured in `app/api.py` for ports 5173 and 3000 — add a new origin there if you changed the Vite port |
| Files won't upload | Check the browser console (F12); files must be `.csv`, `.xls` or `.xlsx` |

## Documentation

Everything a standard user needs is in this README. See [For Developers](#for-developers)
below for API details, debugging tools, and the rest of the documentation set.

---

# For Developers

## API Reference

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/api/stats` | Aggregate usage counters for the home page (public, unauthenticated) |
| POST | `/api/events` | Batch of browser-reported usage events |
| POST | `/api/inspect` | Describe uploaded files' worksheets without analyzing them |
| POST | `/api/analyze-full` | Profile uploaded files and get LLM report recommendations |
| POST | `/api/generate-report` | Build one recommendation into a report (chart + stats + rows) |
| GET | `/api/export/{session_id}/status` | What a session can currently export |
| POST | `/api/export/{session_id}` | Download selected reports as PDF or HTML |
| POST | `/api/export/{session_id}/email` | Email selected reports as an attachment |
| GET | `/api/admin/sessions` | List saved sessions (token-gated) |
| GET | `/api/admin/sessions/{session_id}` | One saved session's detail (token-gated) |
| GET | `/api/admin/sessions/{session_id}/reports/{letter}` | Replay one report from a saved session (token-gated) |
| DELETE | `/api/admin/sessions/{session_id}` | Delete one saved session (token-gated) |
| POST | `/api/admin/sessions/prune` | Bulk-delete saved sessions by criteria, dry-run by default (token-gated) |
| GET | `/api/admin/stats` | Full event log / breakdowns (token-gated) |

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/api/analyze-full \
  -F "files=@datasets/game sales/vgchartz-2024.csv"

curl -X POST http://localhost:8000/api/generate-report \
  -H "Content-Type: application/json" \
  -d '{"session_id": "20260728_120530", "report_type": "A"}'
```

Interactive docs (Swagger UI): `http://localhost:8000/docs`.

## Session data and debug flags

Both off by default — normal operation never touches disk `session_data/`, only
the in-memory `SESSIONS` dict:

| Variable | Effect |
|---|---|
| `SAVE_DEBUG_FILES` | Writes each LLM exchange (`raw_response.txt`, `cleaned_response.json`) and report-build traces to `session_data/<id>/` |
| `SAVE_REPORT_HISTORY` | Snapshots uploaded source files + a manifest to `session_data/<id>/`, enabling the `/api/admin/*` routes to replay it later |
| `ADMIN_TOKEN` | Required for any `/api/admin/*` route to respond (404 otherwise) |

Replay a report with no LLM call: set `SAVE_DEBUG_FILES=true`, run one analysis,
then `python scripts/replay_report.py` (GUI dialogs pick the session/recommendation).
See `docs/DEV_REPORT_BROWSER.md` for the full admin-route based browser.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Adding Dependencies

Install it, look up the resolved version, then add the pinned line to
`requirements.txt` (or `requirements-dev.txt` for a tool that only developers need):

```bash
pip install package-name
pip show package-name | grep Version
```

Every entry is pinned with `==` so a fresh clone installs the exact set the app has been
tested against. Do not add an unpinned name.

## Documentation

| Doc | What it covers |
|---|---|
| [project_file_structure.md](project_file_structure.md) | Current repository layout |
| [app/web/README.md](app/web/README.md) | Frontend architecture and styling |
| [docs/EXPORT_FEATURE.md](docs/EXPORT_FEATURE.md) | Report export (PDF / HTML / email): architecture, renderer constraints, document design |
| [docs/EXPORT_LIVE_SYNC.md](docs/EXPORT_LIVE_SYNC.md) | Keeping the export in sync with the live report — the live/export drift checklist and xhtml2pdf gotchas |
| [docs/USAGE_DB_BROWSING.md](docs/USAGE_DB_BROWSING.md) | Reading `usage.db` in a browser (Datasette) + useful SQL |
| [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) | `usage.db` schema and ER diagram |
| [docs/DEV_REPORT_BROWSER.md](docs/DEV_REPORT_BROWSER.md) | Re-opening a past analysis without an LLM call |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Test plan of record for the upload/loading pipeline |
| [course_requirements.md](course_requirements.md) | The original course brief (historical) |
| [docs/archive/](docs/archive/) | Superseded planning docs and retired code |
