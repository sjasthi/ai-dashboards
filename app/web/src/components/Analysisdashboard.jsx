import { REPORT_TYPE_LETTERS } from '../api';

function PatternBadge({ pattern }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '3px 10px',
      borderRadius: '999px',
      fontSize: '11px',
      fontWeight: 700,
      letterSpacing: '0.03em',
      color: '#2563eb',
      background: '#eff6ff',
      border: '1px solid #bfdbfe'
    }}>
      {pattern}
    </span>
  );
}

function RecommendationCard({ rec, index, onSelect, isGenerating, isSelected, isBuilt }) {
  return (
    <div style={{
      background: 'white',
      border: isSelected ? '2px solid #2563eb' : '1px solid #e2e8f0',
      borderRadius: '10px',
      padding: '22px',
      marginBottom: '16px'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '10px' }}>
        <div>
          <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 700, marginBottom: '4px' }}>
            RANK {rec.rank ?? index + 1}
          </div>
          <h3 style={{ margin: 0, fontSize: '17px', color: '#1e293b' }}>{rec.report_name}</h3>
        </div>
        {rec.pattern_used && <PatternBadge pattern={rec.pattern_used} />}
      </div>

      {rec.question_answered && (
        <p style={{ margin: '0 0 14px 0', fontSize: '14px', color: '#334155', lineHeight: 1.5 }}>
          {rec.question_answered}
        </p>
      )}

      {rec.rationale_bullets && rec.rationale_bullets.length > 0 && (
        <ul style={{ margin: '0 0 14px 0', paddingLeft: '18px', fontSize: '13px', color: '#475569', lineHeight: 1.6 }}>
          {rec.rationale_bullets.map((bullet, i) => (
            <li key={i}>{bullet}</li>
          ))}
        </ul>
      )}

      {rec.data_quality_warning && (
        <div style={{
          marginBottom: '14px',
          padding: '10px 12px',
          background: '#fffbeb',
          border: '1px solid #fde68a',
          borderRadius: '6px',
          fontSize: '13px',
          color: '#92400e'
        }}>
          ⚠ {rec.data_quality_warning}
        </div>
      )}

      <button
        onClick={() => onSelect(index)}
        disabled={isGenerating}
        style={{
          padding: '8px 18px',
          background: isSelected ? '#1d4ed8' : '#2563eb',
          color: 'white',
          border: 'none',
          borderRadius: '6px',
          fontSize: '13px',
          fontWeight: 600,
          cursor: isGenerating ? 'not-allowed' : 'pointer',
          opacity: isGenerating && !isSelected ? 0.5 : 1
        }}
      >
        {isSelected && isGenerating
          ? 'Generating…'
          : isBuilt ? 'View this report' : 'Generate this report'}
      </button>
    </div>
  );
}

export default function AnalysisDashboard({
  sessionId, recommendations, reports = {}, generatingType, errorMsg, onGenerate,
}) {
  const recList = recommendations?.recommendations || [];

  const handleSelect = (index) => {
    if (!sessionId) return;
    onGenerate(index);
  };

  return (
    <div className="analysis-page">
      <div style={{ color: '#2563eb', fontWeight: 700, fontSize: '13px', letterSpacing: '0.05em', marginBottom: '20px' }}>
        STEP 2 — ANALYSIS
      </div>

      {recList.length === 0 ? (
        <div style={{
          background: 'white',
          border: '1px solid #e2e8f0',
          borderRadius: '10px',
          padding: '32px',
          maxWidth: '700px',
          color: '#64748b',
          fontSize: '14px'
        }}>
          No recommendations yet. Upload files in the Upload tab to generate AI report recommendations.
        </div>
      ) : (
        <>
          <p style={{ fontSize: '14px', color: '#64748b', marginBottom: '20px' }}>
            The AI reviewed your data and suggests these reports. Pick one to generate it.
          </p>

          {errorMsg && (
            <div style={{ marginBottom: '16px', padding: '12px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '6px', fontSize: '13px', color: '#b91c1c' }}>
              {errorMsg}
            </div>
          )}

          <div style={{ maxWidth: '760px' }}>
            {recList.map((rec, i) => {
              const letter = REPORT_TYPE_LETTERS[i] || 'A';
              return (
                <RecommendationCard
                  key={i}
                  rec={rec}
                  index={i}
                  onSelect={handleSelect}
                  isGenerating={!!generatingType}
                  isSelected={generatingType === letter}
                  isBuilt={!!reports[letter]}
                />
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
