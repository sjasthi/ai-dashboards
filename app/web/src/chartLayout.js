/**
 * Plotly presentation layer.
 *
 * The figure itself is built server-side (app/data/chart_builder.py). This module only
 * dresses it: recessive chrome, and a small number of *selective* direct labels driven
 * by the computed statistics. Never a value on every point - a label beside every dot
 * is chaos and goes unread; the axis, the tooltip and the table carry the rest.
 *
 * Plotly takes literal colours, so these mirror tokens.css. Keep them in step.
 */
const INK = '#111827';
const INK_SECONDARY = '#6B7280';
const INK_MUTED = '#9CA3AF';
const SURFACE = '#FFFFFF';
const GRID = '#EEF0F2';
const ACCENT = '#2563EB';
const FONT = "'Inter', system-ui, -apple-system, sans-serif";

/** Chart types with no cartesian axes to decorate. */
const NON_CARTESIAN = new Set(['pie', 'box']);

export const PLOT_CONFIG = {
  responsive: true,
  displayModeBar: false,
  // Tooltips enhance, they never gate: every value is also in the table view.
  displaylogo: false,
};

export function buildLayout(chart, stats, { compact = false } = {}) {
  const trace = chart?.data?.[0];
  const traceType = trace?.type;
  const base = chart?.layout || {};

  // chart_builder turns bars sideways when the category names are too long to sit
  // under a vertical bar. That swaps which axis carries the measure, so everything
  // below that points at a measure - the median line, the min/max labels - has to
  // follow it across.
  const horizontal = trace?.orientation === 'h';

  const axis = {
    gridcolor: GRID,
    // Solid hairlines. Dashing adds noise and reads as "threshold" when it is a grid.
    griddash: 'solid',
    gridwidth: 1,
    zeroline: false,
    linecolor: GRID,
    tickfont: { size: compact ? 9 : 11, color: INK_SECONDARY },
    title: { font: { size: compact ? 10 : 12, color: INK_SECONDARY } },
  };

  const layout = {
    ...base,
    autosize: true,
    paper_bgcolor: SURFACE,
    plot_bgcolor: SURFACE,
    font: { family: FONT, color: INK, size: compact ? 10 : 12 },
    hoverlabel: {
      bgcolor: SURFACE,
      bordercolor: GRID,
      font: { family: FONT, size: 12, color: INK },
    },
    margin: compact
      ? { l: 34, r: 10, t: 6, b: 24 }
      : { l: 56, r: 24, t: 16, b: 52 },
    // The card's own heading already names the chart; repeating it inside the plot
    // wastes the vertical space the axis band needs.
    title: undefined,
    showlegend: false,
  };

  if (!NON_CARTESIAN.has(traceType)) {
    layout.xaxis = { ...(base.xaxis || {}), ...axis };
    layout.yaxis = { ...(base.yaxis || {}), ...axis };
    if (compact) {
      layout.xaxis.title = undefined;
      layout.yaxis.title = undefined;

      // A compare card is a 130px thumbnail. A categorical y-axis draws every one of
      // its ticks - Plotly thins a numeric axis but never a categorical one - so nine
      // category names stack into an unreadable smear and steal half the card's width
      // from the bars. The card's own heading names the report; the full view has the
      // labels.
      if (horizontal) layout.yaxis.showticklabels = false;
    }
  }

  // A sideways bar chart grows downward, not across: its height has to come from the
  // number of categories, or nine bars get squeezed into a fixed 420px and the names
  // beside them start touching. Bounded at both ends so one category isn't a lone fat
  // slab and thirty don't run off the page.
  if (horizontal && !compact) {
    const n = trace?.y?.length || 0;
    layout.height = Math.min(900, Math.max(320, n * 44 + 90));
  }

  if (!compact && stats?.available && !NON_CARTESIAN.has(traceType)) {
    const shape = medianLine(traceType, stats, horizontal);
    if (shape) layout.shapes = [...(base.shapes || []), shape.line];
    if (shape?.annotation) layout.annotations = [...(base.annotations || []), shape.annotation];

    const extremes = extremeLabels(chart, traceType, horizontal);
    if (extremes.length) {
      layout.annotations = [...(layout.annotations || []), ...extremes];
    }
  }

  return layout;
}

/**
 * A reference line at the median.
 *
 * The median rather than the mean: on a skewed series the mean sits away from where
 * the data actually is, so a "typical value" line drawn at the mean lands in a gap.
 * A histogram's measure is on x, as is a sideways bar chart's, so the line turns
 * vertical for both.
 */
function medianLine(traceType, stats, horizontal = false) {
  const median = stats.median;
  if (median === null || median === undefined) return null;

  const vertical = traceType === 'histogram' || horizontal;
  const line = {
    type: 'line',
    xref: vertical ? 'x' : 'paper',
    yref: vertical ? 'paper' : 'y',
    x0: vertical ? median : 0,
    x1: vertical ? median : 1,
    y0: vertical ? 0 : median,
    y1: vertical ? 1 : median,
    line: { color: INK_MUTED, width: 1, dash: 'dot' },
    layer: 'below',
  };

  const annotation = {
    xref: vertical ? 'x' : 'paper',
    yref: vertical ? 'paper' : 'y',
    x: vertical ? median : 1,
    y: vertical ? 1 : median,
    text: 'median',
    showarrow: false,
    font: { size: 10, color: INK_MUTED },
    xanchor: vertical ? 'left' : 'right',
    yanchor: vertical ? 'bottom' : 'bottom',
    xshift: vertical ? 4 : 0,
    yshift: 3,
  };

  return { line, annotation };
}

/**
 * Label the highest and lowest point, and nothing else.
 *
 * Read off the plotted arrays rather than off the stats, so the label always sits on
 * the mark the chart actually drew (chart_builder re-sorts and can cap categories).
 * A histogram's bars are binned by Plotly at render time, so there is nothing here to
 * anchor to.
 */
function extremeLabels(chart, traceType, horizontal = false) {
  if (traceType === 'histogram') return [];

  const trace = chart?.data?.[0];
  // Whichever axis holds the measure is the one to rank; the other holds the category
  // the label has to sit against.
  const measures = horizontal ? trace?.x : trace?.y;
  const categories = horizontal ? trace?.y : trace?.x;
  if (!Array.isArray(measures) || !Array.isArray(categories) || measures.length < 3) return [];

  let maxI = -1;
  let minI = -1;
  for (let i = 0; i < measures.length; i += 1) {
    const v = measures[i];
    if (typeof v !== 'number' || !Number.isFinite(v)) continue;
    if (maxI === -1 || v > measures[maxI]) maxI = i;
    if (minI === -1 || v < measures[minI]) minI = i;
  }
  if (maxI === -1 || minI === -1 || maxI === minI) return [];

  if (horizontal) {
    // Both bars grow rightward from zero, so both labels clear the bar the same way.
    return [
      barEndLabel(measures[maxI], categories[maxI]),
      barEndLabel(measures[minI], categories[minI]),
    ];
  }

  return [
    pointLabel(categories[maxI], measures[maxI], 'bottom'),
    pointLabel(categories[minI], measures[minI], 'top'),
  ];
}

/** Shared type treatment for both orientations' value labels. */
const LABEL_STYLE = {
  showarrow: false,
  // Text wears text tokens, never the series colour - the mark beside it already
  // carries identity, and a light hue is illegible as type.
  font: { size: 11, color: INK_SECONDARY, family: FONT },
  bgcolor: 'rgba(255,255,255,0.85)',
  borderpad: 2,
};

function pointLabel(x, y, yanchor) {
  return {
    ...LABEL_STYLE,
    x,
    y,
    text: formatTick(y),
    yanchor,
    yshift: yanchor === 'bottom' ? 8 : -8,
  };
}

function barEndLabel(value, category) {
  return {
    ...LABEL_STYLE,
    x: value,
    y: category,
    text: formatTick(value),
    xanchor: 'left',
    xshift: 6,
  };
}

function formatTick(value) {
  if (typeof value !== 'number') return String(value);
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
}

export { ACCENT, INK_MUTED };
