/**
 * Export helpers — chart rasterisation and file downloads.
 *
 * Chart images are rendered here rather than on the server. The figure the backend
 * sends is unstyled: every bit of its presentation - the font, the median reference
 * line, the min/max labels, the extra height a sideways bar chart needs - is applied
 * by chartLayout.js at draw time. A server-side render would therefore hand the user
 * a PDF whose chart doesn't match the one they just approved on screen, and would
 * need Kaleido, which since v1 wants a Chromium binary fetched at runtime.
 *
 * So the PNG is drawn with the real layout, in the real Plotly, and posted up.
 */

import { PLOT_CONFIG, buildLayout } from './chartLayout';

const DEFAULT_WIDTH = 920;
const DEFAULT_HEIGHT = 520;

/**
 * Rasterise one report's chart to a base64 PNG data URL.
 *
 * Drawn into a detached offscreen node rather than read off the on-screen chart:
 * Plot.jsx purges its node on unmount, so only the report currently being viewed
 * has one - and exporting several reports at once needs all of them.
 *
 * At 920x520 scale 2 the PNG is 1840px wide and prints at ~460pt, around 290 DPI.
 *
 * @returns the data URL, or null if there is no chart or the render failed. Null is
 *   a normal outcome the export is expected to survive - the document prints an
 *   explanation in place of the chart and every number still appears.
 */
export async function chartPngDataUrl(chart, stats, { width = DEFAULT_WIDTH, scale = 2 } = {}) {
  const Plotly = window.Plotly;
  if (!chart || !Plotly) return null;

  const layout = buildLayout(chart, stats);
  const height = layout.height || DEFAULT_HEIGHT;

  const node = document.createElement('div');
  node.style.cssText =
    `position:absolute;left:-10000px;top:0;width:${width}px;height:${height}px`;
  document.body.appendChild(node);

  try {
    // staticPlot: no hover handlers or drag layers to build for an image.
    await Plotly.newPlot(node, chart.data, layout, { ...PLOT_CONFIG, staticPlot: true });
    return await Plotly.toImage(node, { format: 'png', width, height, scale });
  } catch (err) {
    console.warn('Chart image could not be rendered for export:', err);
    return null;
  } finally {
    try { Plotly.purge(node); } catch { /* already gone */ }
    node.remove();
  }
}

/**
 * Chart PNGs for several reports, keyed by letter.
 *
 * One chart failing must not cost the user the other two, so each is caught
 * individually and a failure contributes a null rather than rejecting.
 */
export async function collectChartImages(reports, letters) {
  const images = {};
  for (const letter of letters) {
    const report = reports?.[letter];
    if (!report?.chart) continue;
    images[letter] = await chartPngDataUrl(report.chart, report.stats);
  }
  // Drop the nulls: the server treats a missing key and an unusable value the same
  // way, and sending nulls only inflates the request body.
  return Object.fromEntries(Object.entries(images).filter(([, v]) => v));
}

/**
 * The Distribution card's Center-cell skew curve (SkewGlyph.jsx), reimplemented
 * as flat SVG markup rather than mounted as the real component.
 *
 * Keep this in step with SkewGlyph.jsx and _macros.html's distribution_card()
 * Jinja port - three independent copies of the same shape data now (see
 * docs/EXPORT_LIVE_SYNC.md).
 */
const SKEW_SHAPES = {
  right: { peakX: 70, d: 'M2,30 C30,30 40,4 70,4 C100,4 105,26 140,29 C165,31 185,31 198,31' },
  left: { peakX: 130, d: 'M198,30 C170,30 160,4 130,4 C100,4 95,26 60,29 C35,31 15,31 2,31' },
  symmetric: { peakX: 100, d: 'M2,30 C40,30 55,4 100,4 C145,4 160,30 198,30' },
};

/**
 * Rasterise the skew curve to a PNG for the PDF, which cannot draw inline SVG at
 * all (unlike the chart, which only has theming it can't match, this one it can't
 * render at any fidelity - confirmed by rendering, not assumed).
 *
 * The colours/stroke-widths below are inlined as SVG presentation attributes
 * rather than read from dashboard.css, because a data: SVG rasterised through
 * <img>/canvas is its own document - it never sees the app's external stylesheet
 * or CSS custom properties. Values copied from dashboard.css's .skew-glyph__*
 * rules (var(--color-text-muted) = #9CA3AF, var(--color-series-1) = #0072B2).
 *
 * @returns the data URL, or null if this report has no skew to draw (matches
 *   SkewGlyph.jsx's own early return).
 */
export function skewGlyphPngDataUrl(stats) {
  const level = stats?.skew_level;
  const ratio = stats?.skew_ratio;
  if (!level || typeof ratio !== 'number' || !Number.isFinite(ratio)) {
    return Promise.resolve(null);
  }

  const shape = SKEW_SHAPES[level] || SKEW_SHAPES.symmetric;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const medianX = shape.peakX;
  const meanX = clamp(shape.peakX + clamp(ratio * 40, -60, 60), 10, 190);

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="34" viewBox="0 0 200 34">
    <path d="${shape.d}" fill="none" stroke="#9CA3AF" stroke-width="1.5" opacity="0.35" />
    <line x1="${medianX}" y1="2" x2="${medianX}" y2="32" stroke="#0072B2" stroke-width="2" />
    <line x1="${meanX}" y1="2" x2="${meanX}" y2="32" stroke="#0072B2" stroke-width="2" stroke-dasharray="3 2" opacity="0.6" />
  </svg>`;

  // 3x the SVG's own units for a crisp result at the small size this prints at -
  // no DPI target to hit here the way the chart has one, since this never fills
  // more than a third of a page width.
  const SCALE = 3;
  const width = 200 * SCALE;
  const height = 34 * SCALE;

  return new Promise((resolve) => {
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      canvas.getContext('2d').drawImage(img, 0, 0, width, height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/png'));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    img.src = url;
  });
}

/**
 * Skew-curve PNGs for several reports, keyed by letter. Same failure-isolation
 * and null-dropping behaviour as collectChartImages.
 */
export async function collectDistImages(reports, letters) {
  const images = {};
  for (const letter of letters) {
    const report = reports?.[letter];
    if (!report?.stats) continue;
    images[letter] = await skewGlyphPngDataUrl(report.stats);
  }
  return Object.fromEntries(Object.entries(images).filter(([, v]) => v));
}

/** Save a blob to the user's downloads. */
export function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename || 'export';
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking synchronously cancels the download in Firefox.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
