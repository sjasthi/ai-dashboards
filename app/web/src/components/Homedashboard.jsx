import Card from './ui/Card';

/**
 * The landing tab: what the app does. Usage counters live in the nav bar
 * (NavStats), visible from every tab, so this page is purely the explanation.
 */

const STEPS = [
  {
    title: 'Upload',
    subtitle: 'Drop in your files.',
    body: "Upload your CSV or Excel files and choose the worksheets you want to use. We'll take it from there.",
  },
  {
    title: 'Let AI do the work',
    subtitle: "We'll find what matters.",
    body: 'AI analyzes your data, identifies what matters most, and decides how to build your reports.',
  },
  {
    title: 'Get your reports',
    subtitle: 'Ready to use.',
    body: [
      'Your reports are built with useful charts, key numbers, and insightful information from your data.',
      "When they're ready, download or share your reports as PDF or HTML.",
    ]
  },
];

export default function HomeDashboard({ onStart }) {
  return (
    <div className="home-page">
      <div className="home-intro">
        <div className="eyebrow eyebrow--accent">AI-DASHBOARD</div>

        <div className="home-hero">
          <h1 className="home-hero__title">
            Just upload it. We'll take it from here.
          </h1>
          <p className="home-hero__body">
            Upload your spreadsheet and let AI do the rest. 
            Your data is analyzed, the most useful information is identified, and your reports are built for you.
          </p>
          <p className="home-hero__emphasis">
            We'll figure out what matters. No extra steps. Just upload and go.
          </p>
        </div>
      </div>

      <Card className="home-steps">
        <h2 className="home-steps__title">How it works</h2>
        <ol className="home-steps__list">
          {STEPS.map((step, i) => (
            <li key={step.title} className="home-step">
              <span className="home-step__num">{i + 1}</span>
              <div className="home-step__content">
                <div className="home-step__title">{step.title}</div>
                <p className="home-step__subtitle">{step.subtitle}</p>
                <p className="home-step__body">
                  {Array.isArray(step.body)
                    ? step.body.map((line, idx) => (
                        <span key={idx}>
                          {idx > 0 && <br />}
                          {line}
                        </span>
                      ))
                    : step.body}
                </p>
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
        Supports .csv, .xls and .xlsx with per-sheet selection · outlier detection and data-quality warnings · relationship detection
        across files · PDF, HTML and email export.
      </p>
    </div>
  );
}
