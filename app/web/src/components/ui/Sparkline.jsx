import React from 'react';

/**
 * A 12-point shape cue for a stat tile - the line is drawn in the de-emphasis grey
 * with only the final point in the accent, so the tile reads as "a number, trending"
 * rather than as a second chart competing with the real one.
 *
 * Purely decorative: it carries no values the page doesn't state elsewhere, so it is
 * hidden from assistive tech rather than given a misleading label.
 */
export default function Sparkline({ points, width = 96, height = 26 }) {
  if (!Array.isArray(points) || points.length < 2) return null;

  const values = points.filter((v) => typeof v === 'number' && Number.isFinite(v));
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 3;
  const innerW = width - pad * 2;
  const innerH = height - pad * 2;

  const coords = values.map((v, i) => [
    pad + (i / (values.length - 1)) * innerW,
    pad + innerH - ((v - min) / span) * innerH,
  ]);

  const d = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lastX, lastY] = coords[coords.length - 1];

  return (
    <svg
      className="stat-tile__spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden="true"
      focusable="false"
    >
      <path d={d} fill="none" stroke="var(--color-text-muted)" strokeWidth="1.5"
            strokeLinejoin="round" strokeLinecap="round" opacity="0.55" />
      <circle cx={lastX} cy={lastY} r="2.5" fill="var(--color-accent)" />
    </svg>
  );
}
