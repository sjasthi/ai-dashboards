import Card from './ui/Card';

/**
 * The landing tab: what the app does.
 *
 * The usage counters used to live here as a row of cards. They are in the nav bar
 * now (NavStats), where they are visible from every tab and cost no vertical
 * space, so this page is purely the explanation.
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
  return (
    <div className="home-page">
      <div className="eyebrow eyebrow--accent">AI-DASHBOARD</div>

      <p className="home-hero">
        Turn a spreadsheet into a set of reports. Upload your data, and the app
        profiles it, decides what is worth showing, and builds the charts and
        figures to show it.
      </p>

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
