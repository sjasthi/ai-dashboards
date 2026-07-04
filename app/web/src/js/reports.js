/**
 * reports.js — report type selection and generate button state.
 */
import { state } from './state.js';
import { analyzeData, getResults } from './api.js';

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

/**
 * Populate the three report option cards with AI-recommended reports.
 * @param {Array} reportOptions - Array of {rank, report_name, question_answered, ...}
 */
function populateReportCards(reportOptions) {
  const labels = ['A', 'B', 'C'];

  labels.forEach((label, i) => {
    const rec = reportOptions[i];
    if (!rec) return;

    const card = document.getElementById(`option-${label}`);
    if (!card) return;

    // Update title
    const titleEl = card.querySelector('.report-card__title');
    if (titleEl) titleEl.textContent = rec.report_name || `Report Option ${label}`;

    // Build rationale lines — prefer rationale_bullets from AI, fall back to derived info
    const rationale = card.querySelector('.report-card__rationale');
    if (rationale) {
      let lines = [];
      if (Array.isArray(rec.rationale_bullets) && rec.rationale_bullets.length) {
        lines = rec.rationale_bullets.slice(0, 3);
      } else {
        if (rec.question_answered) lines.push(rec.question_answered);
        if (rec.required_operations?.length) {
          const opTypes = [...new Set(rec.required_operations.map(o => o.operation_type).filter(Boolean))];
          if (opTypes.length) lines.push(`Operations: ${opTypes.join(', ')}`);
        }
        if (rec.plotly_config?.chart_type) {
          lines.push(`Visualisation: ${rec.plotly_config.chart_type} chart`);
        }
      }
      rationale.innerHTML = lines
        .map(l => `<div class="rationale-line">${l}</div>`)
        .join('');
    }
  });
}

/**
 * Display analysis results on the analysis page.
 * Called after analyze-full completes.
 */
export function displayAnalysisResults(result) {
  const payload = result.recommendations || result.analysis || {};

  // The AI returns { recommendations: [ {rank, report_name, ...}, ... ] }
  const reportOptions = Array.isArray(payload.recommendations)
    ? payload.recommendations
    : [];

  // Store on state so generateReport() can pass the right config
  state.reportOptions = reportOptions;

  if (reportOptions.length > 0) {
    populateReportCards(reportOptions);
  }
}

/**
 * Generate a report by calling the backend API.
 * Displays results and navigates to results page.
 */
export async function generateReport() {
  if (!state.sessionId) {
    alert('No analysis yet. Please analyze files first.');
    return;
  }

  if (!state.selectedReport) {
    alert('Please select a report type');
    return;
  }

  const generateBtn = document.getElementById('generateBtn');

  try {
    state.isAnalyzing = true;
    generateBtn.disabled = true;
    generateBtn.textContent = '⏳ Generating...';

    // Call backend to analyze
    const result = await analyzeData(state.sessionId, state.selectedReport);
    state.analysisResult = result;

    generateBtn.textContent = '✓ Generated!';

    // Display results
    displayAnalysisResults(result);

    // Navigate to results page
    document.getElementById('page-analysis').classList.remove('visible');
    document.getElementById('page-reports').classList.add('visible');

    // Highlight the results section
    document.getElementById('nav-reports').classList.add('active');

  } catch (err) {
    console.error('Generate failed:', err);
    generateBtn.textContent = '✕ Generate failed';
    alert(`Failed to generate report: ${err.message}`);
  } finally {
    state.isAnalyzing = false;
    generateBtn.disabled = false;
    generateBtn.textContent = 'Generate report →';
  }
}
