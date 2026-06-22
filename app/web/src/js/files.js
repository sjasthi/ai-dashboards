/**
 * files.js — drag & drop, file parsing, and file list rendering.
 *
 * Replace parseFileMeta() with real SheetJS parsing when ready.
 */
import { state } from './state.js';
import { escHtml } from './utils.js';

// ---- File parsing -------------------------------------------------------

/**
 * Generates placeholder row/sheet metadata for a File object.
 * TODO: replace with SheetJS (xlsx) for real counts:
 *   import * as XLSX from 'xlsx';
 *   const wb = XLSX.read(await file.arrayBuffer());
 *   const sheets = wb.SheetNames.length;
 *   const rows   = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]]).length;
 */
function parseFileMeta(file) {
  const ext       = file.name.split('.').pop().toLowerCase();
  const fakeRows  = Math.floor(Math.random() * 20000) + 500;
  const fakeSheets = ext === 'csv' ? 1 : Math.floor(Math.random() * 4) + 1;
  return { name: file.name, rows: fakeRows, sheets: fakeSheets };
}

// ---- Add / remove files -------------------------------------------------

const ALLOWED_EXTS = ['xlsx', 'xls', 'csv'];

export function addFiles(fileList) {
  Array.from(fileList).forEach(file => {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_EXTS.includes(ext)) return;
    if (state.files.find(f => f.name === file.name)) return; // no duplicates
    state.files.push(parseFileMeta(file));
  });
  renderFileList();
}

export function removeFile(index) {
  state.files.splice(index, 1);
  renderFileList();
}

// ---- Render -------------------------------------------------------------

export function renderFileList() {
  const list        = document.getElementById('fileList');
  const bar         = document.getElementById('analyzeBar');
  const navAnalysis = document.getElementById('nav-analysis');

  // Render file items
  list.innerHTML = state.files.map((f, i) => `
    <li class="file-item">
      <div class="file-item__info">
        <div class="file-item__icon">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M4 1h5l4 4v10H4V1z"/>
            <path d="M9 1v4h4"/>
          </svg>
        </div>
        <div>
          <div class="file-item__name">${escHtml(f.name)}</div>
          <div class="file-item__meta">
            ${f.rows.toLocaleString()} rows · ${f.sheets} sheet${f.sheets !== 1 ? 's' : ''}
          </div>
        </div>
      </div>
      <button class="btn btn-ghost" data-remove="${i}">✕ Remove</button>
    </li>
  `).join('');

  // Attach remove handlers (event delegation avoids inline onclick)
  list.querySelectorAll('[data-remove]').forEach(btn => {
    btn.addEventListener('click', () => removeFile(Number(btn.dataset.remove)));
  });

  // Update analyze bar
  if (state.files.length > 0) {
    const totalRows   = state.files.reduce((a, f) => a + f.rows,   0);
    const totalSheets = state.files.reduce((a, f) => a + f.sheets, 0);
    const n = state.files.length;

    document.getElementById('analyzeCount').textContent =
      `${n} file${n !== 1 ? 's' : ''} ready · ${totalRows.toLocaleString()} total rows · ${totalSheets} sheet${totalSheets !== 1 ? 's' : ''}`;
    document.getElementById('analyzeDetail').textContent =
      'Add more files or continue to analysis';

    bar.style.display = 'flex';
    navAnalysis.classList.remove('disabled');
  } else {
    bar.style.display = 'none';
    navAnalysis.classList.add('disabled');
  }
}

// ---- Drag & drop event handlers -----------------------------------------

export function handleDragOver(e) {
  e.preventDefault();
  document.getElementById('dropzone').classList.add('dragover');
}

export function handleDragLeave() {
  document.getElementById('dropzone').classList.remove('dragover');
}

export function handleDrop(e) {
  e.preventDefault();
  handleDragLeave();
  addFiles(e.dataTransfer.files);
}

export function handleFileInput(e) {
  addFiles(e.target.files);
  e.target.value = ''; // reset so the same file can be re-added after removal
}
