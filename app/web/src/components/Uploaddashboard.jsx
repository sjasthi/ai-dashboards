import { useState } from 'react';

const API_BASE = 'http://localhost:8000';

const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;

export default function UploadDashboard({ files, setFiles, setSessionId, setRecommendations, setFileProfiles, onStart, onDone }) {
  const [status, setStatus] = useState('idle'); // idle | uploading | error | done
  const [errorMsg, setErrorMsg] = useState(null);
  const [confirmingRemoveAll, setConfirmingRemoveAll] = useState(false);

  const handleFileUpload = (event) => {
    const uploadedFiles = Array.from(event.target.files);
    setFiles((prev) => {
      const seen = new Set(prev.map(fileKey));
      return [...prev, ...uploadedFiles.filter((file) => !seen.has(fileKey(file)))];
    });
    setStatus('idle');
    setErrorMsg(null);
    setConfirmingRemoveAll(false);
    event.target.value = '';
  };

  const removeFile = (index) => {
    const remaining = files.filter((_, i) => i !== index);
    setFiles(remaining);
    if (remaining.length === 0) setConfirmingRemoveAll(false);
  };

  const removeAllFiles = () => {
    setFiles([]);
    setStatus('idle');
    setErrorMsg(null);
    setConfirmingRemoveAll(false);
  };

  const runAnalysis = async () => {
    if (!files || files.length === 0) return;

    setStatus('uploading');
    setErrorMsg(null);
    // Reports cached from the previous session describe data that is about to be
    // replaced - clear them before the new session_id lands.
    if (onStart) onStart();

    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    try {
      const response = await fetch(`${API_BASE}/api/analyze-full`, {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail || `Request failed with status ${response.status}`);
      }

      const data = await response.json();

      setSessionId(data.session_id);
      setRecommendations(data.recommendations);
      setFileProfiles(data.file_profiles);
      setStatus('done');

      if (onDone) onDone();
    } catch (err) {
      setStatus('error');
      setErrorMsg(err.message || 'Something went wrong while analyzing your files.');
    }
  };

  const actionsDisabled = !files || files.length === 0 || status === 'uploading';
  // The confirmation prompt owns the interaction until it is answered.
  const rowDisabled = actionsDisabled || confirmingRemoveAll;

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
        maxWidth: '700px'
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
              <div key={fileKey(file)} style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '10px 14px',
                background: '#f8fafc',
                border: '1px solid #e2e8f0',
                borderRadius: '6px',
                marginBottom: '8px',
                fontSize: '14px',
                color: '#334155'
              }}>
                <span>{file.name}</span>
                <button
                  onClick={() => removeFile(i)}
                  style={{ border: 'none', background: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: '13px' }}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}

        {status === 'error' && (
          <div style={{ marginTop: '16px', padding: '12px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '6px', fontSize: '13px', color: '#b91c1c' }}>
            {errorMsg}
          </div>
        )}

        {confirmingRemoveAll && (
          <div style={{
            marginTop: '16px',
            padding: '12px 14px',
            background: '#fef2f2',
            border: '1px solid #fecaca',
            borderRadius: '6px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
            flexWrap: 'wrap'
          }}>
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

        <div style={{ marginTop: '20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button
            onClick={runAnalysis}
            disabled={rowDisabled}
            style={{
              padding: '10px 24px',
              background: rowDisabled ? '#cbd5e1' : '#2563eb',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              fontSize: '14px',
              fontWeight: 600,
              cursor: rowDisabled ? 'not-allowed' : 'pointer'
            }}
          >
            {status === 'uploading' ? 'Analyzing…' : 'Upload & Analyze'}
          </button>

          <button
            onClick={() => setConfirmingRemoveAll(true)}
            disabled={rowDisabled}
            style={{
              padding: '10px 24px',
              background: rowDisabled ? '#f8fafc' : '#fef2f2',
              color: rowDisabled ? '#cbd5e1' : '#b91c1c',
              border: `1px solid ${rowDisabled ? '#e2e8f0' : '#fecaca'}`,
              borderRadius: '6px',
              fontSize: '14px',
              fontWeight: 600,
              cursor: rowDisabled ? 'not-allowed' : 'pointer'
            }}
          >
            Remove All
          </button>
        </div>
      </div>
    </div>
  );
}
