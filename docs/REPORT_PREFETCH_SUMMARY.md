# Report Prefetching & Analysis Responsiveness — 2026-08-01

Reports now build in the background while the user reads the AI's recommendations, so
opening one is instant instead of a 2–3 second wait. Three latent bugs surfaced during
the work and were fixed alongside it.

**6 files changed, +320 / −74.**

**Verification:** `pytest` 139/139 pass · `vite build` succeeds · `app.api` imports
cleanly · full flow driven in real Chrome via Puppeteer, including the click-during-
prefetch race, mid-prefetch re-upload, and a forced report failure.

---

## Why this was needed

On the Analysis page each recommendation had a "Generate this report" button, and the
report was only built when it was clicked. Measured on the Enterprise E-Commerce set,
that was **2.55s / 0.29s / 3.42s** for reports A / B / C — paid again for every report
the user wanted to see.

Two facts made prefetching the obvious fix:

1. **Report generation makes no LLM call.** `generate_report_endpoint` is pure
   pandas → plotly-dict → numpy stats. Prefetching costs CPU only: no tokens, no quota,
   no money. The single LLM call happens once per upload in `/api/analyze-full` and is
   already finished by the time the Analysis page renders.
2. **The tab switch was blocking on the request.** `onGenerate` awaited `requestReport`
   before `setActiveTab('reports')`, so a click produced no visible feedback at all until
   the whole round trip completed.

---

# Part 1 — Report prefetching

## 1.1 A sequential background queue (`App.jsx`)

When a session's recommendations arrive, a `useEffect` walks the letters in rank order
and builds each report in turn.

**Sequential, not parallel.** The server is a single uvicorn worker and pandas holds the
GIL, so three concurrent builds finish no sooner than three serial ones while tripling
peak memory and competing with whatever the user actually clicks.

Both the queue and user clicks go through one function, `ensureReport(letter)`, which
returns the in-flight promise if a request for that letter is already running. A click on
a letter the queue hasn't reached yet starts immediately alongside the current background
build, so concurrency is bounded at two and a user's click is never stuck behind the
queue.

## 1.2 Per-letter UI state

`generatingType` (a single string) and `reportError` (a single string) both assumed one
report at a time. Four call sites gated on `!!generatingType`, so prefetching would have
**greyed out every button on the Analysis page the moment the user arrived** — the exact
opposite of the goal.

| Was | Now | Why |
|---|---|---|
| `generatingType: string \| null` | `generating: Set<letter>` | Each button reflects only its own report |
| `reportError: string` | `reportErrors: {letter: msg}` | A background failure on C must not warn someone reading A |

Also removed: the `opacity: 0.5` dimming on non-active recommendation cards, and the
blanket `disabled` on the Reports page A/B/C control — selecting a report that is still
building should reveal its building state, not refuse the click.

## 1.3 Instant tab switch

`onGenerate` no longer awaits. The Reports page's previously-unreachable empty slot now
serves as the loading state, with three branches: building, failed (with a retry), or
not yet generated.

**Files:** `App.jsx`, `Analysisdashboard.jsx`, `Reportsdashboard.jsx`

---

# Part 2 — Bugs found during the work

## 2.1 Session IDs collided (data leak between users)

`generate_session_id()` returned `datetime.now().strftime("%Y%m%d_%H%M%S")` — second
resolution — and `SESSIONS[session_id] = {...}` overwrites unconditionally. Two users
starting an upload in the same wall-clock second shared a key; the second silently
replaced the first. The first user's browser kept the id it was given and then built
**every report from the other user's data**, with no error.

Prefetching does not widen the collision window (that is set by upload timing) but it
does guarantee the corrupted result gets built and cached immediately rather than
possibly never.

```python
return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
```

Verified safe before changing: nothing parses the id as a datetime (no `strptime`
anywhere). Its three uses all tolerate a suffix — dict key, `session_data/<id>/`
directory, and the export filename component, which sanitises to `[A-Za-z0-9_]` and
truncates at 32 chars.

## 2.2 Stale reports could leak across uploads

The first guard written for this used the session id, and it had a hole: `onStart()` runs
at the *start* of an upload but `setSessionId` only lands ~7s later, when
`/api/analyze-full` returns. For that whole window `sessionRef` still held the **previous**
id, so a prefetch resolving inside it looked current and repopulated reports that
`startNewSession` had just cleared. The next session would then serve a report built from
the previous upload's data.

Replaced with a generation counter bumped synchronously when the upload begins. A test
that deliberately lands a stale report inside the window (two different datasets, leak
detectable by report title) confirms the fix: report C from upload 1 was in flight across
the entire second analyze call, and nothing leaked.

## 2.3 Unbounded report storage

`SESSIONS[sid]["reports"][letter]["data"]` stored every row of every report, uncapped —
and prefetching triples how many of those exist per session. Report C on the test set is
**150,000 rows**.

The only reader is the export appendix, whose `appendix_row_limit` is validated against a
ceiling, so rows past it are unreachable by any code path. Introduced `MAX_STORED_ROWS =
5000`, used both for the cap and for that validator so the two cannot drift. Report C now
stores 5,000 rows instead of 150,000 — a 30× reduction with no behaviour change.

## 2.4 The server never read its own cache

`generate_report_endpoint` already wrote each built report into `SESSIONS`, but never read
it back, so a duplicate request replayed the entire pipeline. It now returns the stored
copy minus its `data` key, which reproduces the original response exactly — including its
original `generated_at`, which should say when the report was built rather than when it
was last asked for.

**Files:** `api.py`

---

# Part 3 — Analysis responsiveness

## 3.1 `/api/analyze-full` was freezing the whole server

The endpoint was `async def` but did entirely blocking work — file writes, pandas
profiling, and a synchronous LLM SDK call — directly on the event loop. Measured: a
`/health` request issued 1s into an analysis took **6.46 seconds** to answer. Nothing else
could be served, including other users' report prefetching.

Changed to a sync `def`, so Starlette runs it in the threadpool. This is the same pattern
`/api/generate-report` already used. The only other edit required was
`await file.read()` → `file.file.read()`, the sole `await` in the function.

Confirmed: user A's three reports all completed *while* user B's analysis was still
running.

## 3.2 A spinner on the Analyze button

The 7s analysis previously showed only a changed button label. It now shows a spinning
ring inside the button next to "Analyzing…". Card height is identical before and during
(1100px), so nothing on the page moves.

Two details:

- The button used to grey to `#cbd5e1` while analysing, leaving white text on light grey.
  Busy is not the same as unavailable, so it keeps its blue while working. It is still
  `disabled` and now carries `aria-busy`.
- Under `prefers-reduced-motion` the spin is slowed to 2.4s rather than stopped — a frozen
  spinner reads as a hung app, which is the opposite of the point.

**Files:** `api.py`, `Uploaddashboard.jsx`, `dashboard.css`

---

# Measurements

All taken on the Enterprise E-Commerce Intelligence set (3 files, 12.15 MB, 177,000 rows).

**Report generation**

| | Before | After |
|---|---|---|
| Open report A / B / C | 2.55s / 0.29s / 3.42s each time | 0 requests, instant |
| All three ready | only when clicked | 6.1s after Analysis page opens |
| Duplicate request to server | full pipeline replay | 7ms (cache hit) |
| Rows stored for report C | 150,000 | 5,000 |

**`/api/analyze-full` phase breakdown** (why a progress bar was rejected — see below)

| Phase | Time | Share |
|---|---|---|
| Load files | 0.51s | 7% |
| Profile columns | 0.68s | 10% |
| Detect relationships | 1.00s | 15% |
| Build prompts | ~0.00s | 0% |
| **LLM call** | **4.58s** | **68%** |

**Server responsiveness during an analysis**

| | Before | After |
|---|---|---|
| `/health` latency | 6463 ms | 218 ms |
| Another user's report prefetch | fully blocked | all 3 completed mid-analysis |

---

# Considered and rejected

- **Parallel fan-out of all three reports.** GIL-bound pandas on one worker means no
  wall-clock gain, 3× peak memory, and contention with user clicks.
- **A batch `/api/generate-reports` endpoint.** One round trip, but all-or-nothing
  latency: nothing usable until the slowest report finishes.
- **Building the reports inside `/api/analyze-full`.** Would make the app's longest wait
  longer still.
- **A staged progress checklist for the analysis.** Built and working — four real phases
  polled from the server, no estimation — but removed as visually disruptive for a 7s
  wait. The store, endpoint, instrumentation and polling were all removed with it; the
  sync-handler change was kept because its value is independent of the UI. Recoverable
  from git history.
- **A percentage progress bar.** The LLM call is 68% of the wait, so any honest bar sits
  near a third of the way across for two thirds of the time and reads as stuck.
- **Timed client-side stage narration.** Cheapest way to *feel* fast, but with 68% of the
  time in a variable LLM call it would be most likely to lie exactly when the user most
  needs the truth.

---

# Known limits, accepted

- **A user who views only one report causes 3× the server CPU.** No LLM cost, so this is
  cheap; it is the deliberate trade for instant report switching.
- **`SESSIONS` still has no eviction.** The dominant term is `tables` — every uploaded
  sheet held as DataFrames, one set per upload, never dropped. Reports are a much smaller
  term and are now bounded (§2.3). A real fix (capped `OrderedDict` or TTL sweep) is ~10
  lines, but it turns "server gets slow" into "session vanishes mid-use and
  `/api/generate-report` 404s", and there is no UI anywhere for an expired session. Worth
  doing deliberately, not as a side effect.
- **Concurrency has a cost under load.** With two users overlapping, one analysis measured
  **11.0s instead of 7.4s**, because it now genuinely shares CPU rather than monopolising
  the process. Nobody is starved and total throughput is better, but a single user's worst
  case under load is slower than it was.

# Not done

- **Upload transfer progress.** Swapping `fetch` for `XMLHttpRequest` would give true
  byte-level progress for the 12 MB payload. Invisible on localhost, meaningful over a
  network. Out of scope here.
