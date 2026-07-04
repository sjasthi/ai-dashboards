/**
 * state.js — single source of truth for app state.
 *
 * Import this wherever you need to read or write state.
 * Never duplicate state in individual modules.
 */
export const state = {
  currentPage:    'upload',
  files:          [],       // Array of { name, rows, sheets }
  fileObjects:    [],       // Array of actual File objects (for uploading)
  reportOptions:  [],       // AI-recommended report objects [{rank, report_name, question_answered, ...}]
  sessionId:      null,     // Session ID from backend after upload
  selectedReport: null,     // 'A' | 'B' | 'C' | null
  analysisResult: null,     // Analysis data from backend
  isLoggedIn:     false,
  user:           null,     // email string when logged in
  isUploading:    false,    // Track upload progress
  isAnalyzing:    false,    // Track analysis progress
};
