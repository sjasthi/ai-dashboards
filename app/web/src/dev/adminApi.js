/**
 * Calls to the token-gated /api/admin/* routes.
 *
 * Deliberately separate from src/api.js. Everything in there spreads
 * clientIdHeaders() and none of it attaches X-Admin-Token, but the real reason for
 * the split is the bundle: nothing under src/dev/ is imported by production code,
 * so the admin surface cannot end up in a build that a user loads. Adding these
 * functions to api.js would ship them to everyone.
 *
 * The token lives in sessionStorage, not localStorage: it is a shared secret typed
 * by a developer, and it should not outlive the browser session it was typed into.
 */

import { API_BASE } from '../api';

const TOKEN_KEY = 'aidash_admin_token';

export function getAdminToken() {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setAdminToken(token) {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode - the token just won't survive a reload */
  }
}

/**
 * GET (or DELETE) an admin route and parse the JSON.
 *
 * Throws an Error carrying `status`, because the two failures the caller has to
 * distinguish are both 4xx: 404 means ADMIN_TOKEN isn't configured server-side (the
 * routes deliberately don't advertise themselves), 401 means the token is wrong.
 * A thrown message alone can't tell those apart.
 */
async function request(path, { method = 'GET', body } = {}) {
  const token = getAdminToken();
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(token ? { 'X-Admin-Token': token } : {}),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.detail || `Request failed with status ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

/** Every saved session, newest first, plus whether saving is switched on at all. */
export function fetchSessions() {
  return request('/api/admin/sessions');
}

/**
 * One saved session's recommendations, as the manifest recorded them.
 *
 * The listing carries only a letter and a name per report - enough to label a
 * button, not enough for the Reports page, which reads the recommendation objects
 * for its compare view and its export selection. Fetched per session rather than
 * folded into the listing so 200 rows don't each carry a full LLM response.
 */
export function fetchSessionDetail(sessionId) {
  return request(`/api/admin/sessions/${encodeURIComponent(sessionId)}`);
}

/**
 * Rebuild one report from a saved session.
 *
 * Costs no LLM quota: the server re-reads the saved workbook and re-runs the same
 * pandas pipeline the live endpoint runs. A 410 means the source files have been
 * deleted; a 422 means the recommendation no longer executes against them, which is
 * a finding about today's code rather than a bug in this viewer.
 */
export function fetchReport(sessionId, letter) {
  return request(
    `/api/admin/sessions/${encodeURIComponent(sessionId)}`
    + `/reports/${encodeURIComponent(letter)}`,
  );
}

/** Forget one saved session - manifest and retained workbooks alike. */
export function deleteSession(sessionId) {
  return request(`/api/admin/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}

/**
 * Delete a set of saved sessions chosen by exactly one criterion.
 *
 * Two-step by design. Called without `dry_run: false` the server deletes nothing
 * and reports what *would* go, which is what the confirmation panel renders; the
 * same call repeated with `dry_run: false` commits it. Passing no criterion is a
 * 400 rather than "everything", so a bug in this file cannot empty the store.
 *
 * @param {{session_ids?: string[], older_than_days?: number, keep_newest?: number,
 *          unreplayable_only?: boolean, dry_run?: boolean}} criteria
 */
export function pruneSessions(criteria) {
  return request('/api/admin/sessions/prune', { method: 'POST', body: criteria });
}

/** The raw event log, not the aggregates the home page reads. */
export function fetchAdminStats(limit = 100) {
  return request(`/api/admin/stats?limit=${limit}`);
}
