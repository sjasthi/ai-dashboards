/**
 * main.js — app entry point.
 *
 * Imports all CSS (Vite bundles it) and all JS modules,
 * then wires up event listeners once the DOM is ready.
 */

// ---- CSS imports (Vite processes and injects these) ---------------------
import './css/tokens.css';
import './css/base.css';
import './css/navbar.css';
import './css/footer.css';
import './css/upload.css';
import './css/analysis.css';
import './css/results.css';
import './css/settings.css';

// ---- JS module imports --------------------------------------------------
import { navigate }                                         from './js/router.js';
import { addFiles, handleDrop, handleDragOver,
         handleDragLeave, handleFileInput, analyzeFiles }   from './js/files.js';
import { selectReport, generateReport }                     from './js/reports.js';
import { handleExport }                                     from './js/export.js';
import { toggleUserMenu, closeUserMenu }                    from './js/user.js';

// ---- Wire up after DOM is ready -----------------------------------------
document.addEventListener('DOMContentLoaded', () => {

  // Navbar links
  document.getElementById('nav-upload')
    ?.addEventListener('click', () => navigate('upload'));
  document.getElementById('nav-analysis')
    ?.addEventListener('click', () => navigate('analysis'));
  document.getElementById('nav-reports')
    ?.addEventListener('click', () => navigate('reports'));
  document.getElementById('nav-settings')
    ?.addEventListener('click', () => navigate('settings'));

  // User menu
  document.getElementById('userBtn')
    ?.addEventListener('click', toggleUserMenu);

  // Close user menu on outside click
  document.addEventListener('click', e => {
    if (!document.querySelector('.navbar__user')?.contains(e.target)) {
      closeUserMenu();
    }
  });

  // Drop zone
  const dropzone = document.getElementById('dropzone');
  dropzone?.addEventListener('click',      () => document.getElementById('fileInput').click());
  dropzone?.addEventListener('dragover',   handleDragOver);
  dropzone?.addEventListener('dragleave',  handleDragLeave);
  dropzone?.addEventListener('drop',       handleDrop);

  // File input (hidden)
  document.getElementById('fileInput')
    ?.addEventListener('change', handleFileInput);

  // Analyze button → full analysis workflow
  document.getElementById('analyzeBtn')
    ?.addEventListener('click', analyzeFiles);

  // Report option cards
  ['A', 'B', 'C'].forEach(opt => {
    document.getElementById(`select-${opt}`)
      ?.addEventListener('click', () => selectReport(opt));
  });

  // Generate report button → call API then navigate to results
  document.getElementById('generateBtn')
    ?.addEventListener('click', generateReport);

  // Export buttons
  document.querySelectorAll('[data-export-type][data-export-format]').forEach(btn => {
    btn.addEventListener('click', () =>
      handleExport(btn.dataset.exportType, btn.dataset.exportFormat)
    );
  });
});
