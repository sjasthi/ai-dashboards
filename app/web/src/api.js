/** Shared backend calls, so report generation has one implementation. */

export const API_BASE = 'http://localhost:8000';

export const REPORT_TYPE_LETTERS = ['A', 'B', 'C', 'D'];

async function postJson(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}));
    throw new Error(errBody.detail || `Request failed with status ${response.status}`);
  }
  return response.json();
}

/**
 * Generate one report. The response now carries `stats` (computed from the report's
 * own rows), `rows` for the table view, and provenance - see api.generate_report_endpoint.
 */
export function generateReport(sessionId, reportType) {
  return postJson('/api/generate-report', {
    session_id: sessionId,
    report_type: reportType,
  });
}

/* ------------------------------------------------------------------- export */

/** Pull the server's filename out of Content-Disposition, or null if absent. */
function filenameFromDisposition(header) {
  if (!header) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * POST and read the response as a file rather than JSON.
 *
 * postJson can't be reused: it calls response.json() unconditionally, which would
 * throw on PDF bytes. Errors still arrive as JSON `{detail}`, so the body is read as
 * text once and parsed opportunistically - reading it twice isn't allowed.
 */
async function postForBinary(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch { /* not JSON */ }
    throw new Error(detail || `Request failed with status ${response.status}`);
  }

  return {
    blob: await response.blob(),
    // Null when the CORS layer hasn't exposed the header; the caller falls back to
    // a locally built name rather than failing the download.
    filename: filenameFromDisposition(response.headers.get('content-disposition')),
  };
}

function exportBody({ reportTypes, format, chartImages }) {
  return {
    report_types: reportTypes,
    format,
    chart_images: chartImages || {},
  };
}

/**
 * Download the selected reports as one file.
 *
 * One report gives a single-report document; two or more give one combined
 * comparative document.
 */
export function exportReports(sessionId, options) {
  return postForBinary(`/api/export/${sessionId}`, exportBody(options));
}

/** Email the selected reports as an attachment. */
export function emailReports(sessionId, { recipients, message, ...options }) {
  return postJson(`/api/export/${sessionId}/email`, {
    ...exportBody(options),
    recipients,
    message: message || null,
  });
}

/** Which reports can be exported, and whether email is set up server-side. */
export async function fetchExportStatus(sessionId) {
  const response = await fetch(`${API_BASE}/api/export/${sessionId}/status`);
  if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
  return response.json();
}
