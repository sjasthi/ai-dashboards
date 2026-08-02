import { useEffect, useState } from 'react';
import Card from './ui/Card';
import StatTile from './ui/StatTile';
import { fetchStats, sendEvents } from '../api';
import { isNewVisit } from '../clientId';

/**
 * The landing tab: what the app does, and how much it has been used.
 *
 * Counters come from GET /api/stats, which is aggregate-only and needs no session.
 * When the backend is down the fetch fails quietly, `stats` stays null, and
 * StatTile renders an em-dash - the description below it is static and still
 * renders, so the page never goes blank because a counter was unavailable.
 */

const STEPS = [
  {
    title: 'Upload',
    body: 'Add CSV or Excel files. Multi-sheet workbooks are read sheet by sheet, so you choose which worksheets to include before anything runs.',
  },
  {
    title: 'Analyse',
    body: 'Each file is profiled - column types, ranges, and relationships across files - and an AI model proposes the reports worth building from it.',
  },
  {
    title: 'Report',
    body: 'Each report is built from your actual rows, with a chart, computed KPIs, and any data-quality warnings. Export to PDF or HTML, or send it by email.',
  },
];

export default function HomeDashboard({ onStart }) {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;

    // The visit is reported before the counters are read, so the person arriving
    // is included in the number they are about to see rather than showing up on
    // someone else's next page load.
    const arrival = isNewVisit()
      ? sendEvents([{ event: 'visit_started' }])
      : Promise.resolve(null);

    arrival
      .then(() => fetchStats())
      .then(data => { if (!cancelled) setStats(data); })
      // A missing counter is not worth an error message on a landing page.
      .catch(() => { if (!cancelled) setStats(null); });

    return () => { cancelled = true; };
  }, []);

  return (
    <div className="home-page">
      <div className="eyebrow eyebrow--accent">AI-DASHBOARD</div>

      <p className="home-hero">
        Turn a spreadsheet into a set of reports. Upload your data, and the app
        profiles it, decides what is worth showing, and builds the charts and
        figures to show it.
      </p>

      <div className="kpi-row kpi-row--three">
        {/* Counted on a browser session's first successful analysis, not on
            arrival - someone who reads this page and leaves is a visitor. */}
        <StatTile label="Users" value={stats?.users} />
        <StatTile label="Files processed" value={stats?.files_processed} />
        <StatTile label="Reports built" value={stats?.reports_built} />
      </div>

      <Card className="home-steps">
        <h2 className="home-steps__title">How it works</h2>
        <ol className="home-steps__list">
          {STEPS.map((step, i) => (
            <li key={step.title} className="home-step">
              <span className="home-step__num">{i + 1}</span>
              <div>
                <div className="home-step__title">{step.title}</div>
                <p className="home-step__body">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <button type="button" className="home-cta" onClick={onStart}>
          Get started
        </button>
      </Card>

      {/* Secondary by design - scope for someone who goes looking, not something
          that competes with the steps above. */}
      <p className="home-capabilities">
        Supports .csv, .xls and .xlsx with per-sheet selection · six report
        patterns (ranking, distribution, composition, trend, comparison, outlier)
        · six chart types (bar, line, scatter, pie, histogram, box) · automatic
        KPIs, outlier detection and data-quality warnings · relationship detection
        across files · PDF, HTML and email export.
      </p>
    </div>
  );
}
