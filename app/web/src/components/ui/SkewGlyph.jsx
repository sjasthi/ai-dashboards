/**
 * The Center cell's mark: a stylised distribution silhouette with the median and
 * mean plotted on it as two ticks.
 *
 * The curve itself is a fixed shape per `skewLevel` (right / left / symmetric) - it
 * is not fit to the data and carries no value of its own, so it's drawn recessive
 * and stays out of the tab order. What IS real: the gap between the two ticks, which
 * comes straight from `skewRatio` (mean-minus-median scaled by IQR, the same number
 * behind the "Right-skewed" chip beside it) - so a bigger gap on screen always means
 * a bigger computed skew, never just a bigger illustration.
 *
 * One hue, two shades, per the dumbbell convention: median (the established mark -
 * Range's box plot already draws it) is the solid tick; mean is the same hue dashed,
 * so the pair reads as "two views of one series" rather than two competing colours.
 */
export default function SkewGlyph({ skewLevel, skewRatio, medianTitle, meanTitle }) {
  if (!skewLevel || typeof skewRatio !== 'number' || !Number.isFinite(skewRatio)) return null;

  const SHAPES = {
    right: { peakX: 70, d: 'M2,30 C30,30 40,4 70,4 C100,4 105,26 140,29 C165,31 185,31 198,31' },
    left: { peakX: 130, d: 'M198,30 C170,30 160,4 130,4 C100,4 95,26 60,29 C35,31 15,31 2,31' },
    symmetric: { peakX: 100, d: 'M2,30 C40,30 55,4 100,4 C145,4 160,30 198,30' },
  };
  const shape = SHAPES[skewLevel] || SHAPES.symmetric;

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const medianX = shape.peakX;
  const meanX = clamp(shape.peakX + clamp(skewRatio * 40, -60, 60), 10, 190);

  return (
    <svg
      className="skew-glyph"
      width="100%"
      height="34"
      viewBox="0 0 200 34"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <path d={shape.d} className="skew-glyph__curve" fill="none" />
      <line x1={medianX} y1="2" x2={medianX} y2="32" className="skew-glyph__tick skew-glyph__tick--median">
        <title>{medianTitle}</title>
      </line>
      <line x1={meanX} y1="2" x2={meanX} y2="32" className="skew-glyph__tick skew-glyph__tick--mean">
        <title>{meanTitle}</title>
      </line>
    </svg>
  );
}
