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
 * Generate a report using the already-loaded analysis data.
 * Calls backend /api/generate-report endpoint to execute operations and build the report.
 */
export async function generateReport() {
  if (!state.analysisResult) {
    alert('No analysis yet. Please analyze files first.');
    return;
  }

  if (!state.selectedReport) {
    alert('Please select a report type first.');
    return;
  }

  if (!state.sessionId) {
    alert('No session ID. Please upload and analyze files first.');
    return;
  }

  try {
    // Disable button during request
    const generateBtn = document.getElementById('generateBtn');
    if (generateBtn) generateBtn.disabled = true;

    // Call backend to generate report
    const response = await fetch('http://localhost:8000/api/generate-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.sessionId,
        report_type: state.selectedReport,
      }),
    });

    if (!response.ok) {
      throw new Error(`Backend error: ${response.status} ${response.statusText}`);
    }

    const reportData = await response.json();

    // Store report data in state for use by export/display modules
    state.reportData = reportData;

    // Find the selected recommendation object (A=index 0, B=1, C=2)
    const idx = { A: 0, B: 1, C: 2 }[state.selectedReport] ?? 0;
    const reportOptions = state.reportOptions || [];
    state.selectedReportConfig = reportOptions[idx] || null;

    renderChart(reportData);
    renderRationaleBullets(state.selectedReportConfig);

    // Navigate to reports page
    document.getElementById('page-analysis').classList.remove('visible');
    document.getElementById('page-reports').classList.add('visible');
    document.getElementById('nav-reports')?.classList.add('active');
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.getElementById('nav-reports')?.classList.add('active');
  } catch (error) {
    console.error('Report generation failed:', error);
    alert(`Failed to generate report: ${error.message}`);
  } finally {
    // Re-enable button
    const generateBtn = document.getElementById('generateBtn');
    if (generateBtn) generateBtn.disabled = false;
  }
}

/**
 * Render the AI-recommended chart (built server-side by chart_builder.py)
 * into the Reports page using Plotly.js, with the report's title.
 * @param {Object} reportData - Response from /api/generate-report
 */
function renderChart(reportData) {
  const titleEl = document.getElementById('chartTitle');
  const container = document.getElementById('chartContainer');
  const note = document.getElementById('chartNote');

  if (titleEl) titleEl.textContent = reportData.report_name || 'Chart';

  if (reportData.chart && container && window.Plotly) {
    window.Plotly.newPlot(container, reportData.chart.data, reportData.chart.layout, { responsive: true });
    if (note) note.style.display = 'none';
  } else {
    if (container) container.innerHTML = '';
    if (note) note.style.display = 'block';
  }
}

/**
 * Populate the three bullet cards with the selected report's rationale_bullets
 * (the same 3 bullets already shown on the Analysis page option card).
 * @param {Object|null} selectedRecConfig - The selected recommendation object
 */
function renderRationaleBullets(selectedRecConfig) {
  const bullets = Array.isArray(selectedRecConfig?.rationale_bullets)
    ? selectedRecConfig.rationale_bullets
    : [];

  for (let i = 0; i < 3; i++) {
    const el = document.getElementById(`bullet-${i + 1}`);
    if (el) el.textContent = bullets[i] || '';
  }
}
