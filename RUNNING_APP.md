# Running Frontend + Backend

This document explains how to run the full AI Dashboard application with both the frontend (Vite) and backend (FastAPI) servers.

## Prerequisites

Make sure you have:
- Node.js v18+ installed
- Python 3.9+ (with `ai-env` virtual environment activated)
- Dependencies installed

## Installation

### 1. Install Python dependencies

The backend requires FastAPI and Uvicorn. These are already listed in `pyproject.toml`.

Install them:
```bash
# Using poetry (if you have it installed)
poetry install

# OR using pip
pip install fastapi uvicorn
```

### 2. Install frontend dependencies

```bash
npm --prefix app/web ci
```

## Running the Application

You'll need **two terminal windows** — one for the frontend, one for the backend.

### Terminal 1: Run the Backend (FastAPI)

```bash
# Activate the Python virtual environment (if not already active)
./ai-env/Scripts/Activate.ps1    # PowerShell
# or
source ai-env/bin/activate       # macOS/Linux

# Run the FastAPI server
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

**Expected output:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

The API will be available at `http://localhost:8000` and includes:
- **Health check:** `http://localhost:8000/health`
- **API docs:** `http://localhost:8000/docs` (Swagger UI)

### Terminal 2: Run the Frontend (Vite)

```bash
npm --prefix app/web run dev
```

**Expected output:**
```
VITE v8.0.12  ready in 123 ms

➜  Local:   http://localhost:5173/
```

Open your browser to `http://localhost:5173`

---

## How They Work Together

1. **Frontend** (localhost:5173): 
   - Upload files via drag-and-drop
   - Select report type
   - Click "Upload Files" → sends to backend
   - Click "Generate Report" → calls backend API

2. **Backend** (localhost:8000):
   - Receives file uploads
   - Stores mock analysis results
   - Returns session IDs and analysis data
   - Currently uses mock data — ready to integrate your `AI_Engine.py`

3. **Communication:**
   - Frontend makes HTTP requests to backend using the `api.js` client
   - Backend returns JSON responses
   - All requests go through CORS-enabled endpoints

---

## API Endpoints

When the backend is running, you can test endpoints directly:

### Upload Files
```bash
curl -X POST http://localhost:8000/api/upload -F "files=@data.csv"
```

### Get Analysis Results
```bash
curl http://localhost:8000/api/results/20260704_120530
```

### Export Report
```bash
curl http://localhost:8000/api/export/20260704_120530?format=json
```

### View API Documentation
Open `http://localhost:8000/docs` in your browser for the interactive Swagger UI.

---

## Next Steps: Integrating Your Code

### To integrate `AI_Engine.py`:

1. Open `app/api.py`
2. Find the `analyze_data()` function
3. Replace the mock responses with real calls to your backend:

```python
from app.data.AI_Engine import AIEngine
engine = AIEngine()

@app.post("/api/analyze")
def analyze_data(session_id: str, report_type: str):
    files = SESSIONS[session_id]['files']
    result = engine.process(files, report_type)  # ← Call your real code
    return result
```

---

## Troubleshooting

### "Connection refused" error in browser
- Make sure the backend is running: Check Terminal 1
- Make sure it's on port 8000

### "CORS error" in browser console
- The FastAPI server needs CORS enabled (already configured in `app/api.py`)
- Try refreshing the page

### Files won't upload
- Check browser console (F12) for errors
- Make sure files are `.csv`, `.xls`, or `.xlsx`

### Virtual environment not activating
```bash
# PowerShell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
./ai-env/Scripts/Activate.ps1
```

---

## Performance Notes

- **Hot reload:** Both frontend and backend support hot reload during development
  - Frontend: Edit CSS/JS files → changes appear instantly
  - Backend: Edit Python files → automatically restarts server
  
- **Production build:** When ready to deploy:
  ```bash
  npm --prefix app/web run build
  # Creates optimized app/web/dist/ folder
  ```

---

Still have questions? Check `app/api.py` and `app/web/src/js/api.js` for more details.
