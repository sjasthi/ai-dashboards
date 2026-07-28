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
