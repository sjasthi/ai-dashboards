import React, { useMemo, useState } from 'react';
import Card from './Card';
import { cellValue, humanizeColumn } from '../../format';

/**
 * The chart's table twin.
 *
 * Every value the chart encodes with position or length is also readable here as
 * text, which is what keeps the page usable for a reader who can't resolve the
 * chart - and what discharges the low-contrast series colours.
 */
export default function DataTable({ columns, rows, totalRows, truncated }) {
  const [sort, setSort] = useState({ column: null, direction: 'asc' });

  // Decided per column across the visible rows: a column of numbers gets right
  // alignment and equal-width digits so the digits line up down the page.
  const numericColumns = useMemo(() => {
    const set = new Set();
    for (const col of columns) {
      const sample = rows.find((r) => r[col] !== null && r[col] !== undefined);
      if (sample && typeof sample[col] === 'number') set.add(col);
    }
    return set;
  }, [columns, rows]);

  const sorted = useMemo(() => {
    if (!sort.column) return rows;
    const dir = sort.direction === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[sort.column];
      const bv = b[sort.column];
      // Nulls sort last in both directions - they are absent data, not a low value.
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, sort]);

  const toggleSort = (col) => {
    setSort((prev) =>
      prev.column === col
        ? { column: col, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { column: col, direction: 'asc' }
    );
  };

  const ariaSort = (col) => {
    if (sort.column !== col) return 'none';
    return sort.direction === 'asc' ? 'ascending' : 'descending';
  };

  if (!columns.length || !rows.length) {
    return (
      <Card>
        <div className="empty-state">No rows to show for this report.</div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="table-wrap">
        <table className="data-table">
          <caption className="sr-only">Report data, sortable by column</caption>
          <thead>
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  scope="col"
                  className={numericColumns.has(col) ? 'is-numeric' : undefined}
                  aria-sort={ariaSort(col)}
                  onClick={() => toggleSort(col)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      toggleSort(col);
                    }
                  }}
                  tabIndex={0}
                >
                  {humanizeColumn(col)}
                  {sort.column === col && (
                    <span className="data-table__sort" aria-hidden="true">
                      {sort.direction === 'asc' ? '↑' : '↓'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => {
                  const text = cellValue(row[col]);
                  return (
                    <td key={col} className={numericColumns.has(col) ? 'is-numeric' : undefined}>
                      {text === null ? <span className="data-table__null">null</span> : text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {truncated && (
        <div className="table-footnote">
          Showing the first {rows.length.toLocaleString()} of {totalRows.toLocaleString()} rows.
          The full report is held server-side.
        </div>
      )}
    </Card>
  );
}
