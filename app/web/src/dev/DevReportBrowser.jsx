import { useCallback, useEffect, useState } from 'react';
import ReportsDashboard from '../components/Reportsdashboard';
import { API_BASE } from '../api';
import { triggerDownload } from '../export';
// Imported here, not from the global stylesheet, so a production build drops these
// rules along with this module.
import './dev-browser.css';

/**
 * Developer tool: reopen a previously generated report without re-uploading and
 * without another LLM call.
 *
 * NOT a user feature. It has no entry in the nav, nothing on the home page links
 * here, and it is gated twice over -- see App.jsx, which mounts it only when
 * `import.meta.env.DEV` is true (so Vite strips this whole module from a
 * production build) and `?dev=1` is in the query string.
 *
 * Three design points worth knowing before changing anything here.
 *
 * It renders from its own local state and never touches App's `sessionId` or
 * `recommendations`. That is what keeps it cheap: App's prefetch effect fires on
 * those two, so leaving them alone means it never runs, and there is no restore
 * endpoint to build.
 *
 * It passes `showExport={false}`. ExportPanel polls /api/export/<id>/status on
 * mount, and a restored bundle has no live server session, so the panel must be
 * absent rather than merely disabled.
 *
 * The token is a shared secret typed by a developer, kept in sessionStorage so it
 * survives a reload but not the browser closing. It authorises nothing a user
 * could reach -- the endpoints behind it return 404 unless ADMIN_TOKEN is set
 * server-side.
 */

const TOKEN_KEY = 'aidash_admin_token';
const SUPPORTED_BUNDLE_VERSION = 1;

function readToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

function writeToken(value) {
  try {
    if (value) window.sessionStorage.setItem(TOKEN_KEY, value);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode - the token just will not persist across reloads */
  }
}

/** Reject a bundle this build cannot render, rather than showing half a report. */
function validateBundle(bundle) {
  if (!bundle || typeof bundle !== 'object') return 'Not a JSON object.';
  if (bundle.bundle_version !== SUPPORTED_BUNDLE_VERSION) {
    return `Unsupported bundle_version ${bundle.bundle_version} `
         + `(this build reads ${SUPPORTED_BUNDLE_VERSION}).`;
  }
  if (!bundle.report) return 'Bundle has no "report".';
  return null;
}

export default function DevReportBrowser() {
  const [token, setToken] = useState(readToken);
  const [tokenDraft, setTokenDraft] = useState('');
  const [list, setList] = useState(null);
  const [historyEnabled, setHistoryEnabled] = useState(null);
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const authFetch = useCallback((path) => fetch(`${API_BASE}${path}`, {
    headers: { 'X-Admin-Token': token },
  }), [token]);

  const loadList = useCallback(async () => {
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const response = await authFetch('/api/admin/reports');
      if (response.status === 404) {
        // The routes hide themselves when ADMIN_TOKEN is unset server-side, so a
        // 404 here means "not configured", not "no reports".
        throw new Error('Admin endpoints are not enabled. Set ADMIN_TOKEN in .env '
                      + 'and restart the backend.');
      }
      if (response.status === 401) {
        writeToken('');
        setToken('');
        throw new Error('That token was rejected.');
      }
      if (!response.ok) throw new Error(`Request failed (${response.status}).`);
      const body = await response.json();
      setList(body.reports || []);
      setHistoryEnabled(body.history_enabled);
    } catch (e) {
      setError(e.message);
      setList(null);
    } finally {
      setBusy(false);
    }
  }, [authFetch, token]);

  useEffect(() => { loadList(); }, [loadList]);

  async function openReport(id) {
    setBusy(true);
    setError(null);
    try {
      const response = await authFetch(`/api/admin/reports/${id}`);
      if (!response.ok) throw new Error(`Could not load report ${id} (${response.status}).`);
      const loaded = await response.json();
      const problem = validateBundle(loaded);
      if (problem) throw new Error(problem);
      setBundle(loaded);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  /** Load a bundle from disk, so one can be handed over without database access. */
  async function openFile(file) {
    setError(null);
    try {
      const parsed = JSON.parse(await file.text());
      const problem = validateBundle(parsed);
      if (problem) throw new Error(problem);
      setBundle(parsed);
    } catch (e) {
      setError(`Could not read ${file.name}: ${e.message}`);
    }
  }

  function downloadBundle() {
    if (!bundle) return;
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    triggerDownload(blob, `report-${bundle.session_id}-${bundle.report_letter}.json`);
  }

  if (!token) {
    return (
      <div className="dev-browser">
        <DevHeader />
        <form
          className="dev-browser__auth"
          onSubmit={(e) => {
            e.preventDefault();
            const value = tokenDraft.trim();
            if (!value) return;
            writeToken(value);
            setToken(value);
            setTokenDraft('');
          }}
        >
          <label className="dev-browser__label" htmlFor="dev-token">Admin token</label>
          <input
            id="dev-token"
            type="password"
            className="dev-browser__input"
            value={tokenDraft}
            onChange={(e) => setTokenDraft(e.target.value)}
            placeholder="value of ADMIN_TOKEN"
            autoComplete="off"
          />
          <button type="submit" className="home-cta">Unlock</button>
        </form>
        {error && <p className="dev-browser__error">{error}</p>}
        <p className="dev-browser__hint">
          Or open a bundle file directly, no token needed:
        </p>
        <BundleFileInput onPick={openFile} />
      </div>
    );
  }

  if (bundle) {
    const letter = bundle.report_letter || 'A';
    return (
      <div className="dev-browser">
        <DevHeader />
        <div className="dev-browser__bar">
          <button type="button" className="dev-browser__back" onClick={() => setBundle(null)}>
            ← Back to list
          </button>
          <span className="dev-browser__meta">
            session <code>{bundle.session_id}</code> · report {letter} ·
            saved {bundle.saved_at}
          </span>
          <button type="button" className="dev-browser__back" onClick={downloadBundle}>
            Download bundle
          </button>
        </div>

        {/*
          Rendered from this component's state alone. `reports` is keyed by the
          bundle's own letter and `activeType` matches it, so the dashboard shows
          the restored report and nothing else. No onSelectType: there is only one
          report here, and offering tabs that cannot load would be a lie.
        */}
        <ReportsDashboard
          reports={{ [letter]: bundle.report }}
          activeType={letter}
          recommendations={bundle.recommendations}
          fileProfiles={bundle.file_profiles}
          showExport={false}
        />
      </div>
    );
  }

  return (
    <div className="dev-browser">
      <DevHeader />

      <div className="dev-browser__bar">
        <button type="button" className="dev-browser__back" onClick={loadList} disabled={busy}>
          {busy ? 'Loading…' : 'Refresh'}
        </button>
        <BundleFileInput onPick={openFile} />
        <button
          type="button"
          className="dev-browser__back"
          onClick={() => { writeToken(''); setToken(''); }}
        >
          Forget token
        </button>
      </div>

      {error && <p className="dev-browser__error">{error}</p>}

      {historyEnabled === false && (
        <p className="dev-browser__hint">
          <strong>SAVE_REPORT_HISTORY is off</strong>, so nothing is being saved.
          Set it in <code>.env</code> and restart the backend, then generate a
          report. Reports built before that point were never stored.
        </p>
      )}

      {list && list.length === 0 && historyEnabled && (
        <p className="dev-browser__hint">
          Saving is on, but no reports have been generated yet.
        </p>
      )}

      {list && list.length > 0 && (
        <table className="dev-browser__table">
          <thead>
            <tr>
              <th>Saved</th><th>Session</th><th>Report</th><th>Name</th>
              <th>Size</th><th />
            </tr>
          </thead>
          <tbody>
            {list.map((row) => (
              <tr key={row.id}>
                <td>{row.ts}</td>
                <td><code>{row.session_id}</code></td>
                <td>{row.letter}</td>
                <td>{row.name || '—'}</td>
                <td>{Math.round((row.bytes || 0) / 1024)} kB</td>
                <td>
                  <button type="button" className="dev-browser__back"
                          onClick={() => openReport(row.id)} disabled={busy}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DevHeader() {
  return (
    <header className="dev-browser__head">
      <h1 className="dev-browser__title">Saved report browser</h1>
      <p className="dev-browser__sub">
        Developer tool. Present only in a development build, and only with{' '}
        <code>?dev=1</code>. Reports open from stored JSON — no analysis is re-run
        and no model is called.
      </p>
    </header>
  );
}

function BundleFileInput({ onPick }) {
  return (
    <label className="dev-browser__file">
      Open bundle file
      <input
        type="file"
        accept=".json,application/json"
        onChange={(e) => {
          const file = e.target.files && e.target.files[0];
          if (file) onPick(file);
          e.target.value = '';  // so picking the same file twice still fires
        }}
      />
    </label>
  );
}
