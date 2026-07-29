# Running Frontend + Backend

This document explains how to run the full AI Dashboard application with both the
frontend (Vite) and backend (FastAPI) servers.

## Prerequisites

- Node.js v18+
- Python 3.13+
- An LLM API key — see [Configuration](#configuration) below

## Installation

### 1. Install Python dependencies

```bash
python -m venv ai-env
ai-env\Scripts\activate          # Windows
source ai-env/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

Add `-r requirements-dev.txt` instead if you also want `pytest` and `ruff`.

Always install from the requirements file — do not hand-pick packages with
`pip install fastapi uvicorn`. The backend also needs `python-multipart`, `pandas`,
`numpy`, `plotly`, `groq`, `google-genai`, `ollama`, `json_repair`, `python-dotenv`,
`openpyxl` and `xlrd`. Missing any of them makes `app/api.py` fail its import check,
and `/api/analyze-full` will then return **503**.

The versions in `requirements.txt` are pinned with `==` to the set the app has been
tested against; installing looser versions is untested.

### 2. Install frontend dependencies

```bash
npm --prefix app/web ci
```

## Configuration

The backend needs an LLM provider before it can analyze anything. Copy the template
and fill in at least one key:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `AI_BACKEND` | `groq` (default in the template) or `ollama` for a fully local run |
| `GROQ_API_KEY` | Free key at https://console.groq.com/keys |
| `GEMINI_API_KEY` | Optional, tried first. Free key at https://aistudio.google.com/apikey |
| `LLM_MAX_RETRIES` | Validation-failure retries. Default `0` — each retry costs quota |
| `SAVE_DEBUG_FILES` | `true` writes per-run artifacts to `session_data/`. Off by default |

There is **no offline mock mode**. Without a working provider the analysis call fails
with a real error rather than returning placeholder data.

## Running the Application

You'll need **two terminal windows** — one for the backend, one for the frontend.

### Terminal 1: Backend (FastAPI)

```bash
uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

**Expected output:**
```
[API] Data modules loaded successfully
INFO:     Uvicorn running on http://127.0.0.1:8000
```

If you see `[API] Data modules not available: ...` instead, the install is incomplete —
fix that before continuing, or every analysis request will 503.

The API is then available at `http://localhost:8000`:
- **Health check:** `http://localhost:8000/health`
- **API docs:** `http://localhost:8000/docs` (Swagger UI)

### Terminal 2: Frontend (Vite)

```bash
npm --prefix app/web run dev
```

**Expected output:**
```
VITE v8.0.16  ready in 123 ms

➜  Local:   http://localhost:5173/
```

Open your browser to `http://localhost:5173`.

---

## How They Work Together

1. **Frontend** (localhost:5173):
   - Pick one or more CSV/Excel files in the Upload tab
   - The app POSTs them to `/api/analyze-full` and shows the returned recommendations
   - Choosing a recommendation POSTs `/api/generate-report` and renders the result

2. **Backend** (localhost:8000):
   - Profiles the uploaded files, builds a prompt, calls the LLM, validates the response
   - Executes the recommended operations into a report, chart and statistics
   - Keeps the loaded DataFrames in memory per session so the report step can reach them

3. **Communication:**
   - The frontend calls the backend through `app/web/src/api.js`
   - All requests go through CORS-enabled endpoints (configured in `app/api.py`)

---

## API Endpoints

The API has exactly three routes.

### Health check
```bash
curl http://localhost:8000/health
```

### Analyze uploaded files
```bash
curl -X POST http://localhost:8000/api/analyze-full \
  -F "files=@datasets/game sales/vgchartz-2024.csv"
```
Returns `session_id`, per-file metadata, the prompt, and the LLM recommendations.

**Errors:** `503` if the data modules failed to import, `502` if the pipeline or LLM
call fails, `400` for missing/empty files.

### Generate a report
```bash
curl -X POST http://localhost:8000/api/generate-report \
  -H "Content-Type: application/json" \
  -d '{"session_id": "20260728_120530", "report_type": "A"}'
```
`report_type` is a letter selecting which recommendation to build (`A`, `B`, `C`).

### Interactive docs
Open `http://localhost:8000/docs` for Swagger UI.

---

## Troubleshooting

### `/api/analyze-full` returns 503
The backend could not import its data modules. Check Terminal 1 for the
`[API] Data modules not available:` line — it names the missing package. Check that your
virtual environment is activated, then run `pip install -r requirements.txt`.

### `/api/analyze-full` returns 502
The pipeline ran but the LLM call or validation failed. The server console has the
underlying cause. Common causes: no API key in `.env`, or the provider's free-tier
quota is exhausted.

### "Connection refused" in the browser
The backend isn't running, or isn't on port 8000. Check Terminal 1.

### "CORS error" in the browser console
CORS is already configured in `app/api.py` for ports 5173 and 3000. If you changed the
Vite port, add the new origin to the `allow_origins` list.

### Files won't upload
Check the browser console (F12). Files must be `.csv`, `.xls` or `.xlsx`.

---

## Development Notes

- **Hot reload:** both servers reload on save — Vite instantly, uvicorn via `--reload`.

- **Replaying a report without spending LLM quota:** set `SAVE_DEBUG_FILES=true` in
  `.env`, run an analysis once, then rebuild the report from the saved response:
  ```bash
  python scripts/replay_report.py
  ```
  It takes no arguments — session, recommendation and (if needed) source-file
  location are all chosen through GUI dialogs. The chart opens in your browser.
  Useful when iterating on `report_builder` / `chart_builder` output.

- **Production build:**
  ```bash
  npm --prefix app/web run build   # writes app/web/dist/
  ```

---

Still have questions? Read `app/api.py` and `app/web/src/api.js`.
