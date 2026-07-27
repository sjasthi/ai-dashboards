import React, { useEffect, useRef } from 'react';

const PLOTLY_CDN = 'https://cdn.plot.ly/plotly-2.32.0.min.js';

function loadPlotly() {
  return new Promise((resolve, reject) => {
    if (window.Plotly) {
      resolve(window.Plotly);
      return;
    }
    const existing = document.querySelector(`script[src="${PLOTLY_CDN}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve(window.Plotly));
      existing.addEventListener('error', reject);
      return;
    }
    const script = document.createElement('script');
    script.src = PLOTLY_CDN;
    script.onload = () => resolve(window.Plotly);
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

function StatCard({ label, value }) {
  return (
    <div style={{
      background: 'white',
      border: '1px solid #e2e8f0',
      borderRadius: '10px',
      padding: '20px',
      flex: 1
    }}>
      <div style={{ fontSize: '12px', fontWeight: 700, letterSpacing: '0.05em', color: '#94a3b8', marginBottom: '10px' }}>
        {label.toUpperCase()}
      </div>
      <div style={{ fontSize: '20px', fontWeight: 700, color: (value !== null && value !== undefined) ? '#1e293b' : '#cbd5e1' }}>
        {(value !== null && value !== undefined) ? value : '—'}
      </div>
    </div>
  );
}

function InsightCard({ icon, title, titleColor, children }) {
  return (
    <div style={{
      background: 'white',
      border: '1px solid #e2e8f0',
      borderRadius: '10px',
      padding: '18px'
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        fontSize: '12px',
        fontWeight: 700,
        letterSpacing: '0.05em',
        color: titleColor,
        marginBottom: '10px'
      }}>
        <span>{icon}</span>{title.toUpperCase()}
      </div>
      <div style={{ fontSize: '14px', color: '#334155', lineHeight: 1.5 }}>
        {children}
      </div>
    </div>
  );
}

function ExportCard({ title }) {
  return (
    <div style={{
      background: 'white',
      border: '1px solid #e2e8f0',
      borderRadius: '10px',
      padding: '18px',
      flex: 1
    }}>
      <div style={{ fontWeight: 700, fontSize: '15px', color: '#1e293b', marginBottom: '12px' }}>
        {title}
      </div>
      <div style={{ display: 'flex', gap: '8px' }}>
        {['PDF', 'HTML', 'Email'].map(fmt => (
          <button key={fmt} style={{
            padding: '6px 14px',
            fontSize: '13px',
            fontWeight: 600,
            color: '#334155',
            background: 'white',
            border: '1px solid #cbd5e1',
            borderRadius: '6px',
            cursor: 'pointer'
          }}>
            {fmt}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ReportsDashboard({ report }) {
  const chartRef = useRef(null);
  const hasReport = !!report;
  const chart = report?.chart;

  useEffect(() => {
    if (!chart || !chartRef.current) return;

    let cancelled = false;
    loadPlotly().then((Plotly) => {
      if (cancelled || !chartRef.current) return;
      Plotly.newPlot(chartRef.current, chart.data, {
        ...chart.layout,
        autosize: true,
        paper_bgcolor: 'white',
        plot_bgcolor: 'white'
      }, { responsive: true, displayModeBar: false });
    }).catch(() => {
      // Plotly failed to load (e.g. offline) - the placeholder text below covers this
    });

    return () => { cancelled = true; };
  }, [chart]);

  const rec = report?.sourceRecommendation;
  const stats = report?.stats;
  const chartType = chart?.data?.[0]?.type;

  return (
    <div className="reports-page">
      <div style={{ color: '#2563eb', fontWeight: 700, fontSize: '13px', letterSpacing: '0.05em', marginBottom: '20px' }}>
        STEP 3 — RESULTS
      </div>

      {/* Stat cards */}
      <div style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
        <StatCard label="Report" value={hasReport ? report.report_name : null} />
        <StatCard label="Rows" value={hasReport ? report.report_rows : null} />
        <StatCard label="Columns" value={hasReport ? report.columns?.length : null} />
        <StatCard label="Chart Type" value={hasReport ? chartType : null} />
      </div>

      {/* Chart + Insights */}
      <div style={{ display: 'flex', gap: '20px', alignItems: 'flex-start' }}>
        <div style={{
          flex: 2,
          background: 'white',
          border: '1px solid #e2e8f0',
          borderRadius: '10px',
          padding: '24px'
        }}>
          <h3 style={{ margin: '0 0 20px 0', fontSize: '17px', color: '#1e293b' }}>
            {hasReport ? report.report_name : 'Performance — placeholder'}
          </h3>

          {chart ? (
            <div ref={chartRef} style={{ width: '100%', height: '340px' }} />
          ) : (
            <div style={{
              height: '260px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#94a3b8',
              fontSize: '13px',
              fontFamily: 'monospace'
            }}>
              Chart placeholder — run analysis and generate a report to populate
            </div>
          )}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <InsightCard icon="" title="Top Insight" titleColor="#94a3b8">
            {stats?.available ? stats.top_insight_text : (rec?.question_answered ||
              'Top finding placeholder — AI-generated key finding will appear here once the report is generated.')}
          </InsightCard>

          <InsightCard icon="⚠" title="Anomaly Detected" titleColor="#d97706">
            {stats?.available ? stats.anomaly_text : (rec?.data_quality_warning ||
              'Flag / warning placeholder — unusual patterns or statistical outliers will be surfaced here.')}
          </InsightCard>

          <InsightCard icon="✓" title="Recommendation" titleColor="#16a34a">
            {stats?.available ? stats.recommendation_text : (rec?.rationale_bullets?.[0] ||
              'Suggested action placeholder — strategic recommendations based on your data will appear here.')}
          </InsightCard>
        </div>
      </div>

      {/* Export */}
      <div style={{ marginTop: '32px' }}>
        <div style={{ fontSize: '13px', fontWeight: 700, letterSpacing: '0.05em', color: '#94a3b8', marginBottom: '14px' }}>
          EXPORT
        </div>
        <div style={{ display: 'flex', gap: '16px' }}>
          <ExportCard title="Summary Report" />
          <ExportCard title="Full Analysis" />
          <ExportCard title="Recommendations" />
        </div>
      </div>
    </div>
  );
}
