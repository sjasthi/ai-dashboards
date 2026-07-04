/**
 * export.js — handles report export actions.
 *
 * Calls the backend API to export analysis results in various formats.
 */
import { state } from './state.js';
import { exportReport } from './api.js';

/**
 * Handle export button clicks.
 * @param {string} reportType - 'summary', 'full', or 'recommendations'
 * @param {string} format - 'pdf', 'html', or 'email'
 */
export async function handleExport(reportType, format) {
  if (!state.sessionId) {
    alert('No analysis yet. Please generate a report first.');
    return;
  }

  try {
    // Map UI format to API format
    const apiFormat = format === 'email' ? 'json' : format;

    const result = await exportReport(state.sessionId, apiFormat);

    if (format === 'email') {
      // For email, show a prompt
      alert(`📧 Report would be emailed. API ready at: ${result.download_url}`);
    } else {
      // For PDF/HTML, show download message
      alert(`✓ ${format.toUpperCase()} export ready!\n\nDownload: ${result.download_url}`);
    }

  } catch (err) {
    console.error('Export failed:', err);
    alert(`Export failed: ${err.message}`);
  }
}
