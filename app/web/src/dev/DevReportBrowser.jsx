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
 * The report itself is not drawn here. This hands the rebuilt payload to App via
 * `onLoadSession`, which puts it on the real Reports page - the same page, the same
 * components, the same payload the live endpoint returns. A second renderer used to
 * live in this file and was always a plainer view of the same object; keeping it
 * meant every panel added to the Reports page quietly stopped being visible to the
 * one tool built for inspecting reports.
 *
 * It still sets none of App's session state. `onLoadSession` fills a slot the
 * background prefetch effect does not watch, because writing a saved session into
 * sessionId/recommendations would build every remaining letter over HTTP for a
 * session someone only wanted to look at - and would throw away the live one.
 */

import { useCallback, useEffect, useState } from 'react';
import Card from '../components/ui/Card';
import Section from '../components/ui/Section';
import { clockTime, formatBytes } from '../format';
import { triggerDownload } from '../export';
import {
  deleteSession, fetchReport, fetchSessionDetail, fetchSessions, getAdminToken,
  pruneSessions, setAdminToken,
} from './adminApi';

export default function DevReportBrowser({ onLoadSession }) {
  const [token, setToken] = useState(getAdminToken);
  const [sessions, setSessions] = useState([]);
  const [historyEnabled, setHistoryEnabled] = useState(null);
  const [totalBytes, setTotalBytes] = useState(0);
  const [listError, setListError] = useState(null);
  const [loadingList, setLoadingList] = useState(false);

  // Which rows are ticked, and the pending prune preview. `preview` holds the
  // server's dry-run answer: it is both the confirmation screen and the exact
  // criteria that will be re-sent to commit, so what you approve is what runs.
  const [selected, setSelected] = useState(() => new Set());
  const [preview, setPreview] = useState(null);
  const [pruning, setPruning] = useState(false);
  const [pruneNote, setPruneNote] = useState(null);

  // Which row is being rebuilt, and what went wrong if it was. Failures stay on
  // this page rather than travelling to the Reports page with the payload: a 410 or
  // a 422 is what the developer clicked for, and the explanation below it would be
  // meaningless on a page users see.
  const [openSession, setOpenSession] = useState(null);
  const [reportError, setReportError] = useState(null);
  const [loadingReport, setLoadingReport] = useState(false);

  const loadList = useCallback(async () => {
    if (!getAdminToken()) return;
    setLoadingList(true);
    setListError(null);
    try {
      const body = await fetchSessions();
      setSessions(body.sessions || []);
      setHistoryEnabled(body.history_enabled);
      setTotalBytes(body.total_bytes || 0);
      // Drop ticks for rows that no longer exist, so a stale selection can't be
      // carried into a later prune.
      const alive = new Set((body.sessions || []).map((s) => s.session_id));
      setSelected((prev) => new Set([...prev].filter((id) => alive.has(id))));
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

  /**
   * Rebuild a saved session's first report and hand the whole session to App.
   *
   * Two requests, one try: the detail call supplies the recommendations the Reports
   * page needs for its switcher and its export selection, the report call supplies
   * the payload it draws. A session whose detail returns but whose report 422s has
   * to report the 422 - loading it half-way would put an empty Reports page in front
   * of someone with no indication of why.
   *
   * Only report A is fetched. The Reports page requests the rest on click through
   * `fetchReport`, which is handed over here rather than imported there so nothing
   * under src/dev/ is reachable from a production build.
   */
  const openReport = useCallback(async (sessionId) => {
    setOpenSession(sessionId);
    setLoadingReport(true);
    setReportError(null);
    try {
      const detail = await fetchSessionDetail(sessionId);
      const report = await fetchReport(sessionId, 'A');
      onLoadSession({
        sessionId,
        recommendations: detail.recommendations,
        report,
        fetchReport,
      });
    } catch (err) {
      setReportError({
        heading: `${sessionId} could not be rebuilt`,
        status: err.status,
        message: err.message,
      });
    } finally {
      setLoadingReport(false);
    }
  }, [onLoadSession]);

  const removeSession = useCallback(async (sessionId) => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(`Delete the saved workbook and manifest for ${sessionId}?`)) return;
    try {
      await deleteSession(sessionId);
      if (openSession === sessionId) { setOpenSession(null); setReportError(null); }
      loadList();
    } catch (err) {
      setListError(err.message);
    }
  }, [loadList, openSession]);

  const toggleSelected = useCallback((sessionId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sessionId)) next.delete(sessionId); else next.add(sessionId);
      return next;
    });
    setPreview(null);
  }, []);

  /**
   * Ask the server what a set of criteria would delete, without deleting it.
   *
   * The criteria object is kept verbatim in state and re-sent on confirm, so the
   * list someone approves is produced by exactly the request that then runs.
   * Rebuilding the criteria at confirm time is how a preview ends up describing a
   * different deletion than the one it authorised.
   */
  const requestPreview = useCallback(async (criteria) => {
    setPruning(true);
    setPruneNote(null);
    setListError(null);
    try {
      const body = await pruneSessions({ ...criteria, dry_run: true });
      setPreview({ criteria, ...body });
    } catch (err) {
      setListError(err.message);
      setPreview(null);
    } finally {
      setPruning(false);
    }
  }, []);

  const commitPreview = useCallback(async () => {
    if (!preview) return;
    setPruning(true);
    try {
      const body = await pruneSessions({ ...preview.criteria, dry_run: false });
      setPruneNote(
        body.deleted.length
          ? `Deleted ${body.deleted.length} session${body.deleted.length === 1 ? '' : 's'}, freeing ${formatBytes(body.freed_bytes)}.`
          : 'Nothing was deleted.'
      );
      if (body.failed?.length) {
        setListError(
          `${body.failed.length} could not be removed: ${body.failed.map((f) => f.session_id).join(', ')}`
        );
      }
      if (body.deleted.includes(openSession)) { setOpenSession(null); setReportError(null); }
      setPreview(null);
      setSelected(new Set());
      loadList();
    } catch (err) {
      setListError(err.message);
    } finally {
      setPruning(false);
    }
  }, [preview, loadList, openSession]);

  /**
   * Rebuild report A and save it as JSON without displaying it.
   *
   * The other half of `openLocalFile`: this is how a payload gets off a machine
   * someone else can't reach, and the file it writes is what that person opens.
   */
  const downloadReport = useCallback(async (sessionId) => {
    setOpenSession(sessionId);
    setLoadingReport(true);
    setReportError(null);
    try {
      const payload = await fetchReport(sessionId, 'A');
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      triggerDownload(blob, `${sessionId}_A.json`);
    } catch (err) {
      setReportError({
        heading: `${sessionId} could not be rebuilt`,
        status: err.status,
        message: err.message,
      });
    } finally {
      setLoadingReport(false);
    }
  }, []);

  /**
   * Open a report payload saved to disk instead of asking the server for one.
   *
   * Kept because it is the only way to look at a report from a machine you don't
   * have access to: the other developer downloads the JSON, sends it over, and it
   * renders here through the same path a server response does.
   *
   * A file carries one report and no recommendations, so both are synthesised. The
   * placeholder entries ahead of it exist to put the payload at its own letter -
   * relabelling a report B as A to make a one-entry list fit would be a lie about
   * which report someone is reading. Its fetcher refuses, because the other letters
   * genuinely are not in the file.
   */
  const openLocalFile = useCallback(async (file) => {
    if (!file) return;
    setLoadingReport(true);
    setReportError(null);
    setOpenSession(file.name);
    try {
      const parsed = JSON.parse(await file.text());
      const problem = validateReportPayload(parsed);
      if (problem) throw new Error(problem);

      const index = Math.max(0, (parsed.report_type || 'A').charCodeAt(0) - 65);
      const recs = Array.from({ length: index + 1 }, (_, i) => (
        i === index
          ? {
            report_name: parsed.report_name,
            pattern_used: parsed.pattern_used,
            question_answered: parsed.question_answered,
          }
          : { report_name: 'Not in this file' }
      ));

      onLoadSession({
        sessionId: parsed.session_id || file.name,
        recommendations: { recommendations: recs },
        report: parsed,
        fetchReport: (_sid, which) => Promise.reject(
          new Error(`${file.name} holds only report ${parsed.report_type || 'A'}, not ${which}.`)
        ),
      });
    } catch (err) {
      setReportError({ heading: `${file.name} could not be opened`, message: err.message });
    } finally {
      setLoadingReport(false);
    }
  }, [onLoadSession]);

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
          not necessarily what the user saw at the time. Generating one opens it on the
          Reports tab, where your own session is waiting behind a “Back to my session”
          button.
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

        {pruneNote && <Notice tone="ok">{pruneNote}</Notice>}

        {!!sessions.length && (
          <PrunePanel
            sessions={sessions}
            totalBytes={totalBytes}
            selected={selected}
            onSelectAll={(all) => {
              setSelected(all ? new Set(sessions.map((s) => s.session_id)) : new Set());
              setPreview(null);
            }}
            preview={preview}
            busy={pruning}
            onPreview={requestPreview}
            onCancel={() => setPreview(null)}
            onCommit={commitPreview}
          />
        )}

        <SessionList
          sessions={sessions}
          openSession={openSession}
          busy={loadingReport}
          selected={selected}
          onToggleSelected={toggleSelected}
          onOpen={openReport}
          onDownload={downloadReport}
          onDelete={removeSession}
        />
      </Section>

      {/* Failures stay here. A rebuild that 410s or 422s is a finding about this
          pipeline, and its explanation is written for whoever clicked the button -
          neither belongs on the page users read their own reports on. */}
      {reportError && (
        <Section title={`Could not rebuild · ${openSession}`}>
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
        </Section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- pieces */

function TokenGate({ onSubmit }) {
  const [value, setValue] = useState('');
  return (
    // Same width cap as Home/Upload/Analysis's centred column, not the 460px this
    // used to carry on its own - that squeezed the token-source sentence into three
    // ragged lines and left the card narrower than every other page's content.
    <div className="dev-browser" style={{ maxWidth: 'var(--content-max)' }}>
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

/**
 * What is being kept, what it costs, and the controls for getting rid of it.
 *
 * Retention is manual and unautomated: nothing deletes a session on a timer. This
 * panel is where a developer does it by hand, and the age criterion doubles as a
 * rehearsal — previewing "older than 30 days" shows exactly what a 30-day policy
 * would take, which is the evidence needed to decide whether to automate it.
 *
 * Every path here goes through a server-side dry run first. The preview is the
 * confirmation step, deliberately rather than a window.confirm: a bulk delete of
 * files a user handed us deserves a list of what is going, not a yes/no box.
 */
function PrunePanel({
  sessions, totalBytes, selected, onSelectAll, preview, busy,
  onPreview, onCancel, onCommit,
}) {
  const [days, setDays] = useState('30');
  const [keep, setKeep] = useState('20');
  const [unreplayableOnly, setUnreplayableOnly] = useState(false);

  const allSelected = selected.size > 0 && selected.size === sessions.length;
  const deadCount = sessions.filter((s) => !s.replayable).length;

  if (preview) {
    return (
      <Card>
        <div style={{ padding: 8 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            {preview.matched.length === 0
              ? 'Nothing matches those criteria.'
              : `This will permanently delete ${preview.matched.length} session${preview.matched.length === 1 ? '' : 's'} and free ${formatBytes(preview.freed_bytes)}.`}
          </div>
          <div style={{ fontSize: 13, color: '#64748b', marginBottom: 8 }}>
            Matched by <code>{preview.criterion}</code>. The workbooks these kept are
            the only copies — there is nothing to restore from afterwards.
          </div>

          {!!preview.matched.length && (
            <ul style={{
              margin: '0 0 10px', paddingLeft: 18, maxHeight: 220, overflowY: 'auto',
              fontSize: 13, fontFamily: 'ui-monospace, monospace',
            }}>
              {preview.matched.map((m) => (
                <li key={m.session_id}>
                  {m.session_id} · {formatBytes(m.bytes)}
                  {m.age_days != null && ` · ${m.age_days.toFixed(1)}d old`}
                  {!m.replayable && ' · already unreplayable'}
                  {m.files?.length ? ` · ${m.files.join(', ')}` : ''}
                </li>
              ))}
            </ul>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="table-toggle"
              style={{ borderColor: '#fecaca', color: '#991b1b' }}
              onClick={onCommit}
              disabled={busy || !preview.matched.length}
            >
              {busy ? 'Deleting…' : `Delete ${preview.matched.length}`}
            </button>
            <button className="table-toggle" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div style={{ padding: 8, display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <strong>{sessions.length} saved</strong>
          <span style={{ color: '#64748b', fontSize: 14 }}>
            {formatBytes(totalBytes)} on disk
            {deadCount > 0 && ` · ${deadCount} already unreplayable`}
          </span>
          <span style={{ flex: 1 }} />
          <label style={{ fontSize: 13, color: '#64748b', display: 'flex', gap: 5 }}>
            <input
              type="checkbox"
              checked={allSelected}
              onChange={(e) => onSelectAll(e.target.checked)}
            />
            Select all
          </label>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            className="table-toggle"
            disabled={busy || selected.size === 0}
            onClick={() => onPreview({ session_ids: [...selected] })}
          >
            Delete selected ({selected.size})
          </button>

          <span style={{ color: '#cbd5e1' }}>|</span>

          <label style={{ fontSize: 14, display: 'flex', gap: 6, alignItems: 'center' }}>
            older than
            <input
              type="number" min="0" value={days}
              onChange={(e) => setDays(e.target.value)}
              style={{ width: 64, padding: '4px 6px', border: '1px solid #cbd5e1', borderRadius: 6 }}
            />
            days
          </label>
          <button
            className="table-toggle"
            disabled={busy || days === ''}
            onClick={() => onPreview({
              older_than_days: Number(days), unreplayable_only: unreplayableOnly,
            })}
          >
            Preview
          </button>

          <span style={{ color: '#cbd5e1' }}>|</span>

          <label style={{ fontSize: 14, display: 'flex', gap: 6, alignItems: 'center' }}>
            keep newest
            <input
              type="number" min="0" value={keep}
              onChange={(e) => setKeep(e.target.value)}
              style={{ width: 64, padding: '4px 6px', border: '1px solid #cbd5e1', borderRadius: 6 }}
            />
          </label>
          <button
            className="table-toggle"
            disabled={busy || keep === ''}
            onClick={() => onPreview({
              keep_newest: Number(keep), unreplayable_only: unreplayableOnly,
            })}
          >
            Preview
          </button>
        </div>

        <label style={{ fontSize: 13, color: '#64748b', display: 'flex', gap: 6 }}>
          <input
            type="checkbox"
            checked={unreplayableOnly}
            onChange={(e) => setUnreplayableOnly(e.target.checked)}
          />
          Only sessions whose source files are already gone — dead manifests, safe to
          clear without losing a replay that still worked.
        </label>
      </div>
    </Card>
  );
}

function SessionList({
  sessions, openSession, busy, selected, onToggleSelected, onOpen, onDownload, onDelete,
}) {
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
            <div style={{ minWidth: 220, display: 'flex', gap: 10 }}>
              <input
                type="checkbox"
                aria-label={`Select ${s.session_id}`}
                checked={selected.has(s.session_id)}
                onChange={() => onToggleSelected(s.session_id)}
                style={{ marginTop: 4 }}
              />
              <div>
                <div style={{ fontWeight: 600, fontFamily: 'ui-monospace, monospace' }}>
                  {s.session_id}
                </div>
                <div style={{ fontSize: 13, color: '#64748b' }}>
                  {(s.files || []).join(', ') || 'no files recorded'}
                  {' · '}{formatBytes(s.bytes)}
                  {/* Time of day before the age: "2:38 PM · 10 days old" reads as
                      one clause, and the clock time is what distinguishes several
                      runs made on the same afternoon. */}
                  {clockTime(s.saved_at) && ` · ${clockTime(s.saved_at)}`}
                  {s.age_days != null && ` · ${formatAge(s.age_days)}`}
                </div>
                {!s.replayable && (
                  <div style={{ fontSize: 13, color: '#b45309', marginTop: 4 }}>
                    Source files deleted — the manifest remains, but this session can
                    no longer be rebuilt.
                  </div>
                )}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              {/* One button, not one per report. The Reports page has its own letter
                  switcher and fetches the rest on click, so offering the same choice
                  twice would only decide which report you land on. The names still
                  ride along in the tooltip - that is what tells two saved runs of the
                  same workbook apart before you open either. */}
              <button
                type="button"
                className="table-toggle"
                title={(s.reports || []).map((r) => `${r.letter}: ${r.report_name || '—'}`).join('\n')}
                disabled={!s.replayable || busy}
                onClick={() => onOpen(s.session_id)}
              >
                {busy && openSession === s.session_id ? 'Rebuilding…' : 'Generate report'}
              </button>
              <button
                type="button"
                className="table-toggle"
                title="Rebuild report A and save it as JSON, without opening it"
                disabled={!s.replayable || busy}
                onClick={() => onDownload(s.session_id)}
              >
                JSON
              </button>
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

/** 0.3 -> "today"; 1 -> "1 day old"; 40 -> "40 days old". */
function formatAge(days) {
  if (days < 1) return 'today';
  const whole = Math.floor(days);
  return `${whole} day${whole === 1 ? '' : 's'} old`;
}

const NOTICE_TONES = {
  error: { bg: '#fef2f2', border: '#fecaca', ink: '#991b1b' },
  warn: { bg: '#fffbeb', border: '#fde68a', ink: '#92400e' },
  ok: { bg: '#f0fdf4', border: '#bbf7d0', ink: '#166534' },
};

function Notice({ tone = 'warn', children }) {
  const colors = NOTICE_TONES[tone] || NOTICE_TONES.warn;
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
 *
 * `data_columns` rather than `columns`: a report payload carries both, but the
 * Reports page draws its table from data_columns - the other one still holds the
 * bookkeeping columns that get stripped. Checking the key nobody renders would let
 * exactly the broken file this exists to catch straight through.
 */
export function validateReportPayload(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return 'not a JSON object';
  }
  if (!Array.isArray(payload.rows)) return 'no "rows" array — is this a report payload?';
  if (!Array.isArray(payload.data_columns)) return 'no "data_columns" array';
  if (payload.stats && typeof payload.stats !== 'object') return '"stats" is not an object';
  if (payload.chart && typeof payload.chart !== 'object') return '"chart" is not an object';
  return null;
}
