/**
 * api.js — Frontend API client for communicating with FastAPI backend.
 *
 * All API calls go through this module.
 * Base URL can be changed here for different environments.
 */

const API_BASE = "http://localhost:8000";

// ============================================================================
// ANALYZE FILES (FULL WORKFLOW)
// ============================================================================

/**
 * Analyze files with full pipeline:
 * 1. Upload files
 * 2. Create summaries
 * 3. Generate recommendation prompt
 * 4. Send to AI
 * 5. Return analysis & recommendations
 * 
 * @param {File[]} files - Array of File objects
 * @returns {Promise<{session_id, status, analysis, recommendations}>}
 */
export async function analyzeFilesFull(files) {
  const formData = new FormData();
  files.forEach(file => {
    formData.append("files", file);
  });

  const response = await fetch(`${API_BASE}/api/analyze-full`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    let errorMsg = response.statusText;
    try {
      const errorData = await response.json();
      errorMsg = errorData.detail || errorMsg;
    } catch (e) {
      // If response is not JSON, use statusText
    }
    throw new Error(`Analysis failed: ${errorMsg}`);
  }

  return response.json();
}

// ============================================================================
// UPLOAD FILES
// ============================================================================

/**
 * Upload files to the backend.
 * @param {File[]} files - Array of File objects
 * @returns {Promise<{session_id, status, files}>}
 */
export async function uploadFiles(files) {
  const formData = new FormData();
  files.forEach(file => {
    formData.append("files", file);
  });

  const response = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`Upload failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// ANALYZE DATA
// ============================================================================

/**
 * Trigger analysis for a session.
 * @param {string} sessionId - Session ID from upload
 * @param {string} reportType - 'A', 'B', or 'C'
 * @returns {Promise<{session_id, report_type, status, analysis, charts}>}
 */
export async function analyzeData(sessionId, reportType = "A") {
  const response = await fetch(
    `${API_BASE}/api/analyze?session_id=${sessionId}&report_type=${reportType}`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(`Analysis failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// GET RESULTS
// ============================================================================

/**
 * Fetch analysis results for a session.
 * @param {string} sessionId - Session ID
 * @returns {Promise<{session_id, status, analysis, charts}>}
 */
export async function getResults(sessionId) {
  const response = await fetch(`${API_BASE}/api/results/${sessionId}`);

  if (!response.ok) {
    throw new Error(`Fetch failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// EXPORT REPORT
// ============================================================================

/**
 * Export analysis as PDF, HTML, or JSON.
 * @param {string} sessionId - Session ID
 * @param {string} format - 'json', 'html', or 'pdf'
 * @returns {Promise<{session_id, format, status, download_url}>}
 */
export async function exportReport(sessionId, format = "json") {
  const response = await fetch(
    `${API_BASE}/api/export/${sessionId}?format=${format}`
  );

  if (!response.ok) {
    throw new Error(`Export failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// LIST SESSIONS
// ============================================================================

/**
 * Fetch all stored sessions.
 * @returns {Promise<{sessions: [{session_id, created_at, file_count, status}], total_count}>}
 */
export async function listSessions() {
  const response = await fetch(`${API_BASE}/api/sessions`);

  if (!response.ok) {
    throw new Error(`List failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// DELETE SESSION
// ============================================================================

/**
 * Delete a session.
 * @param {string} sessionId - Session ID
 * @returns {Promise<{message}>}
 */
export async function deleteSession(sessionId) {
  const response = await fetch(`${API_BASE}/api/sessions/${sessionId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    throw new Error(`Delete failed: ${response.statusText}`);
  }

  return response.json();
}

// ============================================================================
// HEALTH CHECK
// ============================================================================

/**
 * Check if API is running.
 * @returns {Promise<boolean>} - true if API is healthy
 */
export async function isApiHealthy() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    return response.ok;
  } catch (err) {
    return false;
  }
}
