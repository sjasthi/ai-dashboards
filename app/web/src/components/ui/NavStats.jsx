import { compactNumber } from '../../format';

/**
 * The three usage counters, sized to fit the nav's 34px content band
 * (label-over-value stack, not a card).
 *
 * Visible labels are shortened ("Files", not "Files processed") for space; the
 * full wording is what a screen reader announces and what a hover reveals.
 */

const ICON = {
  users: (
    <>
      <path d="M16 20v-1.6a3.6 3.6 0 0 0-3.6-3.6H6.6A3.6 3.6 0 0 0 3 18.4V20" />
      <circle cx="9.5" cy="7.6" r="3.6" />
      <path d="M21 20v-1.6a3.6 3.6 0 0 0-2.7-3.5" />
      <path d="M15.4 4.2a3.6 3.6 0 0 1 0 6.8" />
    </>
  ),
  files: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9 17v-2.5M12 17v-4M15 17v-1.5" />
    </>
  ),
  reports: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <path d="M8 16v-3.5M12 16V8M16 16v-5.5" />
    </>
  ),
};

// title is the full wording the shortened label stands in for.
const STATS = [
  { key: 'users', label: 'Users', title: 'Users', field: 'users' },
  { key: 'files', label: 'Files', title: 'Files processed', field: 'files_processed' },
  { key: 'reports', label: 'Reports', title: 'Reports built', field: 'reports_built' },
];

export default function NavStats({ stats }) {
  return (
    <div className="app-nav__stats" role="group" aria-label="Usage">
      {STATS.map(({ key, label, title, field }) => {
        const value = stats?.[field];
        // An absent counter is an em-dash rather than a zero, matching StatTile:
        // a number we could not read and a number that is zero are different claims.
        const display = value === null || value === undefined ? '—' : compactNumber(value);

        return (
          <div key={key} className="nav-stat" title={title}>
            <svg
              className="nav-stat__icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              {ICON[key]}
            </svg>
            <div className="nav-stat__text">
              <span className="nav-stat__label">{label}</span>
              <span className="nav-stat__value">
                <span className="nav-stat__sr">{title}: </span>
                {display}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
