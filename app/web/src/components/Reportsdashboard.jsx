import React, { useState } from 'react';
import Card from './ui/Card';
import DataTable from './ui/DataTable';
import Plot from './ui/Plot';
import Section from './ui/Section';
import StatTile from './ui/StatTile';
import { clockTime, compactNumber, signedPercent } from '../format';

/**
 * STEP 3 — RESULTS.
 *
 * Laid out as an inverted pyramid: computed headline numbers, then the chart with its
 * findings, then the distribution summary, then the rows themselves.
 *
 * Every claim on this page is labelled with where it came from. Statistics computed
 * from the report's own rows carry a "computed" chip; the model's pre-execution text
 * is kept as context and marked as such. Before this, the model's *question* was
 * displayed as the top insight and its guess about data quality was rendered as a
 * detected anomaly - neither had ever been checked against the data.
 */
export default function ReportsDashboard({
  reports = {},
  activeType = 'A',
  onSelectType,
  recommendations,
  fileProfiles,
  generatingType,
  errorMsg,
}) {
  const [showTable, setShowTable] = useState(false);
  const [comparing, setComparing] = useState(false);

  const report = reports[activeType];
  const recList = recommendations?.recommendations || [];
  const stats = report?.stats;
  const hasStats = !!stats?.available;

  if (!report && !recList.length) {
    return (
      <div className="reports-page">
        <div className="eyebrow eyebrow--accent" style={{ marginBottom: 20 }}>STEP 3 — RESULTS</div>
        <Card>
          <div className="empty-state">
            <div className="empty-state__title">No report yet</div>
            Upload your data in the Upload tab, then pick one of the suggested reports
            in Analysis to generate it.
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="reports-page">
      <div className="eyebrow eyebrow--accent" style={{ marginBottom: 20 }}>STEP 3 — RESULTS</div>

      {errorMsg && <div className="error-banner">{errorMsg}</div>}

      <ReportHeader
        report={report}
        recList={recList}
        activeType={activeType}
        onSelectType={onSelectType}
        generatingType={generatingType}
        comparing={comparing}
        onToggleCompare={() => setComparing((c) => !c)}
        fileProfiles={fileProfiles}
      />

      {comparing ? (
        <CompareView
          reports={reports}
          recList={recList}
          activeType={activeType}
          onSelectType={(t) => { onSelectType(t); setComparing(false); }}
          generatingType={generatingType}
        />
      ) : !report ? (
        <Card>
          <div className="empty-state">
            <div className="empty-state__title">Report {activeType} hasn’t been generated yet</div>
            Choose it above to run it, or pick a different report.
          </div>
        </Card>
      ) : (
        <>
          <KpiRow stats={stats} report={report} />

          <div className="results-grid">
            <ChartPanel report={report} stats={stats} />
            <InsightRail report={report} stats={stats} hasStats={hasStats} />
          </div>

          {hasStats && <DistributionStrip stats={stats} />}

          <Section
            title="Report data"
            actions={
              <button className="table-toggle" onClick={() => setShowTable((s) => !s)}>
                {showTable ? 'Hide table' : 'Show table'}
                <span aria-hidden="true">{showTable ? '▲' : '▼'}</span>
              </button>
            }
          >
            {showTable ? (
              <DataTable
                columns={report.data_columns || []}
                rows={report.rows || []}
                totalRows={report.report_rows || 0}
                truncated={!!report.rows_truncated}
              />
            ) : (
              <Card>
                <div className="empty-state" style={{ padding: '20px' }}>
                  {(report.report_rows || 0).toLocaleString()} rows ·{' '}
                  {(report.data_columns || []).length} columns. Every value in the chart
                  is readable here as text.
                </div>
              </Card>
            )}
          </Section>

          <ExportSection />
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ header */

function ReportHeader({
  report, recList, activeType, onSelectType, generatingType,
  comparing, onToggleCompare, fileProfiles,
}) {
  const letters = recList.map((_, i) => String.fromCharCode(65 + i));
  const rec = recList[activeType.charCodeAt(0) - 65];
  const question = report?.question_answered || rec?.question_answered;

  return (
    <header className="report-header">
      <div className="report-header__top">
        <div style={{ minWidth: 0 }}>
          <span className="eyebrow">
            {report?.pattern_used || rec?.pattern_used || 'Report'}
          </span>
          <h1 className="report-header__title">
            {report?.report_name || rec?.report_name || `Report ${activeType}`}
          </h1>
          {question && (
            <p className="report-header__question">
              <span className="chip chip--ai">AI question</span>
              <em>“{question}”</em>
            </p>
          )}
          <Provenance report={report} fileProfiles={fileProfiles} />
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
          <div className="segmented" role="group" aria-label="Choose report">
            {letters.map((letter) => (
              <button
                key={letter}
                type="button"
                className="segmented__btn"
                aria-pressed={letter === activeType && !comparing}
                disabled={!!generatingType}
                onClick={() => onSelectType(letter)}
              >
                {generatingType === letter ? '…' : letter}
              </button>
            ))}
          </div>
          {letters.length > 1 && (
            <button
              type="button"
              className="segmented__btn"
              style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)' }}
              aria-pressed={comparing}
              onClick={onToggleCompare}
            >
              {comparing ? 'Back to report' : 'Compare all'}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

/**
 * Where these numbers came from and when.
 *
 * Dashboards get trusted or ignored on this: without provenance a reader has no way
 * to tell a fresh result from a stale one, or to check that the pipeline filtered
 * what they thought it filtered.
 */
function Provenance({ report, fileProfiles }) {
  if (!report) return null;

  const bits = [];
  const files = report.source_files?.length
    ? report.source_files
    : (fileProfiles || []).map((f) => f.name);
  if (files?.length) bits.push(files.join(', '));
  if (report.report_rows != null) bits.push(`${report.report_rows.toLocaleString()} data points`);
  if (report.data_columns?.length) bits.push(`${report.data_columns.length} columns`);
  if (report.chart_type) bits.push(`${report.chart_type} chart`);
  const time = clockTime(report.generated_at);
  if (time) bits.push(`generated ${time}`);

  return (
    <div className="provenance">
      {bits.map((bit, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="provenance__sep" aria-hidden="true">·</span>}
          <span>{bit}</span>
        </React.Fragment>
      ))}
      {report.operations?.map((op) => <code key={op}>{op}</code>)}
    </div>
  );
}

/* --------------------------------------------------------------- KPI tiles */

/**
 * Four tiles, all derived from the report's rows.
 *
 * Deliberately not here: the report's column count and Plotly trace name. Both used
 * to occupy a headline slot; neither tells a reader anything about their data, and
 * the column count was inflated by two bookkeeping columns while the trace name
 * called every line chart a "scatter".
 */
function KpiRow({ stats, report }) {
  if (!stats?.available) {
    return (
      <div className="kpi-row">
        <StatTile label="Rows" value={report?.report_rows ?? null} />
        <StatTile label="Columns" value={report?.data_columns?.length ?? null} />
        <StatTile label="Pattern" value={report?.pattern_used || null} />
        <StatTile label="Chart" value={report?.chart_type || null} />
      </div>
    );
  }

  const measure = stats.measure_label || 'value';

  return (
    <div className="kpi-row">
      <StatTile
        label={stats.headline_label || measure}
        value={stats.headline_value}
        sublabel={stats.headline_sublabel}
      />
      <StatTile
        label="Peak"
        value={stats.peak_value}
        sublabel={stats.peak_label || `highest ${measure}`}
      />
      <StatTile
        label="Low"
        value={stats.trough_value}
        sublabel={stats.trough_label || `lowest ${measure}`}
      />
      {stats.blocks?.includes('trend') ? (
        <StatTile
          label="Trend"
          value={signedPercent(stats.trend_pct_change)}
          delta={{
            direction: stats.trend_direction,
            pct: stats.trend_pct_change,
            note: stats.trend_strength
              ? `${stats.trend_strength} · R² ${stats.trend_r2}`
              : null,
          }}
          sparkline={stats.sparkline}
        />
      ) : stats.blocks?.includes('concentration') ? (
        <StatTile
          label="Concentration"
          value={`${stats.top3_share}%`}
          sublabel={`top ${Math.min(3, stats.n_categories)} of ${stats.n_categories} categories`}
        />
      ) : (
        <StatTile
          label="Spread"
          value={stats.cv != null ? `${stats.cv}%` : null}
          sublabel={`variation around a mean of ${compactNumber(stats.mean)}`}
        />
      )}
    </div>
  );
}

/* -------------------------------------------------------------- chart card */

function ChartPanel({ report, stats }) {
  const title = report.chart?.layout?.title?.text || report.report_name;

  return (
    <Card className="chart-card">
      <div className="chart-card__head">
        <h2 className="chart-card__title">{title}</h2>
        {stats?.available && stats.measure_label && (
          <span className="eyebrow">{stats.measure_label}</span>
        )}
      </div>

      {report.chart ? (
        <Plot chart={report.chart} stats={stats} className="chart-card__plot" />
      ) : (
        <div className="chart-card__placeholder">
          No chart could be drawn for this report — the recommended axes didn’t match
          the columns the pipeline produced.
          <br />
          The numbers below still come from the full result set.
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------ insight rail */

function InsightRail({ report, stats, hasStats }) {
  if (!hasStats) {
    return (
      <div className="insight-rail">
        <Card className="insight-card">
          <div className="insight-card__head">
            <span className="insight-card__title">No statistics available</span>
          </div>
          <div className="insight-card__body">
            {stats?.unavailable_reason ||
              'This report has no numeric measure to compute statistics from.'}
          </div>
          {stats?.llm_caveat && <AiNote text={stats.llm_caveat} />}
        </Card>
      </div>
    );
  }

  const outliers = stats.anomalies || [];
  const outlierCount = stats.anomaly_count || 0;

  return (
    <div className="insight-rail">
      <InsightCard title="Key finding" chip>
        {stats.top_insight_text}
      </InsightCard>

      {/* Amber only when something was actually detected. A warning card that always
          looks like a warning stops being read as one. */}
      <InsightCard
        title={outlierCount ? `Outliers (${outlierCount})` : 'Outliers'}
        icon={outlierCount ? '⚠' : null}
        variant={outlierCount ? 'warn' : undefined}
        chip
      >
        {stats.anomaly_text}
        {outliers.length > 0 && (
          <ul className="outlier-list">
            {outliers.slice(0, 5).map((a, i) => (
              <li key={i}>
                <span className="outlier-list__label">{a.label}</span>
                <span className="outlier-list__value">
                  {compactNumber(a.value)}
                  {a.score != null && ` · z ${a.score > 0 ? '+' : ''}${a.score}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </InsightCard>

      <InsightCard
        title="Data quality"
        variant={stats.null_count || report.schema_warning ? 'warn' : undefined}
        chip
      >
        {stats.quality_text || 'No completeness issues found in the charted measure.'}
        {stats.llm_caveat && <AiNote text={stats.llm_caveat} />}
      </InsightCard>

      <InsightCard title="What to check next" icon="✓" variant="good" chip>
        {stats.recommendation_text}
      </InsightCard>
    </div>
  );
}

function InsightCard({ title, icon, variant, chip, children }) {
  return (
    <Card className={`insight-card${variant ? ` insight-card--${variant}` : ''}`}>
      <div className="insight-card__head">
        <span className="insight-card__title">
          {icon && <span aria-hidden="true">{icon}</span>}
          {title}
        </span>
        {chip && <span className="chip chip--computed">computed</span>}
      </div>
      <div className="insight-card__body">{children}</div>
    </Card>
  );
}

/** The model's own text, kept but never dressed up as a measurement. */
function AiNote({ text }) {
  return (
    <div className="insight-note">
      <span className="insight-note__label">AI note</span>
      {text}
    </div>
  );
}

/* ---------------------------------------------------- distribution summary */

function DistributionStrip({ stats }) {
  const items = [
    ['n', stats.count?.toLocaleString()],
    ['min', compactNumber(stats.min)],
    ['p25', compactNumber(stats.p25)],
    ['median', compactNumber(stats.median)],
    ['p75', compactNumber(stats.p75)],
    ['max', compactNumber(stats.max)],
    ['mean', compactNumber(stats.mean)],
    ['std dev', compactNumber(stats.std)],
    ['IQR', compactNumber(stats.iqr)],
    ['CV', stats.cv != null ? `${stats.cv}%` : '—'],
  ].filter(([, v]) => v !== undefined && v !== null);

  return (
    <Card className="stat-strip">
      {items.map(([label, value]) => (
        <div className="stat-strip__item" key={label}>
          <div className="stat-strip__label">{label}</div>
          <div className="stat-strip__value">{value}</div>
        </div>
      ))}
    </Card>
  );
}

/* ------------------------------------------------------------- compare all */

/**
 * The three recommendations side by side.
 *
 * The model always returns exactly three; comparing them one tab-switch at a time
 * made it impossible to see which one actually answered the question.
 */
function CompareView({ reports, recList, activeType, onSelectType, generatingType }) {
  return (
    <Section title="All reports">
      <div className="compare-grid">
        {recList.map((rec, i) => {
          const letter = String.fromCharCode(65 + i);
          const report = reports[letter];
          const stats = report?.stats;

          return (
            <Card
              key={letter}
              className={`compare-card${letter === activeType ? ' compare-card--active' : ''}`}
            >
              <div>
                <span className="eyebrow">{rec.pattern_used} · {letter}</span>
                <h3 className="compare-card__title">{rec.report_name}</h3>
              </div>

              {report?.chart ? (
                <Plot chart={report.chart} stats={stats} compact className="compare-card__plot" />
              ) : (
                <div className="compare-card__plot" style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--color-text-muted)', fontSize: 12,
                }}>
                  {generatingType === letter ? 'Generating…' : 'Not generated yet'}
                </div>
              )}

              {stats?.available && (
                <div className="kpi-row" style={{ gridTemplateColumns: '1fr 1fr', gap: 8, margin: 0 }}>
                  <div>
                    <div className="stat-strip__label">{stats.headline_label}</div>
                    <div className="stat-strip__value">{compactNumber(stats.headline_value)}</div>
                  </div>
                  {stats.blocks?.includes('trend') && (
                    <div>
                      <div className="stat-strip__label">trend</div>
                      <div className={`stat-strip__value stat-tile__delta--${stats.trend_direction}`}>
                        {signedPercent(stats.trend_pct_change)}
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="compare-card__finding">
                {stats?.top_insight_text || rec.question_answered}
              </div>

              <div className="compare-card__foot">
                <button
                  className="link-btn"
                  disabled={!!generatingType}
                  onClick={() => onSelectType(letter)}
                >
                  {report ? 'Open full view' : 'Generate this report'}
                </button>
              </div>
            </Card>
          );
        })}
      </div>
    </Section>
  );
}

/* ------------------------------------------------------------------ export */

/**
 * Scaffolding only — there is no /api/export endpoint yet.
 *
 * These render disabled rather than as live-looking buttons that silently do
 * nothing, which is the worse of the two failure modes.
 */
function ExportSection() {
  return (
    <Section title="Export" chip={<span className="chip">not yet available</span>}>
      <div className="compare-grid">
        {['Summary report', 'Full analysis', 'Recommendations'].map((title) => (
          <Card key={title} className="compare-card">
            <div className="compare-card__title">{title}</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {['PDF', 'HTML', 'Email'].map((fmt) => (
                <button
                  key={fmt}
                  className="link-btn"
                  disabled
                  title="Export isn’t implemented yet"
                >
                  {fmt}
                </button>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </Section>
  );
}
