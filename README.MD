# AI-Dashboards

## Description

An AI-powered dashboard system that ingests one or more CSV or Excel files,
analyzes them, and produces visualizations, reports, summaries, and recommendations.

The app is two processes: a **FastAPI backend** (`app/api.py`) that runs the data
pipeline and talks to the LLM, and a **React + Vite frontend** (`app/web/`) that
renders the dashboard. Both must be running to use the app.

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

Dev-only (`requirements-dev.txt`): `pytest`, `ruff`.

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

Emailing a report additionally needs `SMTP_HOST`, `SMTP_USER` and `SMTP_PASSWORD`
(Gmail requires an [app password](https://myaccount.google.com/apppasswords), not your
account password). Leave them blank to skip it — the export panel then disables the
email row and says why, and PDF/HTML downloads still work.

Free keys: [Groq](https://console.groq.com/keys) or
[Google AI Studio](https://aistudio.google.com/apikey). Alternatively set
`AI_BACKEND=ollama` to use a local Ollama install with no key at all.

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
<!-- Dev view: http://localhost:5173/?dev=1 -->
Then open http://localhost:5173. See [RUNNING_APP.md](RUNNING_APP.md) for
troubleshooting and the API reference.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Documentation

| Doc | What it covers |
|---|---|
| [RUNNING_APP.md](RUNNING_APP.md) | Detailed run instructions, API endpoints, troubleshooting |
| [project_file_structure.md](project_file_structure.md) | Current repository layout |
| [app/web/README.md](app/web/README.md) | Frontend architecture and styling |
| [docs/EXPORT_FEATURE.md](docs/EXPORT_FEATURE.md) | Report export (PDF / HTML / email): architecture, renderer constraints, document design |
| [docs/REACT_MIGRATION_SUMMARY.md](docs/REACT_MIGRATION_SUMMARY.md) | Record of the vanilla-JS → React migration |
| [docs/course_requirements.md](docs/course_requirements.md) | The original project brief |
| [docs/archive/](docs/archive/) | Superseded planning docs and retired code |

## Adding Dependencies

Install it, look up the resolved version, then add the pinned line to
`requirements.txt` (or `requirements-dev.txt` for a tool that only developers need):

```bash
pip install package-name
pip show package-name | grep Version
```

Every entry is pinned with `==` so a fresh clone installs the exact set the app has been
tested against. Do not add an unpinned name.
