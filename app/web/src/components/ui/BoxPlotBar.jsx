import { compactNumber } from '../../format';

/**
 * The five-number summary as one horizontal box-and-whisker.
 *
 * Replaces five separate numeric cells. Min, p25, median, p75 and max read as five
 * facts to compare when they are set side by side as text; drawn on a shared scale
 * they read as one shape, and where the median sits inside the middle half - the
 * thing the numbers make you work out - is simply visible.
 *
 * Built from positioned divs rather than SVG, unlike Sparkline. The bar is fluid-width
 * and its labels are real prose that has to size and wrap with the page; SVG text does
 * neither without a measurement pass, and percentage positions need no measurement at
 * all. Sparkline is SVG because it is fixed at 96x26, which is the case SVG suits.
 *
 * Nothing here is the only route to a value: every number on the bar is also in the
 * card's "Show all statistics" grid. The labels and tooltips enhance, they never gate.
 */
export default function BoxPlotBar({
  min, p25, median, p75, max,
  fenceLow, fenceHigh,
  anomalies = [],
  measureLabel,
}) {
  const nums = [min, p25, median, p75, max];
  if (nums.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null;

  const span = max - min;

  // Every value identical. There is no shape to draw and (max - min) is the divisor
  // below, so this returns before the arithmetic rather than rendering a bar of NaN%.
  if (span <= 0) {
    return (
      <div className="boxplot boxplot--flat">
        <div className="boxplot__track">
          <div className="boxplot__median" style={{ left: '50%' }} />
        </div>
        <div className="boxplot__caption">
          every value is {compactNumber(median)} — no spread to plot
        </div>
      </div>
    );
  }

  const pos = (v) => ((v - min) / span) * 100;
  const medianPct = pos(median);
  const boxLeft = pos(p25);
  const boxWidth = pos(p75) - boxLeft;

  const fences = [
    ['low', fenceLow],
    ['high', fenceHigh],
  ].filter(([, v]) => typeof v === 'number' && v > min && v < max);

  const measure = measureLabel ? ` ${measureLabel}` : '';

  return (
    <div className="boxplot">
      {/* The median rule carries no printed value, which is how a box plot is normally
          drawn - and here it also avoids stating the same number twice in two different
          roundings. The Centre cell one column over prints it at full precision. */}
      <div
        className="boxplot__track"
        role="img"
        aria-label={
          `Distribution of${measure}: minimum ${compactNumber(min)}, ` +
          `lower quartile ${compactNumber(p25)}, median ${compactNumber(median)}, ` +
          `upper quartile ${compactNumber(p75)}, maximum ${compactNumber(max)}.`
        }
      >
        {/* Whisker: the full min-to-max range, with a cap at each end so the extremes
            are marks the eye can land on rather than where a hairline stops. */}
        <div className="boxplot__whisker" />
        <div className="boxplot__cap" style={{ left: 0 }} />
        <div className="boxplot__cap" style={{ left: '100%' }} />

        {/* Dotted because these genuinely are thresholds - the reading a dotted line
            invites, and the reason gridlines in this app are solid. */}
        {fences.map(([side, v]) => (
          <div
            key={side}
            className="boxplot__fence"
            style={{ left: `${pos(v)}%` }}
            title={`Tukey ${side} fence at ${compactNumber(v)} — points beyond it are flagged as outliers`}
          />
        ))}

        {/* The middle half. No border: the fill is what separates it from the whisker,
            and a stroke around a mark adds ink that isn't data. */}
        <div
          className="boxplot__box"
          style={{ left: `${boxLeft}%`, width: `${boxWidth}%` }}
          title={`Middle half of the values: ${compactNumber(p25)} to ${compactNumber(p75)}`}
        />

        <div
          className="boxplot__median"
          style={{ left: `${medianPct}%` }}
          title={`Median ${compactNumber(median)}`}
        />

        {anomalies.map((a, i) => (
          typeof a?.value === 'number' && a.value >= min && a.value <= max ? (
            <div
              key={`${a.label}-${i}`}
              className="boxplot__anomaly"
              style={{ left: `${pos(a.value)}%` }}
              title={`${a.label}: ${compactNumber(a.value)} — flagged as an outlier`}
            />
          ) : null
        ))}
      </div>

      {/* Only the extremes are labelled under the track. A number beside every mark is
          chaos and goes unread; the quartiles get the muted line below, and the exact
          figures are in the statistics grid. */}
      <div className="boxplot__labels-bottom">
        <span className="boxplot__label boxplot__label--min">min {compactNumber(min)}</span>
        <span className="boxplot__label boxplot__label--max">max {compactNumber(max)}</span>
      </div>

      <div className="boxplot__quartiles">
        p25 {compactNumber(p25)} · p75 {compactNumber(p75)}
      </div>
    </div>
  );
}
