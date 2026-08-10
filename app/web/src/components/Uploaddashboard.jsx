import { useEffect, useRef, useState } from 'react';
import { analyzeFull, inspectFiles, sendEvents } from '../api';
import { isNewlyActiveUser } from '../clientId';
import { exactNumber, formatBytes, truncateMiddle } from '../format';

// This file used to declare its own `const API_BASE` and build its own fetches, so
// the backend URL was defined in two places and the two heaviest endpoints in the
// app were the only ones not going through api.js. Adding the X-Client-Id header
// would have meant remembering both. Now there is one definition and one place a
// request header has to be added.

const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;

// inspections: fileKey -> what /api/inspect said about the file (sheets, rows, size).
// selections:  fileKey -> Set of sheet names to analyze. Absent until the probe answers.
// expanded:    fileKeys whose worksheet checkboxes are currently on screen.
// All three live in App, not here - this page unmounts on every tab switch and they
// have to outlive it.
export default function UploadDashboard({
  files, setFiles,
  inspections, setInspections,
  selections, setSelections,
  expanded, setExpanded,
  setSessionId, setRecommendations, setFileMetadata, onStart, onDone,
}) {
  const [status, setStatus] = useState('idle'); // idle | uploading | error | done
  const [errorMsg, setErrorMsg] = useState(null);
  const [confirmingRemoveAll, setConfirmingRemoveAll] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [noticeMsg, setNoticeMsg] = useState(null);

  /** Ask the server what is inside these files. Only ever called with files that
   *  aren't already known, so re-picking an existing file costs nothing. */
  const probeFiles = async (newFiles) => {
    setInspecting(true);

    try {
      const data = await inspectFiles(newFiles);
      // The endpoint answers in request order, which is how each result finds its
      // way back to the File it describes.
      const byKey = {};
      newFiles.forEach((file, i) => {
        if (data.files[i]) byKey[fileKey(file)] = data.files[i];
      });

      setInspections((prev) => ({ ...prev, ...byKey }));
      setSelections((prev) => {
        const next = { ...prev };
        Object.entries(byKey).forEach(([key, info]) => {
          // Everything on by default; an empty sheet is never loaded, so leaving
          // it selected would misreport what is about to be analyzed.
          next[key] = new Set((info.sheets || []).filter((s) => !s.empty).map((s) => s.name));
        });
        return next;
      });
    } catch (err) {
      // Not fatal: the files stay listed and analysis still works, it just runs
      // over every sheet the way it did before this screen could ask. Mark them
      // failed rather than leaving the rows reading "Reading…" with nothing on the
      // way to replace it; the banner carries the detail.
      setInspections((prev) => {
        const next = { ...prev };
        newFiles.forEach((file) => {
          if (!next[fileKey(file)]) next[fileKey(file)] = { error: 'Could not be read.', sheets: [] };
        });
        return next;
      });
      setNoticeMsg(err.message || 'Could not read the worksheets in these files.');
    } finally {
      setInspecting(false);
    }
  };

  const handleFileUpload = (event) => {
    const picked = Array.from(event.target.files);
    // Clearing the input lets the same file fire onChange again after a removal.
    event.target.value = '';

    const seenKeys = new Set(files.map(fileKey));
    const seenNames = new Set(files.map((f) => f.name));
    const added = [];
    const duplicateNames = [];

    for (const file of picked) {
      if (seenKeys.has(fileKey(file))) continue;
      if (seenNames.has(file.name)) {
        // The server writes every upload into one directory keyed by filename, so
        // two files sharing a name would overwrite each other - and `selections`
        // is keyed by name too, so it could not tell them apart either.
        duplicateNames.push(file.name);
        continue;
      }
      seenKeys.add(fileKey(file));
      seenNames.add(file.name);
      added.push(file);
    }

    if (added.length) setFiles((prev) => [...prev, ...added]);
    setNoticeMsg(duplicateNames.length
      ? `Skipped ${duplicateNames.join(', ')} — a file with that name is already listed.`
      : null);
    setStatus('idle');
    setErrorMsg(null);
    setConfirmingRemoveAll(false);
    if (added.length) probeFiles(added);
  };

  /** Drop everything remembered about a file that is no longer listed. */
  const forget = (keys) => {
    const gone = new Set(keys);
    const without = (obj) => Object.fromEntries(
      Object.entries(obj).filter(([key]) => !gone.has(key))
    );
    setInspections(without);
    setSelections(without);
    setExpanded((prev) => new Set([...prev].filter((key) => !gone.has(key))));
  };

  const removeFile = (index) => {
    const target = files[index];
    const remaining = files.filter((_, i) => i !== index);
    setFiles(remaining);
    if (target) forget([fileKey(target)]);
    if (remaining.length === 0) setConfirmingRemoveAll(false);
    setNoticeMsg(null);
  };

  const removeAllFiles = () => {
    setFiles([]);
    forget(files.map(fileKey));
    setStatus('idle');
    setErrorMsg(null);
    setNoticeMsg(null);
    setConfirmingRemoveAll(false);
  };

  const toggleSheet = (key, sheetName) => {
    setSelections((prev) => {
      const next = new Set(prev[key] || []);
      if (next.has(sheetName)) next.delete(sheetName); else next.add(sheetName);
      return { ...prev, [key]: next };
    });
  };

  const setEverySheet = (key, on) => {
    const selectable = (inspections[key]?.sheets || []).filter((s) => !s.empty);
    setSelections((prev) => ({
      ...prev,
      [key]: new Set(on ? selectable.map((s) => s.name) : []),
    }));
  };

  /**
   * Everything the row and the totals need about one file. Row counts are summed
   * from the sheets that are actually checked, so the figure on screen always
   * describes the run the button would start.
   */
  const statsFor = (file) => {
    const key = fileKey(file);
    const info = inspections[key];
    if (!info) return { key, pending: true, included: true };
    if (info.error) return { key, error: info.error, included: !info.empty };

    const sheets = info.sheets || [];
    if (!sheets.length) {
      // A CSV holds exactly one table and has no sheets to choose between.
      return { key, isCsv: true, included: true, sheetsTotal: 1, sheetsOn: 1, rows: info.rows, rowsTotal: info.rows };
    }

    const chosen = selections[key] || new Set();
    const selectable = sheets.filter((s) => !s.empty);
    const on = selectable.filter((s) => chosen.has(s.name));
    return {
      key,
      sheets,
      selectable,
      sheetsTotal: selectable.length,
      sheetsOn: on.length,
      rows: on.reduce((sum, s) => sum + s.rows, 0),
      rowsTotal: selectable.reduce((sum, s) => sum + s.rows, 0),
      included: on.length > 0,
    };
  };

  const stats = files.map(statsFor);
  const included = files.filter((_, i) => stats[i].included);
  const totals = stats.reduce((acc, s, i) => {
    if (!s.included) return acc;
    acc.files += 1;
    acc.size += files[i].size;
    acc.sheetsOn += s.sheetsOn || 0;
    acc.rows += s.rows || 0;
    return acc;
  }, { files: 0, size: 0, sheetsOn: 0, rows: 0 });
  const sheetsTotal = stats.reduce((sum, s) => sum + (s.sheetsTotal || 0), 0);

  const runAnalysis = async () => {
    if (!included.length) return;

    setStatus('uploading');
    setErrorMsg(null);
    // Reports cached from the previous session describe data that is about to be
    // replaced - clear them before the new session_id lands.
    if (onStart) onStart();

    const sheetSelections = {};
    included.forEach((file) => {
      const info = inspections[fileKey(file)];
      // Only workbooks get an entry. A file the probe couldn't read is left out
      // so the server falls back to loading all of it rather than nothing.
      if (info && !info.error && (info.sheets || []).length) {
        sheetSelections[file.name] = Array.from(selections[fileKey(file)] || []);
      }
    });

    try {
      const data = await analyzeFull(
        included,
        Object.keys(sheetSelections).length ? sheetSelections : null,
      );

      setSessionId(data.session_id);
      setRecommendations(data.recommendations);
      setFileMetadata(data.file_metadata);
      setStatus('done');

      // Only now does this browser session count as a user rather than a visitor.
      // Fired here, on the success path, so a failed analysis doesn't promote
      // someone who never got a result. sendEvents swallows its own failures.
      if (isNewlyActiveUser()) {
        sendEvents([{ event: 'user_activated', session_id: data.session_id }]);
      }

      if (onDone) onDone();
    } catch (err) {
      setStatus('error');
      setErrorMsg(err.message || 'Something went wrong while analyzing your files.');
    }
  };

  // Remove All only needs a list to clear; analysis also needs the probe to have
  // finished and at least one sheet still checked.
  const analyzing = status === 'uploading';
  const actionsDisabled = !files || files.length === 0 || analyzing;
  const analyzeDisabled = actionsDisabled || inspecting || included.length === 0;

  return (
    <div className="upload-page">
      <div style={{ color: '#2563eb', fontWeight: 700, fontSize: '13px', letterSpacing: '0.05em', marginBottom: '20px' }}>
        STEP 1 — UPLOAD
      </div>

      <div style={{
        background: 'white',
        border: '1px solid #e2e8f0',
        borderRadius: '10px',
        padding: '32px',
        // No max-width: the page wrapper already constrains this to the shared
        // centred column, and a second cap here left the card narrower than the
        // column it sits in, so Upload looked misaligned next to every other tab.
      }}>
        <h2 style={{ margin: '0 0 8px 0', fontSize: '18px', color: '#1e293b' }}>Upload Data</h2>
        <p style={{ margin: '0 0 20px 0', fontSize: '14px', color: '#64748b' }}>
          Upload one or more CSV or Excel files to begin analysis.
        </p>

        <label style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '8px',
          border: '2px dashed #cbd5e1',
          borderRadius: '8px',
          padding: '32px',
          cursor: 'pointer',
          background: '#f8fafc'
        }}>
          <input
            type="file"
            multiple
            accept=".csv,.xlsx,.xls"
            onChange={handleFileUpload}
            style={{ display: 'none' }}
          />
          <span style={{ fontSize: '14px', fontWeight: 600, color: '#2563eb' }}>Click to choose files</span>
          <span style={{ fontSize: '12px', color: '#94a3b8' }}>.csv, .xlsx, .xls</span>
        </label>

        {files && files.length > 0 && (
          <div style={{ marginTop: '20px' }}>
            {files.map((file, i) => (
              <FileRow
                key={fileKey(file)}
                file={file}
                stats={stats[i]}
                selected={selections[fileKey(file)]}
                expanded={expanded.has(fileKey(file))}
                onToggleExpand={() => setExpanded((prev) => {
                  const next = new Set(prev);
                  const key = fileKey(file);
                  if (next.has(key)) next.delete(key); else next.add(key);
                  return next;
                })}
                onToggleSheet={(name) => toggleSheet(fileKey(file), name)}
                onSetEvery={(on) => setEverySheet(fileKey(file), on)}
                onRemove={() => removeFile(i)}
              />
            ))}
          </div>
        )}

        {noticeMsg && (
          <div style={{ marginTop: '12px', padding: '10px 14px', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: '6px', fontSize: '13px', color: '#92400e' }}>
            {noticeMsg}
          </div>
        )}

        {status === 'error' && (
          <div style={{ marginTop: '16px', padding: '12px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '6px', fontSize: '13px', color: '#b91c1c' }}>
            {errorMsg}
          </div>
        )}

        {/* Sits above the action row, not inside it: the Remove All confirmation
            covers that row, and this summary should stay visible while it does. */}
        {files && files.length > 0 && (
          <div style={{ marginTop: '16px', fontSize: '13px', color: '#64748b' }}>
            {inspecting ? 'Reading worksheets…' : [
              totals.files === files.length
                ? `${totals.files} ${totals.files === 1 ? 'file' : 'files'}`
                : `${totals.files} of ${files.length} files`,
              sheetsTotal > 0 && (totals.sheetsOn === sheetsTotal
                ? `${sheetsTotal} ${sheetsTotal === 1 ? 'sheet' : 'sheets'}`
                : `${totals.sheetsOn} of ${sheetsTotal} sheets`),
              `${exactNumber(totals.rows)} rows`,
              formatBytes(totals.size),
            ].filter(Boolean).join(' · ')}
          </div>
        )}

        {/* The action row and the prompt share one slot: the prompt lays over the
            buttons so the card keeps its height and the buttons stay out of the way
            until the question is answered. */}
        <div style={{ marginTop: '20px', position: 'relative' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            // `hidden` rather than unmounting: it holds the row's space open under
            // the prompt, and drops the buttons out of the tab order meanwhile.
            visibility: confirmingRemoveAll ? 'hidden' : 'visible'
          }}>
            {/* Busy is not the same as unavailable: while analysing, the button
                keeps its blue so the spinner and label stay legible. Greying it
                out left white text on #cbd5e1, which was barely readable. */}
            <button
              onClick={runAnalysis}
              disabled={analyzeDisabled}
              aria-busy={analyzing}
              style={{
                padding: '10px 24px',
                background: analyzing ? '#2563eb' : analyzeDisabled ? '#cbd5e1' : '#2563eb',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: 600,
                cursor: analyzeDisabled ? 'not-allowed' : 'pointer'
              }}
            >
              {analyzing && <span className="btn-spinner" aria-hidden="true" />}
              {analyzing ? 'Analyzing…' : 'Upload & Analyze'}
            </button>

            <button
              onClick={() => setConfirmingRemoveAll(true)}
              disabled={actionsDisabled}
              style={{
                padding: '10px 24px',
                background: actionsDisabled ? '#f8fafc' : '#fef2f2',
                color: actionsDisabled ? '#cbd5e1' : '#b91c1c',
                border: `1px solid ${actionsDisabled ? '#e2e8f0' : '#fecaca'}`,
                borderRadius: '6px',
                fontSize: '14px',
                fontWeight: 600,
                cursor: actionsDisabled ? 'not-allowed' : 'pointer'
              }}
            >
              Remove All
            </button>
          </div>

          {confirmingRemoveAll && (
            <div
              role="alertdialog"
              aria-label="Confirm removing all files"
              style={{
                // Centred on the row it covers: the prompt is a little taller than
                // the buttons, so it spills evenly rather than shifting the card.
                position: 'absolute',
                left: 0,
                right: 0,
                top: '50%',
                transform: 'translateY(-50%)',
                padding: '12px 14px',
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '12px',
                flexWrap: 'wrap'
              }}
            >
              <span style={{ fontSize: '13px', color: '#b91c1c' }}>
                {files.length === 1
                  ? 'Remove this file from the list?'
                  : `Remove all ${files.length} files from the list?`}
              </span>
              <div style={{ display: 'flex', gap: '8px' }}>
                {/* Cancel takes focus so a stray Enter backs out rather than deleting. */}
                <button
                  autoFocus
                  onClick={() => setConfirmingRemoveAll(false)}
                  style={{
                    padding: '7px 14px',
                    background: 'white',
                    color: '#475569',
                    border: '1px solid #e2e8f0',
                    borderRadius: '6px',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer'
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={removeAllFiles}
                  style={{
                    padding: '7px 14px',
                    background: '#b91c1c',
                    color: 'white',
                    border: 'none',
                    borderRadius: '6px',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer'
                  }}
                >
                  Yes, remove all
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/**
 * Everything you can do to one listed file, behind a single button. A menu rather
 * than visible controls, which competed with the file name for attention on every
 * row while being wanted only occasionally. Which worksheet actions appear is the
 * caller's decision - see FileRow.
 */
function FileMenu({ showSelectAll, showClear, allOn, noneOn, onSelectAll, onClear, onRemove }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    // A menu that only closes by picking something traps the user, and several
    // rows can have one - clicking a second must not leave the first hanging open.
    const onPointerDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKeyDown = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const choose = (action) => { action(); setOpen(false); };

  return (
    <div className="sheet-menu" ref={ref}>
      <button
        className="sheet-menu__button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="File options"
        onClick={() => setOpen((v) => !v)}
      >
        ⋮
      </button>

      {open && (
        <div className="sheet-menu__panel" role="menu">
          {/* Disabled rather than hidden while the sheets are on screen, so the
              menu doesn't shift under the cursor as boxes are ticked. */}
          {showSelectAll && (
            <button className="sheet-menu__item" role="menuitem" disabled={allOn}
                    onClick={() => choose(onSelectAll)}>
              Select all sheets
            </button>
          )}
          {showClear && (
            <button className="sheet-menu__item" role="menuitem" disabled={noneOn}
                    onClick={() => choose(onClear)}>
              Clear selection
            </button>
          )}
          {(showSelectAll || showClear) && <div className="sheet-menu__divider" role="separator" />}
          {/* Last, and after a rule: it discards the file rather than adjusting it. */}
          <button className="sheet-menu__item" role="menuitem"
                  onClick={() => choose(onRemove)}>
            Remove file
          </button>
        </div>
      )}
    </div>
  );
}


/** One listed file: its name, what is inside it, and its worksheet checkboxes. */
function FileRow({ file, stats, selected, expanded, onToggleExpand, onToggleSheet, onSetEvery, onRemove }) {
  const sheets = stats.sheets || [];
  // A CSV has no sheets and a one-sheet workbook offers no choice, so a checkbox
  // there could not change the outcome. Remove is how you exclude those.
  const showSheets = sheets.length > 1;
  const allOn = stats.sheetsOn === stats.sheetsTotal;

  const meta = () => {
    if (stats.pending) return 'Reading…';
    if (stats.error) return stats.error;

    const parts = [formatBytes(file.size)];
    if (stats.isCsv) {
      parts.push('1 sheet');
    } else if (allOn) {
      parts.push(`${stats.sheetsTotal} ${stats.sheetsTotal === 1 ? 'sheet' : 'sheets'}`);
    } else {
      parts.push(`${stats.sheetsOn} of ${stats.sheetsTotal} sheets`);
    }
    parts.push(`${exactNumber(stats.rows)} rows`);
    return parts.join(' · ');
  };

  return (
    <div style={{
      padding: '12px 14px',
      background: '#f8fafc',
      border: '1px solid #e2e8f0',
      borderRadius: '6px',
      marginBottom: '8px',
      fontSize: '14px',
      color: '#334155'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {file.name}
        </span>
        {/* Every file gets one, so the button forms a fixed column down the list.

            Expanded, both actions show and grey out when they would do nothing.
            Collapsed, only "Select all sheets", and only when some sheet is left
            out: it is the one-click way back to the whole workbook. Clearing is
            not worth offering against checkboxes that aren't on screen. */}
        <FileMenu
          showSelectAll={showSheets && (expanded || !allOn)}
          showClear={showSheets && expanded}
          allOn={allOn}
          noneOn={stats.sheetsOn === 0}
          onSelectAll={() => onSetEvery(true)}
          onClear={() => onSetEvery(false)}
          onRemove={onRemove}
        />
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '12px',
        marginTop: '2px',
        fontSize: '12px',
        color: stats.error ? '#b91c1c' : '#64748b'
      }}>
        <span>{meta()}</span>
        {showSheets && (
          <button className="sheet-toggle" onClick={onToggleExpand} aria-expanded={expanded}>
            {expanded ? 'Hide sheets' : 'Choose sheets'}
            <span aria-hidden="true">{expanded ? '▴' : '▾'}</span>
          </button>
        )}
      </div>

      {showSheets && expanded && (
        <div className="sheet-grid">
          {sheets.map((sheet) => (
            <label
              key={sheet.name}
              className={`sheet-chip${sheet.empty ? ' sheet-chip--empty' : ''}`}
              title={sheet.empty
                ? `${sheet.name} — empty, so it is not analyzed`
                : `${sheet.name} — ${exactNumber(sheet.rows)} rows × ${sheet.columns} columns`}
            >
              <input
                type="checkbox"
                checked={!sheet.empty && !!selected?.has(sheet.name)}
                disabled={sheet.empty}
                onChange={() => onToggleSheet(sheet.name)}
              />
              <span className="sheet-chip__name">
                {truncateMiddle(sheet.name)}{sheet.empty ? ' (empty)' : ''}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
