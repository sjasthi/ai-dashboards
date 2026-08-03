import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import HomeDashboard from './components/Homedashboard';
import UploadDashboard from './components/Uploaddashboard';
import AnalysisDashboard from './components/Analysisdashboard';
import ReportsDashboard from './components/Reportsdashboard';
import SettingsDashboard from './components/Settingsdashboard';
import { REPORT_TYPE_LETTERS, generateReport } from './api';

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
  // Home is the landing tab: arriving on Upload gave no answer to "what is this",
  // and the visit counter has nowhere to be recorded from otherwise.
  const [activeTab, setActiveTab] = useState('home');

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

  const startNewSession = useCallback(() => {
    // A new upload invalidates every report built from the previous one. Requests
    // already in the air aren't cancellable, but bumping the generation makes
    // ensureReport drop them on arrival; clearing the map stops them being handed
    // back to a caller asking about the new upload.
    generation.current += 1;
    reportsRef.current = {};
    inFlight.current.clear();
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
   * One at a time, in rank order: the server is a single uvicorn worker and pandas
   * holds the GIL, so three at once finish no sooner but triple peak memory and
   * compete with whatever the user actually clicks.
   */
  useEffect(() => {
    const recList = recommendations?.recommendations;
    if (!sessionId || !recList?.length) return;

    const letters = recList
      .map((_, i) => REPORT_TYPE_LETTERS[i])
      .filter(Boolean);

    // Checked between reports, not just at the start: a new upload begins ~7s
    // before its session id exists, and this queue should stop the moment the user
    // kicks one off rather than keep building reports for data they've replaced.
    const gen = generation.current;
    let cancelled = false;
    (async () => {
      for (const letter of letters) {
        if (cancelled || generation.current !== gen) return;
        // Resolves rather than rejects, and returns the in-flight promise if the
        // user got to this letter first, so the queue never double-requests.
        await ensureReport(letter, sessionId);
      }
    })();

    return () => { cancelled = true; };
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
          <ReportsDashboard
            sessionId={sessionId}
            reports={reports}
            activeType={activeReportType}
            onSelectType={requestReport}
            recommendations={recommendations}
            fileProfiles={fileProfiles}
            generating={generating}
            errors={reportErrors}
          />
        )}
        {activeTab === 'settings' && <SettingsDashboard />}
        {/* Reads its own state from the admin API and sets none of App's: assigning
            sessionId/recommendations here would trigger the prefetch effect above
            and build reports B and C for a session someone only wanted to look at. */}
        {DEV_TAB_ENABLED && activeTab === 'dev' && (
          <Suspense fallback={<div style={{ padding: 24 }}>Loading developer tools…</div>}>
            <DevReportBrowser />
          </Suspense>
        )}
      </main>
    </div>
  );
}
