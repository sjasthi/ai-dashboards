import React, { useCallback, useEffect, useState } from 'react';
import RangeSummary from './ui/RangeSummary';
import Card from './ui/Card';
import DataTable from './ui/DataTable';
import Meter from './ui/Meter';
import Plot from './ui/Plot';
import Section from './ui/Section';
import SkewGlyph from './ui/SkewGlyph';
import StatTile from './ui/StatTile';
import { compactNumber, directionGlyph, signedPercent } from '../format';
import { emailReports, exportReports, fetchExportStatus } from '../api';
import { collectChartImages, triggerDownload } from '../export';

/**
 * STEP 3 — RESULTS.
 *
 * Laid out as an inverted pyramid: computed headline numbers, then the chart with its
 * findings, then the distribution summary, then the rows themselves.
 *
 * Model-authored text on this page is labelled as such - the AI question above the
 * report, the AI note inside Data quality - and everything unlabelled is computed
 * from the report's own rows. Marking the computed side too was the original
 * design, but a chip on all four insight cards distinguished nothing. Before this,
 * the model's *question* was displayed as the top insight and its guess about data
 * quality was rendered as a detected anomaly - neither had ever been checked
 * against the data.
 */
export default function ReportsDashboard({
  reports = {},
  activeType = 'A',
  onSelectType,
  recommendations,
  fileProfiles,
  generating,
  errors = {},
  sessionId,
  replaySessionId = null,
  onExitReplay = null,
}) {
  const [showTable, setShowTable] = useState(false);
  const [showSpecs, setShowSpecs] = useState(false);
  const [comparing, setComparing] = useState(false);

  const inFlight = generating || new Set();
  const report = reports[activeType];
  const activeError = errors[activeType];
  const recList = recommendations?.recommendations || [];
  const rec = recList[activeType.charCodeAt(0) - 65];
  const stats = report?.stats;
  const hasStats = !!stats?.available;

  const replayBanner = (
    <ReplayBanner sessionId={replaySessionId} onExit={onExitReplay} />
  );

  if (!report && !recList.length) {
    return (
      <div className="reports-page">
        <div className="eyebrow eyebrow--accent" style={{ marginBottom: 20 }}>STEP 3 — RESULTS</div>
        {/* Rendered here too, not only below: a replay that came back empty would
            otherwise strand the reader on this page with no way back to their own
            session. */}
        {replayBanner}
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

      {replayBanner}

      {/* Only the report being looked at. A background build that failed for another
          letter is reported on that letter's own card, not here. */}
      {activeError && report && <div className="error-banner">{activeError}</div>}

      <ReportHeader
        report={report}
        recList={recList}
        activeType={activeType}
        onSelectType={onSelectType}
        inFlight={inFlight}
        comparing={comparing}
        onToggleCompare={() => setComparing((c) => !c)}
      />

      {comparing ? (
        <CompareView
          reports={reports}
          recList={recList}
          activeType={activeType}
          onSelectType={(t) => { onSelectType(t); setComparing(false); }}
          inFlight={inFlight}
        />
      ) : !report ? (
        /* Reached when the user clicks through faster than the background queue can
           build. The tab switch no longer waits on the request, so this slot is what
           they see in the meantime. */
        <Card>
          <div className="empty-state">
            {inFlight.has(activeType) ? (
              <div className="empty-state__title">Building report {activeType}…</div>
            ) : activeError ? (
              <>
                <div className="empty-state__title">Report {activeType} couldn’t be built</div>
                {activeError}
                <div style={{ marginTop: 12 }}>
                  <button className="link-btn" onClick={() => onSelectType(activeType)}>
                    Try again
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="empty-state__title">Report {activeType} hasn’t been generated yet</div>
                Choose it above to run it, or pick a different report.
              </>
            )}
          </div>
        </Card>
      ) : (
        <>
          <KpiRow stats={stats} report={report} />

          <div className="results-grid">
            <ChartPanel report={report} stats={stats} />
            <InsightRail report={report} stats={stats} hasStats={hasStats} rec={rec} />
          </div>

          {hasStats && <DistributionCard stats={stats} report={report} />}

          <ReportDataCard
            report={report}
            fileProfiles={fileProfiles}
            scopeText={stats?.scope_text}
            showSpecs={showSpecs}
            onToggleSpecs={() => setShowSpecs((s) => !s)}
            showTable={showTable}
            onToggleTable={() => setShowTable((s) => !s)}
          />
        </>
      )}

      {/* Outside the branch above: the export panel selects across reports, so it
          belongs in the compare view too - that is exactly when a reader wants the
          combined document. */}
      <ExportPanel
        sessionId={sessionId}
        reports={reports}
        recList={recList}
        inFlight={inFlight}
      />
    </div>
  );
}

/**
 * "You are not looking at your own data."
 *
 * A replayed report is rendered by this same page, from a payload the live endpoint
 * would have produced, so nothing on screen distinguishes it from a live one - on a
 * page whose whole premise is that every claim is labelled with where it came from.
 * Hence the label, and hence naming the session id rather than just saying "saved":
 * the id is what a developer matches against the row they clicked.
 *
 * Both props are null in the app users run, so this renders nothing there.
 */
function ReplayBanner({ sessionId, onExit }) {
  if (!onExit) return null;

  return (
    <div className="replay-banner" role="status">
      <span>
        <strong>Viewing a saved session</strong>{' '}
        <span className="replay-banner__id">{sessionId}</span>. Rebuilt from the stored
        workbook by today’s pipeline — not necessarily the report the user saw.
      </span>
      <button type="button" className="table-toggle" onClick={onExit}>
        Back to my session
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ header */

/**
 * Two lines shorter than it was, to buy the distribution card its height.
 *
 * Gone: the pattern eyebrow (COMPOSITION / RANKING / TREND). It named a branch of the
 * pipeline, not anything about the reader's data, and the report's own title says what
 * the report is.
 *
 * Gone too: the scope sentence, which now rides in the provenance line under "Report
 * data". It still has to be on the page - it is what stops someone reading the
 * headline number as a total for the whole file - but the provenance line already
 * wraps, so it costs no height there.
 */
function ReportHeader({
  report, recList, activeType, onSelectType, inFlight,
  comparing, onToggleCompare,
}) {
  const letters = recList.map((_, i) => String.fromCharCode(65 + i));
  const rec = recList[activeType.charCodeAt(0) - 65];
  const question = report?.question_answered || rec?.question_answered;

  return (
    <header className="report-header">
      <div className="report-header__top">
        <div className="report-header__text">
          <h1 className="report-header__title">
            {report?.report_name || rec?.report_name || `Report ${activeType}`}
          </h1>
          {question && (
            <p className="report-header__question">
              <span className="chip chip--ai">AI question</span>
              <em>“{question}”</em>
            </p>
          )}
        </div>

        <div className="report-header__controls">
          <div className="segmented" role="group" aria-label="Choose report">
            {/* Never disabled: selecting a report that is still building should show
                its building state, not refuse the click. */}
            {letters.map((letter) => (
              <button
                key={letter}
                type="button"
                className="segmented__btn"
                aria-pressed={letter === activeType && !comparing}
                aria-busy={inFlight.has(letter)}
                onClick={() => onSelectType(letter)}
              >
                {inFlight.has(letter) ? '…' : letter}
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
function Provenance({ report, fileProfiles, scopeText }) {
  if (!report) return null;

  const bits = [];
  // Moved down from beside the title, which the page needed the height back from.
  // Still first in the line, and still stated as a definition rather than as rows
  // lost: the filter is the report, and phrasing it as an exclusion reads as a fault.
  if (scopeText) bits.push(scopeText);
  const files = report.source_files?.length
    ? report.source_files
    : (fileProfiles || []).map((f) => f.name);
  if (files?.length) bits.push(files.join(', '));
  if (report.report_rows != null) bits.push(`${report.report_rows.toLocaleString()} data points`);
  if (report.data_columns?.length) bits.push(`${report.data_columns.length} columns`);
  if (report.chart_type) bits.push(`${report.chart_type} chart`);

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

/**
 * "Report data - Specifications" plus "Show table", laid out as bordered cells the
 * same way DistributionCard is - one head row of controls, then a stack of
 * disclosure content below it - rather than a title with a wrapping subtitle.
 *
 * That switch is what keeps "Show table" pinned in place: it used to sit in a
 * flex row that centered against the title *and* the provenance line beneath it,
 * so opening "specifications" (which can wrap to two lines) visibly shifted the
 * button. Two toggles that both open a block below the head row can't do that -
 * the head row's own height never changes.
 */
function ReportDataCard({
  report, fileProfiles, scopeText, showSpecs, onToggleSpecs, showTable, onToggleTable,
}) {
  const rows = report.report_rows || 0;
  const columns = (report.data_columns || []).length;

  return (
    <Card className="report-data-card">
      <div className="report-data-card__head">
        <div className="report-data-card__title">
          <span className="eyebrow">Report data –</span>
          <button
            type="button"
            className="build-details__toggle"
            aria-expanded={showSpecs}
            onClick={onToggleSpecs}
          >
            Specifications
            <span aria-hidden="true">{showSpecs ? '▲' : '▼'}</span>
          </button>
        </div>
        <button
          type="button"
          className="link-btn"
          aria-expanded={showTable}
          onClick={onToggleTable}
        >
          {showTable ? 'Hide table' : 'Show table'}{' '}
          <span aria-hidden="true">{showTable ? '▲' : '▼'}</span>
        </button>
      </div>

      {showSpecs && (
        <div className="report-data-card__cell">
          <Provenance report={report} fileProfiles={fileProfiles} scopeText={scopeText} />
        </div>
      )}

      <div className="report-data-card__cell">
        {showTable ? (
          <DataTable
            columns={report.data_columns || []}
            rows={report.rows || []}
            totalRows={report.report_rows || 0}
            truncated={!!report.rows_truncated}
          />
        ) : (
          <div className="empty-state">
            Report contains: {rows.toLocaleString()} rows · {columns} columns.
            Select <strong className="report-data-card__cta">Show table</strong> to view report data.
          </div>
        )}
      </div>
    </Card>
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
        label="Highest"
        value={stats.peak_value}
        sublabel={stats.peak_label || `highest ${measure}`}
      />
      <StatTile
        label="Lowest"
        value={stats.trough_value}
        sublabel={stats.trough_label || `lowest ${measure}`}
      />
      {stats.blocks?.includes('trend') ? (
        /* No sparkline: the chart directly below plots the same series at full
           size, so a thumbnail of it cost tile height to repeat what the reader
           was about to see anyway. */
        <StatTile
          label="Trend"
          value={signedPercent(stats.trend_pct_change)}
          sublabel={
            <>
              <span className={`stat-tile__delta stat-tile__delta--${stats.trend_direction || 'flat'}`}>
                {directionGlyph(stats.trend_direction)}
              </span>
              {stats.trend_strength && <> {stats.trend_strength} · R² {stats.trend_r2}</>}
            </>
          }
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
        {/* Sample size rides here rather than taking a cell in the distribution card.
            It qualifies everything on the page - how much data every figure below is
            drawn from - so it belongs beside what is being measured. */}
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

/**
 * Two findings in one card, ruled apart by a hairline.
 *
 * Used to be four. Outliers and data quality moved into the distribution card's
 * "show all statistics" disclosure - they're measurements about the same numbers
 * that card already summarises, not a separate reading of the report - and "what
 * to check next" is gone outright, it never told a reader anything the key finding
 * hadn't already said. What replaced that space is the model's own rationale for
 * recommending this report: the same two bullets shown on the Analysis page,
 * carried over so a reader who jumped straight here still gets them.
 */
function InsightRail({ report, stats, hasStats, rec }) {
  const bullets = (rec?.rationale_bullets || []).slice(1, 3);

  if (!hasStats) {
    return (
      <Card className="insight-rail">
        <div className="insight-row">
          <div className="insight-card__head">
            <span className="insight-card__title">No statistics available</span>
          </div>
          <div className="insight-card__body">
            {stats?.unavailable_reason ||
              'This report has no numeric measure to compute statistics from.'}
          </div>
          {stats?.llm_caveat && <AiNote text={stats.llm_caveat} />}
        </div>
        {bullets.length > 0 && <AiInsightsCard bullets={bullets} />}
      </Card>
    );
  }

  return (
    <Card className="insight-rail">
      <InsightCard title="Key finding">
        {stats.top_insight_text}
      </InsightCard>

      {bullets.length > 0 && <AiInsightsCard bullets={bullets} />}
    </Card>
  );
}

/** The model's own rationale for this report - why it expects this and how to use it. */
function AiInsightsCard({ bullets }) {
  return (
    <InsightCard title="AI insights">
      <ul className="ai-insights-list">
        {bullets.map((b, i) => <li key={i}>{b}</li>)}
      </ul>
    </InsightCard>
  );
}

/**
 * One finding: a ruled row inside the rail, no longer a card of its own.
 *
 * Every one of these used to carry a "computed" chip. When all four say the same
 * thing the chip stops distinguishing anything - what marks model-authored text is
 * the AI note inside the row, which is where the distinction is actually load-bearing.
 *
 * The amber variant survives the merge as a tinted row rather than a tinted card. It
 * still has to be able to shout: it is the only thing on the page that says a number
 * above it is standing on a hole in the data.
 */
function InsightCard({ title, icon, variant, children }) {
  return (
    <div className={`insight-row${variant ? ` insight-row--${variant}` : ''}`}>
      <div className="insight-card__head">
        <span className="insight-card__title">
          {icon && <span aria-hidden="true">{icon}</span>}
          {title}
        </span>
      </div>
      <div className="insight-card__body">{children}</div>
    </div>
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

/**
 * Ten numbers, grouped by the three jobs they actually do.
 *
 * This was a flat strip of ten equal cells. Every statistic got the same weight, and
 * the five-number summary - which is one shape - was five separate figures the reader
 * had to hold in their head and compare. Now the summary is drawn on a shared scale,
 * the two measures of centre sit together with the skew between them, and the three
 * measures of spread sit under one meter.
 *
 * The ten cells are still here, behind the disclosure. Nothing the card visualises is
 * reachable only by hovering a mark: the exact figures are one click away, and the
 * chart's own values are in the table below. A tooltip enhances, it never gates.
 */
function DistributionCard({ stats, report }) {
  const [showAll, setShowAll] = useState(false);

  const skewTitle = stats.skew_ratio != null
    ? `The mean sits ${Math.abs(stats.skew_ratio)} interquartile ranges ` +
      `${stats.skew_ratio > 0 ? 'above' : 'below'} the median.`
    : undefined;

  const stdTitle = stats.pct_within_1sd != null
    ? `Standard deviation (σ) — about ${stats.pct_within_1sd}% of values fall within this distance of the average.`
    : 'Standard deviation (σ) — the typical distance of a value from the average.';
  const iqrTitle = 'Interquartile range (IQR) — the span of the middle 50% of values, unaffected by outliers.';
  const meanTitle = 'Mean — the sum of all values divided by how many there are. Sensitive to outliers.';
  const medianTitle = 'Median — the middle value when all values are sorted. Unaffected by outliers.';

  return (
    <Card className="dist-card">
      <div className="dist-card__head">
        <span className="eyebrow">Distribution</span>
        <button
          type="button"
          className="link-btn"
          aria-expanded={showAll}
          onClick={() => setShowAll((s) => !s)}
        >
          {showAll ? 'Hide all statistics' : 'Show all statistics'}{' '}
          <span aria-hidden="true">{showAll ? '▲' : '▼'}</span>
        </button>
      </div>

      <div className="dist-card__cell">
        <div className="dist-card__cell-label">Range</div>
        <RangeSummary
          min={stats.min}
          p25={stats.p25}
          median={stats.median}
          p75={stats.p75}
          max={stats.max}
          fenceLow={stats.fence_low}
          fenceHigh={stats.fence_high}
          anomalies={stats.anomalies || []}
          measureLabel={stats.measure_label}
          iqrTitle={iqrTitle}
        />
      </div>

      <div className="dist-card__cell">
        <div className="dist-card__cell-label-row">
          <div className="dist-card__cell-label">Center</div>
          {/* Neutral, not a status colour: a skewed series is a shape, not a
              problem. What it does tell the reader is which of the two figures
              below to trust as the typical value. Alongside the section label
              rather than under the numbers, so it reads as a property of "center"
              being described, not a third stat competing with mean/median. */}
          {stats.skew_label && (
            <span className="chip chip--neutral" title={skewTitle}>{stats.skew_label}</span>
          )}
        </div>
        <SkewGlyph
          skewLevel={stats.skew_level}
          skewRatio={stats.skew_ratio}
          medianTitle={medianTitle}
          meanTitle={meanTitle}
        />
        <div className="dist-card__pair">
          <span className="dist-card__pair-label" title={meanTitle}>Average</span>
          <span className="dist-card__pair-value" title={meanTitle}>{compactNumber(stats.mean)}</span>
          <span className="dist-card__pair-label" title={medianTitle}>Midpoint</span>
          <span className="dist-card__pair-value" title={medianTitle}>{compactNumber(stats.median)}</span>
        </div>
      </div>

      <div className="dist-card__cell">
        <div className="dist-card__cell-label-row">
          <div className="dist-card__cell-label">Spread</div>
          {/* CV has no upper bound, so the meter saturates at 100% and two very
              different extremes fill the same bar. That is why the badge beside the
              label prints the figure rather than leaving the bar to carry it: the
              meter shows the band, the badge shows the number. */}
          <span
            className={`chip chip--${VARIANCE_CHIP[stats.variance_level] || 'neutral'}`}
            title="Coefficient of variation — the spread of the values relative to their average."
          >
            {stats.variance_label || 'Variance —'}
          </span>
        </div>
        <Meter
          value={stats.cv}
          level={stats.variance_level}
          label={stats.variance_label || 'Variance not measurable'}
        />
        <div className="dist-card__pair dist-card__pair--spread">
          <span className="dist-card__pair-label" title={stdTitle}>Average variation</span>
          <span className="dist-card__pair-value" title={stdTitle}>{compactNumber(stats.std)}</span>
          <span className="dist-card__pair-label" title={iqrTitle}>Range of middle 50%</span>
          <span className="dist-card__pair-value" title={iqrTitle}>{compactNumber(stats.iqr)}</span>
        </div>
      </div>

      {showAll && <DistributionStrip stats={stats} report={report} />}
    </Card>
  );
}

/* A wide spread is a property of the data, not a fault, so the top band is amber and
   never red - red on this page means something went wrong. */
const VARIANCE_CHIP = { low: 'good', moderate: 'neutral', high: 'warn' };

/**
 * The ten raw figures, plus outliers and data quality. No longer the page's
 * distribution summary - it is what DistributionCard's disclosure opens - but
 * still the place every exact value lives, and still what the PDF export renders.
 *
 * Outliers and data quality used to be their own cards beside the chart. They
 * moved here because they're both measurements about the same numbers this strip
 * already lays out, not a separate reading of the report - and putting them
 * behind the same disclosure keeps the default view to what most readers need.
 */
function DistributionStrip({ stats, report }) {
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

  const outliers = stats.anomalies || [];
  const outlierCount = stats.anomaly_count || 0;
  // Same reasoning as the insight rail used to carry in its comment: a join loss
  // or a schema warning is a real hole in the numbers above and earns the amber
  // tint; a filter's excluded rows are scope, not a fault, and live elsewhere.
  const qualityWarn = !!(stats.null_count || stats.join_loss_rows || report?.schema_warning);

  return (
    <>
      <div className="stat-strip dist-card__all">
        {items.map(([label, value]) => (
          <div className="stat-strip__item" key={label}>
            <div className="stat-strip__label">{label}</div>
            <div className="stat-strip__value">{value}</div>
          </div>
        ))}
      </div>

      <div className="dist-card__all dist-card__meta">
        <div className={`dist-card__meta-col${outlierCount ? ' dist-card__meta-col--warn' : ''}`}>
          <div className="dist-card__meta-label">
            {outlierCount ? `⚠ Outliers (${outlierCount})` : 'Outliers'}
          </div>
          <div className="dist-card__meta-body">
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
          </div>
        </div>

        <div className={`dist-card__meta-col${qualityWarn ? ' dist-card__meta-col--warn' : ''}`}>
          <div className="dist-card__meta-label">
            {qualityWarn ? '⚠ Data quality' : 'Data quality'}
          </div>
          <div className="dist-card__meta-body">{stats.quality_text || 'No issues found.'}</div>
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------------------- compare all */

/**
 * The three recommendations side by side.
 *
 * The model always returns exactly three; comparing them one tab-switch at a time
 * made it impossible to see which one actually answered the question.
 */
function CompareView({ reports, recList, activeType, onSelectType, inFlight }) {
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
                  {inFlight.has(letter) ? 'Generating…' : 'Not generated yet'}
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
                  disabled={inFlight.has(letter)}
                  onClick={() => onSelectType(letter)}
                >
                  {inFlight.has(letter)
                    ? 'Generating…'
                    : report ? 'Open full view' : 'Generate this report'}
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
 * Download or email the generated reports.
 *
 * The unit of export is a report, A/B/C - not the three tiers ("Summary report",
 * "Full analysis", "Recommendations") this section used to offer, which named
 * nothing the app produces. Selecting one gives a single-report document; selecting
 * several gives one combined comparative document, because comparing them is the
 * reason to export more than one.
 *
 * A report that hasn't been generated can't be offered. Only generated reports have
 * a chart figure in state, and Plot.jsx purges its node on unmount, so there is
 * nothing to rasterise for the others - the checkbox is disabled rather than
 * failing at render time.
 */
function ExportPanel({ sessionId, reports, recList, inFlight }) {
  const letters = recList.map((_, i) => String.fromCharCode(65 + i));
  const generated = letters.filter((l) => reports[l]);

  const [selected, setSelected] = useState(() => new Set(generated));
  const [recipients, setRecipients] = useState('');
  const [emailFormat, setEmailFormat] = useState('pdf');
  const [status, setStatus] = useState(null); // { kind: 'info'|'error'|'ok', text }
  const [busy, setBusy] = useState(false);
  const [emailConfigured, setEmailConfigured] = useState(null); // null = unknown yet

  // Asked up front so the email row can explain itself before the user types an
  // address, rather than after.
  //
  // Re-asked on window focus because the server reads .env on every request: fill
  // in the credentials, alt-tab back, and the row unlocks. Without this the panel
  // keeps telling you to do the thing you just did until you reload the page.
  const refreshStatus = useCallback(() => {
    if (!sessionId) return;
    fetchExportStatus(sessionId)
      .then((s) => setEmailConfigured(!!s.email_configured))
      .catch(() => setEmailConfigured(false));
  }, [sessionId]);

  useEffect(() => {
    refreshStatus();
    window.addEventListener('focus', refreshStatus);
    return () => window.removeEventListener('focus', refreshStatus);
  }, [refreshStatus]);

  // A freshly generated report joins the selection: the user just asked for it, so
  // it is almost certainly one they want in the file.
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set([...prev].filter((l) => reports[l]));
      generated.forEach((l) => { if (!prev.has(l) && !next.has(l)) next.add(l); });
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generated.join(',')]);

  const chosen = generated.filter((l) => selected.has(l));
  // Global rather than per-letter: export rasterises charts across every selected
  // report, so it has to wait for the whole set to settle. With background
  // prefetching that just means export unlocks a moment after the page opens.
  const disabled = busy || inFlight.size > 0 || !sessionId;

  if (!generated.length) {
    return (
      <Card className="export-panel">
        <div className="export-panel__head">
          <span className="eyebrow">Export</span>
        </div>
        <div className="empty-state" style={{ padding: '24px' }}>
          Generate a report first — then you can download it as a PDF or an HTML
          file, or email it.
        </div>
      </Card>
    );
  }

  const toggle = (letter) => {
    setStatus(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(letter)) next.delete(letter); else next.add(letter);
      return next;
    });
  };

  /** Rasterise the selected charts, then hand the payload to `send`. */
  const withPayload = async (verb, send) => {
    setBusy(true);
    setStatus({ kind: 'info', text: 'Rendering charts…' });
    try {
      const chartImages = await collectChartImages(reports, chosen);
      setStatus({ kind: 'info', text: `${verb}…` });
      await send(chartImages);
    } catch (err) {
      setStatus({ kind: 'error', text: err.message || 'Something went wrong.' });
      refreshStatus();  // a 503 here means the server's view of .env has changed
    } finally {
      setBusy(false);
    }
  };

  const download = (format) => withPayload(
    `Building the ${format.toUpperCase()}`,
    async (chartImages) => {
      const { blob, filename } = await exportReports(sessionId, {
        reportTypes: chosen, format, chartImages,
      });
      const name = filename || `ai-dashboard-reports-${chosen.join('').toLowerCase()}.${format}`;
      triggerDownload(blob, name);
      setStatus({ kind: 'ok', text: `Downloaded ${name}` });
    },
  );

  const sendEmail = () => withPayload('Sending', async (chartImages) => {
    const result = await emailReports(sessionId, {
      reportTypes: chosen, format: emailFormat, chartImages,
      recipients: [recipients],
    });
    setStatus({ kind: 'ok', text: `Sent to ${result.recipients.join(', ')}.` });
    setRecipients('');
  });

  return (
    <Card className="export-panel">
      <div className="export-panel__head">
        <span className="eyebrow">Export</span>
        <div className="export-panel__links">
          <button
            className="link-btn"
            disabled={disabled || chosen.length === generated.length}
            onClick={() => { setStatus(null); setSelected(new Set(generated)); }}
          >
            Select all
          </button>
          <button
            className="link-btn"
            disabled={disabled || !chosen.length}
            onClick={() => { setStatus(null); setSelected(new Set()); }}
          >
            Clear
          </button>
        </div>
      </div>

      <div className="export-panel__body">
        <div className="export-panel__choices">
          {letters.map((letter) => {
            const report = reports[letter];
            const rec = recList[letter.charCodeAt(0) - 65];
            return (
              <label
                key={letter}
                className={`export-choice${report ? '' : ' export-choice--unavailable'}`}
              >
                <input
                  type="checkbox"
                  checked={selected.has(letter)}
                  disabled={disabled || !report}
                  onChange={() => toggle(letter)}
                />
                <span className="export-choice__body">
                  <span className="export-choice__name">
                    {letter} — {report?.report_name || rec?.report_name || `Report ${letter}`}
                  </span>
                  <span className="export-choice__meta">
                    {report
                      ? `${report.pattern_used || 'report'} · ${(report.report_rows || 0).toLocaleString()} data points`
                      : 'not generated yet — open it above first'}
                  </span>
                </span>
              </label>
            );
          })}
        </div>

        <div className="export-panel__row">
          <button className="link-btn" disabled={disabled || !chosen.length}
                  onClick={() => download('pdf')}>
            Download PDF
          </button>
          <button className="link-btn" disabled={disabled || !chosen.length}
                  onClick={() => download('html')}>
            Download HTML
          </button>
          <span className="export-panel__hint">
            {chosen.length === 0
              ? 'Choose at least one report.'
              : chosen.length === 1
                ? `Report ${chosen[0]} as one document.`
                : `${chosen.length} reports as one combined document, with a comparison table.`}
          </span>
        </div>

        <div className="export-panel__row export-panel__row--email">
          <label className="export-panel__label" htmlFor="export-email">Email to</label>
          <input
            id="export-email"
            type="text"
            className="export-panel__input"
            placeholder="recipient@example.com"
            value={recipients}
            disabled={disabled || emailConfigured === false}
            onChange={(e) => { setRecipients(e.target.value); setStatus(null); }}
          />
          <div className="segmented" role="group" aria-label="Email format">
            {['pdf', 'html'].map((fmt) => (
              <button
                key={fmt}
                type="button"
                className="segmented__btn"
                aria-pressed={emailFormat === fmt}
                disabled={disabled || emailConfigured === false}
                onClick={() => setEmailFormat(fmt)}
              >
                {fmt.toUpperCase()}
              </button>
            ))}
          </div>
          <button
            className="link-btn"
            disabled={disabled || !chosen.length || !recipients.trim() || emailConfigured === false}
            onClick={sendEmail}
          >
            Send
          </button>
        </div>

        {emailConfigured === false && (
          <div className="export-panel__hint">
            Email isn’t configured on this server — set SMTP_HOST, SMTP_USER and
            SMTP_PASSWORD in <code>.env</code> (see <code>.env.example</code>) and
            restart the API. Downloads work either way.
          </div>
        )}

        {status && (
          <div className={`export-status export-status--${status.kind}`} role="status">
            {status.text}
          </div>
        )}
      </div>
    </Card>
  );
}
