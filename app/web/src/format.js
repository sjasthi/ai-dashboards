/**
 * Display formatting shared by the dashboard.
 *
 * Numbers on a dashboard are read at a glance, so these lean on compaction and
 * locale separators rather than raw precision - the exact value is always still
 * reachable in the data table.
 */

/** 12148 -> "12,148"; 4200000 -> "4.2M". Whole numbers never grow a ".0". */
export function compactNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';

  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return trimZero(n / 1_000_000_000) + 'B';
  if (abs >= 1_000_000) return trimZero(n / 1_000_000) + 'M';
  if (abs >= 100_000) return trimZero(n / 1_000) + 'K';
  if (Number.isInteger(n)) return n.toLocaleString();
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function trimZero(n) {
  const s = n.toFixed(1);
  return s.endsWith('.0') ? s.slice(0, -2) : s;
}

/** Full precision with separators, for table cells. */
export function exactNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n)
    ? n.toLocaleString()
    : n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** 2411008 -> "2.3 MB". Binary units, matching what an OS file listing reports. */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—';
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${n} B`;

  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // One decimal below 10 — 1.4 MB reads better than 1 MB — and none above it.
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/**
 * Shorten to `max` characters, dropping from the middle: "Sales_2024…Q2_Region".
 *
 * The middle rather than the tail because worksheets in one workbook routinely
 * share a long prefix (Sales_2024_Q1 / Sales_2024_Q2) - truncating the end would
 * render those identically.
 *
 * 20 sits just under the ~21 characters a sheet chip fits at four per row; going
 * wider hands the job to the CSS ellipsis, which would clip a second time and
 * leave two sets of dots in one label.
 */
export function truncateMiddle(text, max = 20) {
  const s = String(text ?? '');
  if (s.length <= max) return s;
  const head = Math.ceil((max - 1) / 2);
  const tail = max - 1 - head;
  return `${s.slice(0, head)}…${tail ? s.slice(-tail) : ''}`;
}

/** "+3.2%" / "−3.2%" — a real minus sign, not a hyphen. */
export function signedPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const n = Number(value);
  const sign = n > 0 ? '+' : n < 0 ? '−' : '';
  return `${sign}${Math.abs(n).toFixed(1)}%`;
}

/** ▲ / ▼ / — matched to a trend direction. */
export function directionGlyph(direction) {
  if (direction === 'up') return '▲';
  if (direction === 'down') return '▼';
  return '—';
}

/**
 * An ISO timestamp as a local wall-clock time: "11:38 AM".
 *
 * hour12 is set explicitly rather than left to the locale. Without it the same
 * build renders "11:38 AM" or "23:38" depending on the reader's regional
 * settings, so a screenshot and the machine that produced it could disagree -
 * and a report stamped "11:38" with no meridiem is genuinely ambiguous.
 */
export function clockTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString(undefined, {
    hour: 'numeric', minute: '2-digit', hour12: true,
  });
}

/**
 * Render a table cell value.
 *
 * The API sends dates as ISO strings (pandas' JSON writer), so a value that parses
 * as a full ISO timestamp gets shortened back to something readable.
 */
export function cellValue(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return exactNumber(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';

  const s = String(value);
  const isoMatch = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):\d{2}/.exec(s);
  if (isoMatch) {
    const [, date, hh, mm] = isoMatch;
    return hh === '00' && mm === '00' ? date : `${date} ${hh}:${mm}`;
  }
  return s;
}

/** "order_id_count" -> "Order Id (count)". Mirrors chart_builder.humanize_column. */
const AGG_WORDS = {
  mean: 'avg',
  sum: 'total',
  count: 'count',
  min: 'min',
  max: 'max',
  median: 'median',
  std: 'std dev',
  nunique: 'unique count',
};

export function humanizeColumn(name) {
  if (!name) return '';
  let base = name;
  let qualifier = null;
  for (const [suffix, word] of Object.entries(AGG_WORDS)) {
    if (name.endsWith(`_${suffix}`)) {
      base = name.slice(0, -(suffix.length + 1));
      qualifier = word;
      break;
    }
  }
  const label = base
    .replace(/_/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return qualifier ? `${label} (${qualifier})` : label;
}
