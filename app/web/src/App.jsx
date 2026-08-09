import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import HomeDashboard from './components/Homedashboard';
import UploadDashboard from './components/Uploaddashboard';
import AnalysisDashboard from './components/Analysisdashboard';
import ReportsDashboard from './components/Reportsdashboard';
import SettingsDashboard from './components/Settingsdashboard';
import NavStats from './components/ui/NavStats';
import { REPORT_TYPE_LETTERS, fetchStats, generateReport, sendEvents } from './api';
import { isNewVisit } from './clientId';

/**
 * The developer session browser, behind two independent gates.
 *
 * `import.meta.env.DEV` is substituted with the literal `false` at build time, so
 * the ternary collapses to `null` and the dynamic import inside it is eliminated -
 * the module and everything it pulls in (the admin API client included) never
 * reaches a production bundle. That makes "developer only" a build-time fact rather
 * than a convention someone could quietly break.
 *
 * `?dev=1` is the second gate: it keeps the tab out of the way during ordinary
 * local work, where an extra nav entry would just be noise. A query parameter
 * rather than a route because this app has no router.
 */
const DevReportBrowser = import.meta.env.DEV
  ? lazy(() => import('./dev/DevReportBrowser'))
  : null;

const DEV_TAB_ENABLED = !!DevReportBrowser
  && new URLSearchParams(window.location.search).has('dev');

export default function App() {
  // Home is the landing tab: arriving on Upload gave no answer to "what is this".
  const [activeTab, setActiveTab] = useState('home');

  // The usage counters shown in the nav. Held here rather than in the page that
  // used to own them because the nav outlives every tab: read once per page load,
  // not once per tab click.
  const [stats, setStats] = useState(null);

  // Raw File objects picked in the Upload tab
  const [files, setFiles] = useState([]);

  // What /api/inspect reported about each file, which sheets are ticked, and which
  // rows have their checkboxes showing - all keyed by fileKey. Held here rather
  // than in the Upload page for the same reason as `reports`: that page unmounts on
  // every tab switch, and rebuilding this would mean re-uploading each file and
  // would throw away the sheet choices the user had made.
  const [inspections, setInspections] = useState({});
  const [selections, setSelections] = useState({});
  const [expanded, setExpanded] = useState(() => new Set());

  // Read once, on arrival. The visit is reported before the counters are read, so
  // the person arriving is included in the number they are about to see rather
  // than showing up on someone else's next page load.
  useEffect(() => {
    let cancelled = false;

    const arrival = isNewVisit()
      ? sendEvents([{ event: 'visit_started' }])
      : Promise.resolve(null);

    arrival
      .then(() => fetchStats())
      .then(data => { if (!cancelled) setStats(data); })
      // A missing counter is not worth an error message in the nav bar.
      .catch(() => { if (!cancelled) setStats(null); });

    return () => { cancelled = true; };
  }, []);

  // Populated once /api/analyze-full succeeds
  const [sessionId, setSessionId] = useState(null);
  const [recommendations, setRecommendations] = useState(null); // { recommendations: [...] }
  const [fileProfiles, setFileProfiles] = useState(null);

  // Generated reports, cached by report type ("A" | "B" | "C"). Held here rather
  // than in the Reports page so switching between them - or comparing all three -
  // doesn't re-POST a report the server has already built.
  const [reports, setReports] = useState({});
  const [activeReportType, setActiveReportType] = useState('A');

  // Which letters are building *right now*, and which letters failed. Both are
  // per-letter rather than single values because reports are prefetched in the
  // background: one shared "is generating" flag would disable every button on the
  // Analysis page the moment the user arrived, and one shared error string would
  // show a failure for report C to someone who is still reading report A.
  const [generating, setGenerating] = useState(() => new Set());
  const [reportErrors, setReportErrors] = useState({});

  /**
   * A saved session being viewed on the Reports page instead of the live one.
   *
   * `null` normally. Held in its own slot rather than by assigning sessionId /
   * recommendations / reports, for two reasons. The prefetch effect below watches
   * those two, so writing a saved session into them would start building every
   * remaining letter over HTTP for a session someone only wanted to look at. And
   * the user's own session survives untouched, so leaving a replay restores it
   * with its report cache intact rather than making them upload again.
   *
   * Shape: { sessionId, recommendations, reports, generating, errors, fetchReport }.
   * `fetchReport` is injected by the developer module rather than imported here:
   * everything under src/dev/ is dropped from a production build, and importing
   * the admin client from App would pull it back in.
   */
  const [replay, setReplay] = useState(null);
  const replayRef = useRef(null);
  replayRef.current = replay;

  // Mirrors of state that the prefetch queue reads. The queue is one long-lived
  // async loop, so it can't depend on `reports`/`sessionId` as state without
  // restarting itself on every change - it reads these instead.
  const reportsRef = useRef(reports);
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  // Bumped the moment a new upload starts. The session id can't play this role:
  // it isn't known until /api/analyze-full returns, which is ~7s after the upload
  // begins, and for that whole window sessionRef still holds the *previous* id. A
  // prefetch from the old session landing in that window would look current, and
  // would repopulate reports that startNewSession had just cleared - so the next
  // session would serve a report built from the previous upload's data.
  const generation = useRef(0);

  // letter -> { generation, promise }, for requests that are in the air. Tagged so
  // a re-upload can't be handed back a promise belonging to the previous one.
  const inFlight = useRef(new Map());

  /**
   * Build `letter` if it isn't built already, and return a promise for it.
   *
   * The single entry point for both the background queue and user clicks, so the
   * two can never duplicate a request: whoever asks second gets the promise the
   * first one started. Never rejects - failures land in `reportErrors` - so the
   * queue can await it without a try/catch around every step.
   */
  const ensureReport = useCallback((letter, sid) => {
    const session = sid || sessionRef.current;
    if (!session) return Promise.resolve(null);
    if (reportsRef.current[letter]) return Promise.resolve(reportsRef.current[letter]);

    const gen = generation.current;
    const pending = inFlight.current.get(letter);
    if (pending && pending.generation === gen) return pending.promise;

    // Kept so the handlers below can tell "still the entry I created" from "a newer
    // entry for a newer upload" - a stale completion must not clear live state.
    const entry = { generation: gen, promise: null };

    setGenerating((prev) => new Set(prev).add(letter));

    entry.promise = generateReport(session, letter)
      .then((data) => {
        // A report built from an upload the user has already replaced is worse than
        // no report, so drop it rather than merging it.
        if (generation.current !== gen) return null;
        reportsRef.current = { ...reportsRef.current, [letter]: data };
        setReports(reportsRef.current);
        setReportErrors((prev) => {
          if (!(letter in prev)) return prev;
          const next = { ...prev };
          delete next[letter];
          return next;
        });
        return data;
      })
      .catch((err) => {
        if (generation.current !== gen) return null;
        setReportErrors((prev) => ({
          ...prev,
          [letter]: err.message || 'Something went wrong while generating the report.',
        }));
        return null;
      })
      .finally(() => {
        if (inFlight.current.get(letter) === entry) inFlight.current.delete(letter);
        // Only the live upload may clear the spinner: otherwise a stale request
        // finishing would un-disable a button for a report still being built.
        if (generation.current !== gen) return;
        setGenerating((prev) => {
          if (!prev.has(letter)) return prev;
          const next = new Set(prev);
          next.delete(letter);
          return next;
        });
      });

    inFlight.current.set(letter, entry);
    return entry.promise;
  }, []);

  const requestReport = useCallback((letter) => {
    setActiveReportType(letter);
    return ensureReport(letter);
  }, [ensureReport]);

  /**
   * Show a saved session on the Reports page.
   *
   * Called by the developer browser once it has both the session's recommendations
   * and its first rebuilt report, so the page renders filled in rather than
   * flashing an empty state. Switching the tab here rather than there keeps the
   * developer module unaware of how this app does navigation.
   */
  const loadReplay = useCallback(({ sessionId: sid, recommendations: recs, report, fetchReport }) => {
    const letter = report?.report_type || 'A';
    setReplay({
      sessionId: sid,
      recommendations: recs,
      reports: report ? { [letter]: report } : {},
      generating: new Set(),
      errors: {},
      fetchReport,
    });
    setActiveReportType(letter);
    setActiveTab('reports');
  }, []);

  /**
   * Build one letter of the open replay, if it isn't built or building already.
   *
   * Split out from `requestReplayReport` so the background prefetch below can call
   * it without also stealing the active tab out from under whichever letter the
   * developer is actually looking at.
   *
   * Deliberately without ensureReport's generation counter and in-flight map: those
   * exist because a background queue and a user click race for the same letter, and
   * because a new upload can invalidate a request already in the air. Neither
   * happens here - every request starts from a click or this one prefetch effect,
   * and a replay is never replaced underneath itself. The one guard that is still
   * needed is against a response arriving after the developer left the replay or
   * opened a different session, which is what the session id check does.
   */
  const ensureReplayReport = useCallback((letter) => {
    const active = replayRef.current;
    if (!active || active.reports[letter] || active.generating.has(letter)) return;

    const sid = active.sessionId;
    const stillOpen = () => replayRef.current?.sessionId === sid;
    const patch = (fn) => setReplay((prev) => (prev?.sessionId === sid ? fn(prev) : prev));

    patch((prev) => ({ ...prev, generating: new Set(prev.generating).add(letter) }));

    active.fetchReport(sid, letter)
      .then((data) => {
        if (!stillOpen()) return;
        patch((prev) => {
          const errors = { ...prev.errors };
          delete errors[letter];
          return { ...prev, reports: { ...prev.reports, [letter]: data }, errors };
        });
      })
      .catch((err) => {
        if (!stillOpen()) return;
        patch((prev) => ({
          ...prev,
          errors: {
            ...prev.errors,
            [letter]: err.message || `Report ${letter} could not be rebuilt.`,
          },
        }));
      })
      .finally(() => {
        patch((prev) => {
          if (!prev.generating.has(letter)) return prev;
          const next = new Set(prev.generating);
          next.delete(letter);
          return { ...prev, generating: next };
        });
      });
  }, []);

  /** The Reports page's letter switcher while a replay is open. */
  const requestReplayReport = useCallback((letter) => {
    setActiveReportType(letter);
    ensureReplayReport(letter);
  }, [ensureReplayReport]);

  /**
   * Build every other letter of an opened replay in the background, same as the
   * live prefetch queue below does for `sessionId`/`recommendations`. The developer
   * browser only rebuilds the one letter it opens with (openReport in
   * DevReportBrowser.jsx costs one request, not three, to list-then-peek quickly),
   * so without this A/B/C only finished generating one at a time, on click, same as
   * the live page used to.
   */
  useEffect(() => {
    const sid = replay?.sessionId;
    const recList = replay?.recommendations?.recommendations;
    if (!sid || !recList?.length) return;

    recList
      .map((_, i) => REPORT_TYPE_LETTERS[i])
      .filter(Boolean)
      .forEach((letter) => ensureReplayReport(letter));
    // Re-runs only when a *different* session is opened, not on every report
    // that streams in - ensureReplayReport reads live state off replayRef itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replay?.sessionId, ensureReplayReport]);

  const startNewSession = useCallback(() => {
    // A new upload invalidates every report built from the previous one. Requests
    // already in the air aren't cancellable, but bumping the generation makes
    // ensureReport drop them on arrival; clearing the map stops them being handed
    // back to a caller asking about the new upload.
    generation.current += 1;
    reportsRef.current = {};
    inFlight.current.clear();
    // Someone uploading data wants to see *their* data. Leaving a replay in place
    // would show the Reports page a saved session while Analysis described the new
    // upload - two tabs disagreeing about which session is current.
    setReplay(null);
    setReports({});
    setActiveReportType('A');
    setGenerating(new Set());
    setReportErrors({});
  }, []);

  /**
   * Build every recommended report in the background as soon as the analysis lands.
   *
   * Reports are pure pandas server-side - no LLM call, so no quota cost - and the
   * user spends a while reading the recommendations before picking one. Building
   * them during that time means the report is usually already there on click.
   *
   * All three fire at once so A/B/C are ready together rather than trickling in
   * rank order.
   */
  useEffect(() => {
    const recList = recommendations?.recommendations;
    if (!sessionId || !recList?.length) return;

    const letters = recList
      .map((_, i) => REPORT_TYPE_LETTERS[i])
      .filter(Boolean);

    // ensureReport resolves rather than rejects, and returns the in-flight promise
    // if the user got to this letter first, so firing all three never double-requests.
    letters.forEach((letter) => { ensureReport(letter, sessionId); });
  }, [sessionId, recommendations, ensureReport]);

  const getTabStyle = (tab) => ({
    padding: '10px 4px',
    cursor: 'pointer',
    color: activeTab === tab ? '#2563eb' : '#64748b',
    fontWeight: activeTab === tab ? 600 : 500,
    fontSize: '15px',
    borderBottom: activeTab === tab ? '2px solid #2563eb' : '2px solid transparent',
    transition: 'color 0.15s ease, border-color 0.15s ease'
  });

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#f8fafc', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* NAVBAR */}
      {/* Padding, gaps and wrapping live in dashboard.css (.app-nav) rather than here:
          inline styles beat media queries, so anything that has to change at a
          breakpoint can't be set on the element. */}
      <nav className="app-nav" style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        background: 'white',
        borderBottom: '1px solid #e2e8f0'
      }}>
        <div className="app-nav__brand" style={{ display: 'flex', alignItems: 'center' }}>
          <div style={{
            width: '34px',
            height: '34px',
            borderRadius: '8px',
            background: '#2563eb',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="12" width="4" height="9" rx="1" fill="white" />
              <rect x="10" y="7" width="4" height="14" rx="1" fill="white" />
              <rect x="17" y="3" width="4" height="18" rx="1" fill="white" />
            </svg>
          </div>
          <span style={{ fontWeight: 700, fontSize: '1.25rem', color: '#1e293b' }}>
            AI-Dashboard
          </span>
        </div>

        <div className="app-nav__tabs" style={{ display: 'flex' }}>
          {['home', 'upload', 'analysis', 'reports', 'settings',
            ...(DEV_TAB_ENABLED ? ['dev'] : [])].map(tab => (
            <div key={tab} style={getTabStyle(tab)} onClick={() => setActiveTab(tab)}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </div>
          ))}
        </div>

        <NavStats stats={stats} />
      </nav>

      <main className="app-main">
        {activeTab === 'home' && (
          <HomeDashboard onStart={() => setActiveTab('upload')} />
        )}
        {activeTab === 'upload' && (
          <UploadDashboard
            files={files}
            setFiles={setFiles}
            inspections={inspections}
            setInspections={setInspections}
            selections={selections}
            setSelections={setSelections}
            expanded={expanded}
            setExpanded={setExpanded}
            setSessionId={setSessionId}
            setRecommendations={setRecommendations}
            setFileProfiles={setFileProfiles}
            onStart={startNewSession}
            onDone={() => setActiveTab('analysis')}
          />
        )}
        {activeTab === 'analysis' && (
          <AnalysisDashboard
            sessionId={sessionId}
            recommendations={recommendations}
            reports={reports}
            generating={generating}
            errors={reportErrors}
            activeReportType={activeReportType}
            onGenerate={(index) => {
              // Not awaited: waiting here meant the click did nothing visible until
              // the report was fully built. The Reports page shows its own building
              // state, so switch first and let it fill in.
              setActiveTab('reports');
              requestReport(REPORT_TYPE_LETTERS[index] || 'A');
            }}
          />
        )}
        {activeTab === 'reports' && (
          /* One page, two possible sources. A replay substitutes its own props
             wholesale rather than being merged into the live ones, so the page
             renders a saved session through exactly the code path it renders a
             live one - and leaving the replay needs no repair, only setReplay(null). */
          <ReportsDashboard
            sessionId={replay ? replay.sessionId : sessionId}
            reports={replay ? replay.reports : reports}
            activeType={activeReportType}
            onSelectType={replay ? requestReplayReport : requestReport}
            recommendations={replay ? replay.recommendations : recommendations}
            /* Only a fallback for a report that names no source files, and a
               replayed report always names its own - the live profiles would be
               the wrong files entirely. */
            fileProfiles={replay ? null : fileProfiles}
            generating={replay ? replay.generating : generating}
            errors={replay ? replay.errors : reportErrors}
            replaySessionId={replay ? replay.sessionId : null}
            onExitReplay={replay ? () => setReplay(null) : null}
          />
        )}
        {activeTab === 'settings' && <SettingsDashboard />}
        {/* Hands back a saved session for `replay` above - a slot the prefetch
            effect doesn't watch. Writing one into sessionId/recommendations instead
            would build every remaining letter for a session someone only wanted to
            look at, and would discard the live session to do it. */}
        {DEV_TAB_ENABLED && activeTab === 'dev' && (
          <Suspense fallback={<div style={{ padding: 24 }}>Loading developer tools…</div>}>
            <DevReportBrowser onLoadSession={loadReplay} />
          </Suspense>
        )}
      </main>
    </div>
  );
}
