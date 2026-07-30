import { useCallback, useState } from 'react';
import UploadDashboard from './components/Uploaddashboard';
import AnalysisDashboard from './components/Analysisdashboard';
import ReportsDashboard from './components/Reportsdashboard';
import SettingsDashboard from './components/Settingsdashboard';
import { REPORT_TYPE_LETTERS, generateReport } from './api';

export default function App() {
  const [activeTab, setActiveTab] = useState('upload');

  // Raw File objects picked in the Upload tab
  const [files, setFiles] = useState([]);

  // Populated once /api/analyze-full succeeds
  const [sessionId, setSessionId] = useState(null);
  const [recommendations, setRecommendations] = useState(null); // { recommendations: [...] }
  const [fileProfiles, setFileProfiles] = useState(null);

  // Generated reports, cached by report type ("A" | "B" | "C"). Held here rather
  // than in the Reports page so switching between them - or comparing all three -
  // doesn't re-POST a report the server has already built.
  const [reports, setReports] = useState({});
  const [activeReportType, setActiveReportType] = useState('A');
  const [generatingType, setGeneratingType] = useState(null);
  const [reportError, setReportError] = useState(null);

  const requestReport = useCallback(async (letter) => {
    setActiveReportType(letter);
    setReportError(null);

    // Already built: just show it.
    if (reports[letter] || !sessionId) return;

    setGeneratingType(letter);
    try {
      const data = await generateReport(sessionId, letter);
      setReports((prev) => ({ ...prev, [letter]: data }));
    } catch (err) {
      setReportError(err.message || 'Something went wrong while generating the report.');
    } finally {
      setGeneratingType(null);
    }
  }, [reports, sessionId]);

  const startNewSession = useCallback(() => {
    // A new upload invalidates every report built from the previous one.
    setReports({});
    setActiveReportType('A');
    setReportError(null);
  }, []);

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
      {/* Padding, gaps and wrapping live in dashboard.css (.app-nav) rather than here:
          inline styles beat media queries, so anything that has to change at a
          breakpoint can't be set on the element. */}
      <nav className="app-nav" style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        background: 'white',
        borderBottom: '1px solid #e2e8f0'
      }}>
        <div className="app-nav__brand" style={{ display: 'flex', alignItems: 'center' }}>
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

        <div className="app-nav__tabs" style={{ display: 'flex' }}>
          {['upload', 'analysis', 'reports', 'settings'].map(tab => (
            <div key={tab} style={getTabStyle(tab)} onClick={() => setActiveTab(tab)}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </div>
          ))}
        </div>

      </nav>

      <main className="app-main">
        {activeTab === 'upload' && (
          <UploadDashboard
            files={files}
            setFiles={setFiles}
            setSessionId={setSessionId}
            setRecommendations={setRecommendations}
            setFileProfiles={setFileProfiles}
            onStart={startNewSession}
            onDone={() => setActiveTab('analysis')}
          />
        )}
        {activeTab === 'analysis' && (
          <AnalysisDashboard
            sessionId={sessionId}
            recommendations={recommendations}
            reports={reports}
            generatingType={generatingType}
            errorMsg={reportError}
            onGenerate={async (index) => {
              await requestReport(REPORT_TYPE_LETTERS[index] || 'A');
              setActiveTab('reports');
            }}
          />
        )}
        {activeTab === 'reports' && (
          <ReportsDashboard
            sessionId={sessionId}
            reports={reports}
            activeType={activeReportType}
            onSelectType={requestReport}
            recommendations={recommendations}
            fileProfiles={fileProfiles}
            generatingType={generatingType}
            errorMsg={reportError}
          />
        )}
        {activeTab === 'settings' && <SettingsDashboard />}
      </main>
    </div>
  );
}
