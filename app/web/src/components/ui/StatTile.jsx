import Card from './Card';
import Sparkline from './Sparkline';
import { compactNumber, directionGlyph } from '../../format';

/**
 * label (+ optional chip) · value · optional name+delta line · optional short note ·
 * optional sparkline.
 *
 * `value` is auto-compacted when numeric (12,148 / 4.2M) and shown verbatim when it
 * is already a string. An absent value renders as an em-dash in muted ink rather
 * than as a zero, because a missing measurement and a measurement of zero are not
 * the same claim.
 *
 * `name` and `delta` share one row on purpose - "GTK ▼ −88% vs avg" is one claim
 * (which one, and how extreme), not two facts that happen to be stacked. `name` is
 * omitted outright rather than falling back to a placeholder when the axis has none
 * to give (a raw-histogram report with no grouping column): an invented "no label"
 * reads as something having gone wrong, an absent line reads as what's actually true.
 * `delta` still renders alone in that case.
 */
export default function StatTile({ label, value, chip, name, delta, note, sparkline }) {
  const isEmpty = value === null || value === undefined || value === '';
  const display = typeof value === 'number' ? compactNumber(value) : value;

  return (
    <Card className="stat-tile">
      <div className="stat-tile__label-row">
        <div className="stat-tile__label">{label}</div>
        {chip}
      </div>

      <div className={`stat-tile__value${isEmpty ? ' stat-tile__value--empty' : ''}`}>
        {isEmpty ? '—' : display}
      </div>

      {(name || delta) && (
        <div className="stat-tile__name-row">
          {name && <span className="stat-tile__name">{name}</span>}
          {delta && (
            <span className="stat-tile__delta-inline">
              {directionGlyph(delta.direction)} {delta.text}
            </span>
          )}
        </div>
      )}

      {note && <div className="stat-tile__sub">{note}</div>}

      {sparkline && <Sparkline points={sparkline} />}
    </Card>
  );
}
