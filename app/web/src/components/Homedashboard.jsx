import { useEffect, useRef, useState } from 'react';
import { fetchStats } from '../api';
import { exactNumber } from '../format';

/**
 * The landing page: what the app does, and how much it has been used.
 *
 * Two deliberate constraints.
 *
 * There is no way from here to anyone's previous report. Saved reports are a
 * developer tool behind a token-gated endpoint, so this page never calls
 * /api/admin/* and has no list to click. The only backend it touches is
 * /api/stats, which returns counts and nothing else.
 *
 * The capability list is written from what the code actually supports -- the six
 * patterns in recommendation_requester.REPORT_PATTERNS, the six chart types in
 * chart_builder, the statistics report_stats computes. A landing page that
 * promises a seventh chart type is worse than one that lists none, because the
 * user finds out by not getting it.
 */

// From chart_builder.COLORWAY (Okabe-Ito). Only the first two slots are used --
// this page has one two-series chart and nothing else that carries identity by
// colour. Validated for the light surface: worst adjacent separation dE 29.2
// under protanopia. Amber sits at 2.19:1 against white, below the 3:1 contrast
// floor, so the series carries visible end-of-line value labels rather than
// relying on the colour alone to be readable.
const SERIES_FILES = '#0072B2';
const SERIES_REPORTS = '#E69F00';

const STEPS = [
  {
    n: 1,
    title: 'Upload your data',
    body: 'Drop in CSV or Excel files. Multi-sheet workbooks are expanded so you '
        + 'can pick exactly which worksheets to include.',
  },
  {
    n: 2,
    title: 'AI proposes reports',
    body: 'Each file is profiled -- column types, ranges, null rates, likely keys '
        + '-- and a model proposes three reports the data can actually support.',
  },
  {
    n: 3,
    title: 'Read and share',
    body: 'Every report arrives with its chart, computed statistics and the '
        + 'operations behind it. Export to PDF or HTML, or send it by email.',
  },
];

const CAPABILITIES = [
  {
    title: 'Spreadsheets, as they really arrive',
    points: [
      '.csv, .xls and .xlsx, mixed freely in one upload',
      'Multi-sheet workbooks split into one table per worksheet',
      'Per-sheet checkboxes, so unwanted sheets never reach the analysis',
      'Encoding detection for CSVs that are not UTF-8',
    ],
  },
  {
    title: 'Six kinds of report',
    points: [
      'Ranking — order entities by a measure',
      'Distribution — how one numeric column is spread',
      'Composition — a measure broken down by category',
      'Trend — change over time',
      'Comparison — how two measures relate',
      'Outlier — rows far from the norm',
    ],
  },
  {
    title: 'Charts chosen for the question',
    points: [
      'Bar, line, scatter, pie, histogram and box plots',
      'A colourway that stays readable with colour-vision deficiency',
      'Long category labels flip the bars horizontal instead of overlapping',
      'Charts render in the browser, so exports match what you saw',
    ],
  },
  {
    title: 'Statistics you can check',
    points: [
      'Headline figures computed from the report\'s own rows',
      'Outlier detection with the method and threshold shown',
      'Data-quality warnings — nulls dropped, gaps in a time series',
      'Every figure labelled "computed" or "AI note", never blurred together',
    ],
  },
  {
    title: 'More than one file at a time',
    points: [
      'Shared keys detected across files, so reports can span them',
      'Each table traced back to the workbook and sheet it came from',
      'Row and column counts reported per file, not guessed',
    ],
  },
  {
    title: 'Getting it out',
    points: [
      'PDF export, single report or several combined for comparison',
      'Standalone HTML that keeps its chart without a server',
      'Email delivery with the document attached',
      'Optional appendix with the underlying rows',
    ],
  },
];

const TILES = [
  { key: 'users', label: 'People' },
  { key: 'sessions', label: 'Sessions' },
  { key: 'files_processed', label: 'Files processed' },
  { key: 'reports_built', label: 'Reports built' },
];

/**
 * A compact two-series line chart of recent activity.
 *
 * Hand-built SVG rather than pulling in Plotly: this is a sparkline in a page
 * that may load before any chart library is needed, and the whole thing is a
 * polyline and two labels.
 */
function ActivitySparkline({ daily }) {
  const [hover, setHover] = useState(null);
  // The viewBox width tracks the element's real width so the drawing is 1:1 with
  // CSS pixels. A fixed viewBox on a fluid container gets letterboxed by
  // preserveAspectRatio -- the line then occupies the middle of the card with dead
  // space either side. preserveAspectRatio="none" would stretch instead, but that
  // distorts the 2px strokes and turns the hover dots into ellipses.
  const [boxWidth, setBoxWidth] = useState(640);
  const plotRef = useRef(null);

  useEffect(() => {
    const node = plotRef.current;
    if (!node || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width);
      if (next > 0) setBoxWidth(next);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // A single day is a dot, not a line, and nothing to compare against; below that
  // there is nothing to draw at all.
  if (!daily || daily.length < 2) return null;

  const width = Math.max(320, boxWidth);
  const height = 120;
  const padX = 12;
  const padY = 14;
  const peak = Math.max(1, ...daily.map((d) => Math.max(d.files, d.reports)));

  const x = (i) => padX + (i * (width - padX * 2)) / (daily.length - 1);
  const y = (v) => height - padY - (v / peak) * (height - padY * 2);
  const path = (pick) => daily.map((d, i) => `${x(i)},${y(pick(d))}`).join(' ');

  const last = daily[daily.length - 1];

  return (
    <div className="home-spark">
      <div className="home-spark__head">
        <h3 className="home-spark__title">Recent activity</h3>
        {/* Two series, so a legend is always present -- identity is never left to
            colour alone. The labels wear text ink; only the swatch is coloured. */}
        <div className="home-spark__legend">
          <span className="home-spark__key">
            <span className="home-spark__swatch" style={{ background: SERIES_FILES }} />
            Files
          </span>
          <span className="home-spark__key">
            <span className="home-spark__swatch" style={{ background: SERIES_REPORTS }} />
            Reports
          </span>
        </div>
      </div>

      <div className="home-spark__plot" ref={plotRef}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="home-spark__svg"
          role="img"
          aria-label={`Files and reports per day over the last ${daily.length} days`}
          onMouseLeave={() => setHover(null)}
        >
          <polyline
            points={path((d) => d.files)}
            fill="none"
            stroke={SERIES_FILES}
            strokeWidth="2"
            strokeLinecap="round"
          />
          <polyline
            points={path((d) => d.reports)}
            fill="none"
            stroke={SERIES_REPORTS}
            strokeWidth="2"
            strokeLinecap="round"
          />

          {/* Hit targets are full-height columns, much bigger than the marks, so a
              day is easy to hover on a chart only 120px tall. */}
          {daily.map((d, i) => (
            <rect
              key={d.date}
              x={x(i) - (width - padX * 2) / (daily.length - 1) / 2}
              y={0}
              width={(width - padX * 2) / (daily.length - 1)}
              height={height}
              fill="transparent"
              onMouseEnter={() => setHover({ ...d, i })}
            />
          ))}

          {hover && (
            <g pointerEvents="none">
              <line
                x1={x(hover.i)} y1={padY / 2} x2={x(hover.i)} y2={height - padY / 2}
                stroke="var(--color-border-focus)" strokeWidth="1"
              />
              {/* A 2px surface ring separates the highlighted point from the line
                  it sits on. */}
              <circle cx={x(hover.i)} cy={y(hover.files)} r="4"
                      fill={SERIES_FILES} stroke="var(--color-surface)" strokeWidth="2" />
              <circle cx={x(hover.i)} cy={y(hover.reports)} r="4"
                      fill={SERIES_REPORTS} stroke="var(--color-surface)" strokeWidth="2" />
            </g>
          )}
        </svg>

        {/* Direct labels for the latest day. Required, not decorative: amber falls
            below the 3:1 contrast floor against white, so its value has to be
            legible as text. */}
        <div className="home-spark__latest">
          <span>{last.date}</span>
          <strong>{exactNumber(last.files)} files</strong>
          <strong>{exactNumber(last.reports)} reports</strong>
        </div>
      </div>

      {hover && (
        <p className="home-spark__readout">
          {hover.date}: {exactNumber(hover.files)} file{hover.files === 1 ? '' : 's'},{' '}
          {exactNumber(hover.reports)} report{hover.reports === 1 ? '' : 's'}
        </p>
      )}
    </div>
  );
}

export default function HomeDashboard({ onStart }) {
  const [stats, setStats] = useState(null);
  const [statsFailed, setStatsFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchStats()
      .then((data) => { if (!cancelled) setStats(data); })
      // The counters are the least important thing on this page. If the backend is
      // not running, the capability content still has to render -- so the failure
      // hides the tiles and nothing else.
      .catch(() => { if (!cancelled) setStatsFailed(true); });
    return () => { cancelled = true; };
  }, []);

  const showTiles = stats && stats.available;

  return (
    <div className="home-page">
      <header className="home-hero">
        <h1 className="home-hero__title">Turn a spreadsheet into a set of reports</h1>
        <p className="home-hero__lede">
          Upload the files you already have. Every column is profiled, an AI proposes
          the reports your data can actually support, and each one comes back with a
          chart, computed statistics and a record of how it was built.
        </p>
        <button type="button" className="home-cta" onClick={onStart}>
          Upload files to start
        </button>
      </header>

      <section className="home-steps" aria-label="How it works">
        {STEPS.map((step) => (
          <div key={step.n} className="home-step">
            <span className="home-step__n">{step.n}</span>
            <h3 className="home-step__title">{step.title}</h3>
            <p className="home-step__body">{step.body}</p>
          </div>
        ))}
      </section>

      {showTiles && (
        <section className="home-usage" aria-label="Usage so far">
          <div className="home-tiles">
            {TILES.map((tile) => (
              <div key={tile.key} className="home-tile">
                <span className="home-tile__n">{exactNumber(stats[tile.key] || 0)}</span>
                <span className="home-tile__label">{tile.label}</span>
              </div>
            ))}
          </div>
          <ActivitySparkline daily={stats.daily} />
        </section>
      )}

      {stats && !stats.available && !statsFailed && (
        <p className="home-note">
          No usage recorded yet — the counters appear here once the first files have
          been analysed.
        </p>
      )}

      <section className="home-caps" aria-label="What it can do">
        <h2 className="home-caps__title">What it can do</h2>
        <div className="home-caps__grid">
          {CAPABILITIES.map((cap) => (
            <div key={cap.title} className="home-cap">
              <h3 className="home-cap__title">{cap.title}</h3>
              <ul className="home-cap__list">
                {cap.points.map((point) => <li key={point}>{point}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <footer className="home-foot">
        <button type="button" className="home-cta" onClick={onStart}>
          Upload files to start
        </button>
      </footer>
    </div>
  );
}
