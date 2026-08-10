/**
 * A single ratio against a scale, as a filled track.
 *
 * The unfilled remainder is a lighter step of the fill's own hue rather than a neutral
 * grey, so the state reads across the whole bar instead of only across the filled part
 * - at a 6% fill a grey track leaves almost nothing carrying the colour.
 *
 * `level` names the band; the caller decides where the bands fall. Passing a level of
 * null renders an empty track, which is what an unmeasurable ratio should look like:
 * not a bar at zero, which would claim the value was measured and came back nothing.
 */
export default function Meter({ value, max = 100, level = null, label }) {
  const measured = typeof value === 'number' && Number.isFinite(value) && level;
  const pct = measured ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;

  return (
    <div
      className={`meter${level ? ` meter--${level}` : ''}`}
      role="meter"
      aria-valuenow={measured ? Math.round(value) : undefined}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
    >
      <div className="meter__fill" style={{ width: `${pct}%` }} />
    </div>
  );
}
