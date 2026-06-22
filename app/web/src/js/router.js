/**
 * router.js — controls which page is visible and syncs the navbar.
 */
import { state } from './state.js';

const PAGE_IDS = ['upload', 'analysis', 'reports', 'settings'];

// Maps page id → index in the .nav-link NodeList
const NAV_INDEX = { upload: 0, analysis: 1, reports: 2, settings: 3 };

/**
 * Navigate to a page by id.
 * Guards prevent skipping ahead without completing prior steps.
 */
export function navigate(page) {
  if (page === 'analysis' && state.files.length === 0) return;
  if (page === 'reports'  && !state.selectedReport)    return;

  state.currentPage = page;

  // Show/hide page sections
  PAGE_IDS.forEach(id => {
    document.getElementById(`page-${id}`)
      ?.classList.toggle('visible', id === page);
  });

  // Sync active nav link
  const links = document.querySelectorAll('.nav-link');
  links.forEach(l => l.classList.remove('active'));
  links[NAV_INDEX[page]]?.classList.add('active');
}
