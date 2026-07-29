import React, { useState } from 'react';
import UploadDashboard from './components/Uploaddashboard';
import AnalysisDashboard from './components/Analysisdashboard';
import ReportsDashboard from './components/Reportsdashboard';
import SettingsDashboard from './components/Settingsdashboard';

export default function App() {
  const [activeTab, setActiveTab] = useState('upload');

  // Raw File objects picked in the Upload tab
  const [files, setFiles] = useState([]);

  // Populated once /api/analyze-full succeeds
  const [sessionId, setSessionId] = useState(null);
  const [recommendations, setRecommendations] = useState(null); // { recommendations: [...] }
  const [fileProfiles, setFileProfiles] = useState(null);

  // Populated once /api/generate-report succeeds (user picked a recommendation)
  const [report, setReport] = useState(null);

  const getTabStyle = (tab) => ({
    padding: '10px 4px',
    cursor: 'pointer',
    color: activeTab === tab ? '#2563eb' : '#64748b',
    fontWeight: activeTab === tab ? 600 : 500,
    fontSize: '15px',
    borderBottom: activeTab === tab ? '2px solid #2563eb' : '2px solid transparent',
    transition: 'color 0.15s ease, border-color 0.15s ease'
  });

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#f8fafc', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* NAVBAR */}
      <nav style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 32px',
        background: 'white',
        borderBottom: '1px solid #e2e8f0'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{
            width: '34px',
            height: '34px',
            borderRadius: '8px',
            background: '#2563eb',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="12" width="4" height="9" rx="1" fill="white" />
              <rect x="10" y="7" width="4" height="14" rx="1" fill="white" />
              <rect x="17" y="3" width="4" height="18" rx="1" fill="white" />
            </svg>
          </div>
          <span style={{ fontWeight: 700, fontSize: '1.25rem', color: '#1e293b' }}>
            AI-Dashboard
          </span>
        </div>

        <div style={{ display: 'flex', gap: '32px' }}>
          {['upload', 'analysis', 'reports', 'settings'].map(tab => (
            <div key={tab} style={getTabStyle(tab)} onClick={() => setActiveTab(tab)}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </div>
          ))}
        </div>

        <button style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 16px',
          background: 'white',
          border: '1px solid #cbd5e1',
          borderRadius: '6px',
          fontSize: '14px',
          fontWeight: 600,
          color: '#1e293b',
          cursor: 'pointer'
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="8" r="4" stroke="#1e293b" strokeWidth="2" />
            <path d="M4 20c0-4 4-6 8-6s8 2 8 6" stroke="#1e293b" strokeWidth="2" />
          </svg>
          Sign in
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
            <path d="M6 9l6 6 6-6" stroke="#64748b" strokeWidth="2" />
          </svg>
        </button>
      </nav>

      <main style={{ padding: '32px 40px' }}>
        {activeTab === 'upload' && (
          <UploadDashboard
            files={files}
            setFiles={setFiles}
            setSessionId={setSessionId}
            setRecommendations={setRecommendations}
            setFileProfiles={setFileProfiles}
            onDone={() => setActiveTab('analysis')}
          />
        )}
        {activeTab === 'analysis' && (
          <AnalysisDashboard
            files={files}
            sessionId={sessionId}
            recommendations={recommendations}
            setReport={setReport}
            onDone={() => setActiveTab('reports')}
          />
        )}
        {activeTab === 'reports' && <ReportsDashboard report={report} sessionId={sessionId} />}
        {activeTab === 'settings' && <SettingsDashboard />}
      </main>
    </div>
  );
}