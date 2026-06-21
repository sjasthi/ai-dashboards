/**
 * state.js — single source of truth for app state.
 *
 * Import this wherever you need to read or write state.
 * Never duplicate state in individual modules.
 */
export const state = {
  currentPage:    'upload',
  files:          [],       // Array of { name, rows, sheets }
  selectedReport: null,     // 'A' | 'B' | 'C' | null
  isLoggedIn:     false,
  user:           null,     // email string when logged in
};
