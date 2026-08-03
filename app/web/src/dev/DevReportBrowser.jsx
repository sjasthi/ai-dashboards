/**
 * Developer-only browser for past sessions.
 *
 * Not a user feature: there is no nav entry unless ?dev=1 is in the URL, and the
 * whole module is dropped from production builds (see App.jsx's two gates). It
 * exists because reports otherwise vanish on refresh - SESSIONS is an in-memory
 * dict - and because there was no way to inspect what the pipeline produced for a
 * run that has already happened.
 *
 * What it shows is a *reproduction*, not an archive. The server re-reads the saved
 * workbook and re-runs today's pipeline over it, so a session that now renders
 * differently after a refactor is a finding worth seeing, and a replay that fails
 * where the live run succeeded must be shown as an error rather than swallowed into
 * a blank panel. That is why every failure below has a visible home.
 *
 * It holds its own state and never touches App's. Setting App's sessionId and
 * recommendations would trip the background prefetch effect, which would then build
 * reports B and C over HTTP for a session the developer only wanted to look at.
 */

import { useCallback, useEffect, useState } from 'react';
import Card from '../components/ui/Card';
import DataTable from '../components/ui/DataTable';
import Plot from '../components/ui/Plot';
import Section from '../components/ui/Section';
import StatTile from '../components/ui/StatTile';
import { triggerDownload } from '../export';
import {
  deleteSession, fetchReport, fetchSessions, getAdminToken, setAdminToken,
} from './adminApi';

export default function DevReportBrowser() {
  const [token, setToken] = useState(getAdminToken);
  const [sessions, setSessions] = useState([]);
  const [historyEnabled, setHistoryEnabled] = useState(null);
  const [listError, setListError] = useState(null);
  const [loadingList, setLoadingList] = useState(false);

  const [openSession, setOpenSession] = useState(null);
  const [letter, setLetter] = useState('A');
  const [report, setReport] = useState(null);
  const [reportError, setReportError] = useState(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [source, setSource] = useState(null); // 'server' | 'file'

  const loadList = useCallback(async () => {
    if (!getAdminToken()) return;
    setLoadingList(true);
    setListError(null);
    try {
      const body = await fetchSessions();
      setSessions(body.sessions || []);
      setHistoryEnabled(body.history_enabled);
    } catch (err) {
      // 404 and 401 mean different things and lead to different fixes, so they are
      // spelled out rather than folded into one "couldn't load" message.
      setListError(
        err.status === 404
          ? 'ADMIN_TOKEN is not set on the server, so these routes answer 404 on purpose. Set it in .env and restart the API.'
          : err.status === 401
            ? 'That token was rejected. Check ADMIN_TOKEN in the server’s .env.'
            : err.message
      );
      setSessions([]);
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList, token]);

  const openReport = useCallback(async (sessionId, which) => {
    setOpenSession(sessionId);
    setLetter(which);
    setLoadingReport(true);
    setReportError(null);
    setReport(null);
    setSource('server');
    try {
      setReport(await fetchReport(sessionId, which));
    } catch (err) {
      setReportError({
        heading: `Report ${which} could not be rebuilt`,
        status: err.status,
        message: err.message,
      });
    } finally {
      setLoadingReport(false);
    }
  }, []);

  const removeSession = useCallback(async (sessionId) => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(`Delete the saved workbook and manifest for ${sessionId}?`)) return;
    try {
      await deleteSession(sessionId);
      if (openSession === sessionId) { setReport(null); setOpenSession(null); }
      loadList();
    } catch (err) {
      setListError(err.message);
    }
  }, [loadList, openSession]);

  /**
   * Open a report payload saved to disk instead of asking the server for one.
   *
   * Kept because it is the only way to look at a report from a machine you don't
   * have access to: the other developer downloads the JSON, sends it over, and it
   * renders here through the same path a server response does.
   */
  const openLocalFile = useCallback(async (file) => {
    if (!file) return;
    setLoadingReport(true);
    setReportError(null);
    setReport(null);
    setSource('file');
    setOpenSession(file.name);
    try {
      const parsed = JSON.parse(await file.text());
      const problem = validateReportPayload(parsed);
      if (problem) throw new Error(problem);
      setLetter(parsed.report_type || '?');
      setReport(parsed);
    } catch (err) {
      setReportError({ heading: `${file.name} could not be opened`, message: err.message });
    } finally {
      setLoadingReport(false);
    }
  }, []);

  const saveReport = useCallback(() => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    triggerDownload(blob, `${report.session_id || 'report'}_${report.report_type || letter}.json`);
  }, [report, letter]);

  if (!token) {
    return <TokenGate onSubmit={(value) => { setAdminToken(value); setToken(value); }} />;
  }

  return (
    <div className="dev-browser">
      <header style={{ marginBottom: 16 }}>
        <span className="eyebrow">Developer tool</span>
        <h1 style={{ margin: '4px 0 6px', fontSize: '1.4rem' }}>Session browser</h1>
        <p style={{ margin: 0, color: 'var(--color-ink-muted, #64748b)', fontSize: 14 }}>
          Reports are rebuilt from the saved workbook by today’s pipeline — no LLM call,
          no quota. What you see is what this code does with that input now, which is
          not necessarily what the user saw at the time.
        </p>
      </header>

      <Section
        title="Saved sessions"
        actions={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label className="table-toggle" style={{ cursor: 'pointer' }}>
              Open a .json report
              <input
                type="file"
                accept=".json,application/json"
                style={{ display: 'none' }}
                onChange={(e) => openLocalFile(e.target.files?.[0])}
              />
            </label>
            <button className="table-toggle" onClick={loadList} disabled={loadingList}>
              {loadingList ? 'Refreshing…' : 'Refresh'}
            </button>
            <button
              className="table-toggle"
              onClick={() => { setAdminToken(''); setToken(''); }}
            >
              Forget token
            </button>
          </div>
        }
      >
        {listError && <Notice tone="error">{listError}</Notice>}

        {!listError && historyEnabled === false && (
          <Notice tone="warn">
            SAVE_REPORT_HISTORY is off, so nothing new is being saved. Set it in
            <code> .env</code> and restart the API; sessions analysed before that
            point were never persisted and cannot be recovered.
          </Notice>
        )}

        <SessionList
          sessions={sessions}
          openSession={openSession}
          activeLetter={letter}
          onOpen={openReport}
          onDelete={removeSession}
        />
      </Section>

      {(loadingReport || report || reportError) && (
        <Section
          title={source === 'file' ? `Report from file · ${openSession}` : `Report ${letter} · ${openSession}`}
          actions={report && (
            <button className="table-toggle" onClick={saveReport}>Download JSON</button>
          )}
        >
          {loadingReport && <Card><div className="empty-state">Rebuilding…</div></Card>}

          {reportError && (
            <Notice tone="error">
              <strong>{reportError.heading}</strong>
              {reportError.status ? ` (HTTP ${reportError.status})` : ''}: {reportError.message}
              {reportError.status === 422 && (
                <div style={{ marginTop: 6 }}>
                  A 422 here means the saved recommendation no longer executes against
                  its own data — worth investigating rather than dismissing.
                </div>
              )}
            </Notice>
          )}

          {report && <ReportView report={report} />}
        </Section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- pieces */

function TokenGate({ onSubmit }) {
  const [value, setValue] = useState('');
  return (
    <div className="dev-browser" style={{ maxWidth: 460 }}>
      <Card>
        <div style={{ padding: 4 }}>
          <span className="eyebrow">Developer tool</span>
          <h2 style={{ margin: '6px 0 8px', fontSize: '1.15rem' }}>Admin token</h2>
          <p style={{ fontSize: 14, color: '#64748b', marginTop: 0 }}>
            The value of <code>ADMIN_TOKEN</code> in the server’s <code>.env</code>.
            Kept in sessionStorage, so it is gone when this browser session ends.
          </p>
          <form onSubmit={(e) => { e.preventDefault(); onSubmit(value.trim()); }}>
            <input
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="ADMIN_TOKEN"
              style={{
                width: '100%', padding: '8px 10px', fontSize: 14,
                border: '1px solid #cbd5e1', borderRadius: 6, marginBottom: 8,
              }}
            />
            <button className="table-toggle" type="submit" disabled={!value.trim()}>
              Unlock
            </button>
          </form>
        </div>
      </Card>
    </div>
  );
}

function SessionList({ sessions, openSession, activeLetter, onOpen, onDelete }) {
  if (!sessions.length) {
    return (
      <Card>
        <div className="empty-state">
          No saved sessions yet. Run an analysis with SAVE_REPORT_HISTORY on and it
          will appear here.
        </div>
      </Card>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {sessions.map((s) => (
        <Card key={s.session_id}>
          <div style={{
            display: 'flex', gap: 12, alignItems: 'flex-start',
            justifyContent: 'space-between', flexWrap: 'wrap', padding: 4,
          }}>
            <div style={{ minWidth: 220 }}>
              <div style={{ fontWeight: 600, fontFamily: 'ui-monospace, monospace' }}>
                {s.session_id}
              </div>
              <div style={{ fontSize: 13, color: '#64748b' }}>
                {(s.files || []).join(', ') || 'no files recorded'}
                {s.saved_at ? ` · saved ${s.saved_at.replace('T', ' ').slice(0, 19)}` : ''}
              </div>
              {!s.replayable && (
                <div style={{ fontSize: 13, color: '#b45309', marginTop: 4 }}>
                  Source files deleted — the manifest remains, but this session can no
                  longer be rebuilt.
                </div>
              )}
            </div>

            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              {(s.reports || []).map((r) => (
                <button
                  key={r.letter}
                  type="button"
                  className="segmented__btn"
                  title={r.report_name || ''}
                  disabled={!s.replayable}
                  aria-pressed={openSession === s.session_id && activeLetter === r.letter}
                  onClick={() => onOpen(s.session_id, r.letter)}
                >
                  {r.letter}
                </button>
              ))}
              <button className="table-toggle" onClick={() => onDelete(s.session_id)}>
                Delete
              </button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

/**
 * One rebuilt report: KPIs, chart, insights, table.
 *
 * Built from the shared ui/ primitives rather than by exporting Reportsdashboard's
 * internals, so a developer tool never dictates the shape of the page users see.
 * The trade is that this is a plainer rendering of the same payload - deliberate,
 * since what matters here is whether the numbers came back, not how they look.
 */
function ReportView({ report }) {
  const stats = report.stats || {};
  const hasStats = !!stats.available;

  return (
    <>
      <div className="kpi-row">
        <StatTile label="Rows" value={report.report_rows ?? null} />
        <StatTile label="Columns" value={report.data_columns?.length ?? null} />
        <StatTile label="Pattern" value={report.pattern_used || null} />
        <StatTile label="Chart" value={report.chart_type || null} />
        {hasStats && (
          <StatTile
            label={stats.headline_label || stats.measure_label || 'Headline'}
            value={stats.headline_value}
            sublabel={stats.headline_sublabel}
          />
        )}
      </div>

      <Card>
        <div style={{ padding: 4 }}>
          <div style={{ fontWeight: 600 }}>{report.report_name || 'Untitled report'}</div>
          {report.question_answered && (
            <div style={{ fontSize: 14, color: '#64748b', marginTop: 2 }}>
              “{report.question_answered}”
            </div>
          )}
          <div className="provenance" style={{ marginTop: 6 }}>
            <span>{(report.source_files || []).join(', ') || 'no source files listed'}</span>
            {report.generated_at && <span> · rebuilt {report.generated_at.replace('T', ' ').slice(0, 19)}</span>}
            {(report.operations || []).map((op) => <code key={op}>{op}</code>)}
          </div>
          {report.rows_truncated && (
            <div style={{ fontSize: 13, color: '#b45309', marginTop: 6 }}>
              Showing the first {report.rows?.length} of {report.report_rows} rows — the
              response is capped, the same way the live endpoint caps it.
            </div>
          )}
        </div>
      </Card>

      {report.chart
        ? <Card className="chart-card"><Plot chart={report.chart} stats={stats} className="chart-card__plot" /></Card>
        : <Notice tone="warn">No chart in this payload — the figure failed to build, which the server records but does not treat as fatal.</Notice>}

      {report.schema_warning && <Notice tone="warn">{report.schema_warning}</Notice>}

      {hasStats ? (
        <div className="insight-rail">
          {[
            ['Top insight', stats.top_insight_text],
            ['Anomalies', stats.anomaly_text],
            ['Data quality', stats.quality_text],
            ['What to do', stats.recommendation_text],
          ].filter(([, text]) => text).map(([title, text]) => (
            <Card className="insight-card" key={title}>
              <div className="insight-card__head">
                <span className="insight-card__title">{title}</span>
              </div>
              <div className="insight-card__body">{text}</div>
            </Card>
          ))}
        </div>
      ) : (
        <Notice tone="warn">
          No statistics were computed for this report — stats failed or the shape
          wasn’t one report_stats handles.
        </Notice>
      )}

      <DataTable
        columns={report.data_columns || []}
        rows={report.rows || []}
        totalRows={report.report_rows || 0}
        truncated={!!report.rows_truncated}
      />
    </>
  );
}

function Notice({ tone = 'warn', children }) {
  const colors = tone === 'error'
    ? { bg: '#fef2f2', border: '#fecaca', ink: '#991b1b' }
    : { bg: '#fffbeb', border: '#fde68a', ink: '#92400e' };
  return (
    <div
      role={tone === 'error' ? 'alert' : undefined}
      style={{
        background: colors.bg, border: `1px solid ${colors.border}`, color: colors.ink,
        borderRadius: 8, padding: '10px 12px', fontSize: 14, margin: '8px 0',
      }}
    >
      {children}
    </div>
  );
}

/**
 * Why a payload can't be rendered, or null if it can.
 *
 * A hand-edited or truncated file should say what is wrong with it, not throw
 * somewhere inside DataTable half a screen later.
 */
export function validateReportPayload(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return 'not a JSON object';
  }
  if (!Array.isArray(payload.rows)) return 'no "rows" array — is this a report payload?';
  if (!Array.isArray(payload.columns)) return 'no "columns" array';
  if (payload.stats && typeof payload.stats !== 'object') return '"stats" is not an object';
  if (payload.chart && typeof payload.chart !== 'object') return '"chart" is not an object';
  return null;
}
