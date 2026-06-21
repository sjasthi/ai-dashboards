/**
 * utils.js — small shared helpers.
 */

/**
 * Escape a string for safe HTML insertion.
 * Use whenever inserting user-controlled text into innerHTML.
 */
export function escHtml(str = '') {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
