/**
 * reports.js — report type selection and generate button state.
 */
import { state } from './state.js';

/**
 * Select a report option ('A', 'B', or 'C').
 * Updates card styles, button text, and enables the generate button.
 */
export function selectReport(option) {
  state.selectedReport = option;

  // Reset all cards
  ['A', 'B', 'C'].forEach(o => {
    document.getElementById(`option-${o}`)?.classList.remove('selected');
    const btn = document.getElementById(`select-${o}`);
    if (btn) btn.textContent = 'Select this report';
  });

  // Highlight chosen card
  document.getElementById(`option-${option}`)?.classList.add('selected');
  const selectedBtn = document.getElementById(`select-${option}`);
  if (selectedBtn) selectedBtn.textContent = '✓ Selected';

  // Enable generate button + unlock Reports nav link
  const generateBtn = document.getElementById('generateBtn');
  if (generateBtn) generateBtn.disabled = false;

  document.getElementById('nav-reports')?.classList.remove('disabled');
}
