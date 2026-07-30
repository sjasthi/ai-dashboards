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
