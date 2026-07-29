# Archive

Retired files kept for the record. **Nothing here is current** — every file below
describes an architecture or plan the project has since moved away from, or is code
that no longer runs. Do not follow instructions in these documents.

Archived 2026-07-28 during a codebase cleanup pass. For what the project actually
looks like now, see [project_file_structure.md](../../project_file_structure.md).

## Documents

| File | What it was | Why it was retired |
|---|---|---|
| `Project_Planning.md` | The FP3 planning deliverable (scope, FP3–FP10 roadmap, team roles) | Its "Code Structure" section describes a **PHP/LAMP** stack (`index.php`, `dashboard.php`, `config/database.php`) that was never built — the project went Python/FastAPI instead. It also names the Anthropic Claude API, while the app uses Groq/Gemini/Ollama. Kept as the submitted FP3 record. |
| `INTEGRATION_SUMMARY.md` | A handoff note written when the frontend↔backend wiring was first stubbed out (2026-07-04) | All six endpoints it documents are fictional; the real API has three. It describes the AI integration as "pending" — that work shipped weeks later. |
| `prompt_handler_plan.md` | The 5-phase implementation plan for LLM-driven report recommendation | The strategy was implemented, but under different names: it targets `prompt_builder.py` and `app/data_main.py`, neither of which exists. Its 5-type taxonomy (Statistical / Time-Series / Comparative / Segmentation / Data Quality) was **superseded** by the 6 patterns in `RecommendationRequester.REPORT_PATTERNS` (RANKING / DISTRIBUTION / COMPOSITION / TREND / COMPARISON / OUTLIER), which is the live source of truth. Its "Potential Future Additions" list is still a reasonable roadmap. |

## Code

| File | What it was | Why it was retired |
|---|---|---|
| `app.py` | A Flask prototype (originally `app/app.py`) | Superseded by `app/api.py`. It could not run: it imports `flask` and `requests` (neither is a project dependency), renders `templates/index.html` (no such directory), and posts to a non-existent Anthropic endpoint with a placeholder API key. |
| `main.py` | A tkinter file-picker harness (originally `app/main.py`) | Superseded by `POST /api/analyze-full`, which runs the same pipeline. It also crashed when run: `AI_Engine.send_prompt()` returns a `dict`, but line 53 calls `json.loads()` on it, raising `TypeError` — and the handler below only catches `json.JSONDecodeError`. It predates response validation, calling `send_prompt` where the live endpoint calls `get_validated_recommendations`. |

Note: `main.py` used `SessionManager.save_profiles()` and `save_prompt()`, which were
its only callers. Both methods were removed from `app/data/session_manager.py` in the
same cleanup, so this file would need them restored to run even after its other bugs
are fixed.
