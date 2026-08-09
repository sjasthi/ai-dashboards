import Card from './Card';
import Sparkline from './Sparkline';
import { compactNumber, directionGlyph, signedPercent } from '../../format';

/**
 * label · value · optional delta · optional sparkline.
 *
 * `value` is auto-compacted when numeric (12,148 / 4.2M) and shown verbatim when it
 * is already a string. An absent value renders as an em-dash in muted ink rather
 * than as a zero, because a missing measurement and a measurement of zero are not
 * the same claim.
 */
export default function StatTile({ label, value, sublabel, delta, sparkline }) {
  const isEmpty = value === null || value === undefined || value === '';
  const display = typeof value === 'number' ? compactNumber(value) : value;

  return (
    <Card className="stat-tile">
      <div className="stat-tile__label">
        {label}
        {sublabel && <span className="stat-tile__label-sub"> — {sublabel}</span>}
      </div>
      <div className={`stat-tile__value${isEmpty ? ' stat-tile__value--empty' : ''}`}>
        {isEmpty ? '—' : display}
      </div>

      {delta && (
        <div className="stat-tile__sub">
          <span className={`stat-tile__delta stat-tile__delta--${delta.direction || 'flat'}`}>
            {directionGlyph(delta.direction)}
            {delta.pct != null && <> {signedPercent(delta.pct)}</>}
          </span>
          {delta.note && <> · {delta.note}</>}
        </div>
      )}

      {sparkline && <Sparkline points={sparkline} />}
    </Card>
  );
}
