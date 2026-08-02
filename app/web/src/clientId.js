/**
 * An anonymous, stable id for this browser, sent as X-Client-Id on every request.
 *
 * "Users" in the usage counters means this: a browser that has visited before, not
 * an account. There is no login in this app and adding one to count visitors would
 * be absurd, so a random UUID in localStorage is the honest unit. It says nothing
 * about who the person is - it only distinguishes "the same browser came back" from
 * "someone new arrived", which is the entire question the home page asks.
 *
 * Consequences worth knowing, none of them worth fixing here: clearing site data or
 * opening a private window looks like a new user, and the same person on a phone and
 * a laptop counts twice.
 */

const STORAGE_KEY = 'aidash_client_id';

/** Cached so a page that makes several calls doesn't re-read localStorage each time,
 *  and so the fallback below stays stable within a session. */
let cached = null;

function randomId() {
  // crypto.randomUUID needs a secure context. localhost counts as one, so this holds
  // in dev and in any https deployment; the fallback covers a plain-http host.
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `fallback-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Read this browser's id, creating and persisting one on first visit.
 *
 * Never throws. Safari with cookies blocked, private-mode quota errors, and
 * server-side rendering all make localStorage unavailable or throwing, and a
 * telemetry id is not worth breaking an upload over - the caller gets a
 * session-lifetime id instead and the counters slightly over-count new users.
 */
export function getClientId() {
  if (cached) return cached;

  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      cached = stored;
      return cached;
    }
    cached = randomId();
    window.localStorage.setItem(STORAGE_KEY, cached);
    return cached;
  } catch {
    cached = cached || randomId();
    return cached;
  }
}

/** The header every request carries. Spread into a fetch's headers. */
export function clientIdHeaders() {
  return { 'X-Client-Id': getClientId() };
}

const VISIT_KEY = 'aidash_visit';

/**
 * True once per browser session, false every time after.
 *
 * This is what makes the home page's visitor count behave the way it reads: a
 * refresh or a tab switch is the same visit, while closing the browser and coming
 * back is a new one. The mechanism is sessionStorage rather than a timer or an
 * expiry timestamp, because sessionStorage is *already* scoped to exactly that
 * lifetime - the browser clears it on exit, so there is no window to tune and
 * nothing to get wrong at a boundary.
 *
 * Note this is per tab, not per browser: opening the app in a second tab counts as
 * a second visit. That is sessionStorage's own definition of a session and is the
 * behaviour a visitor count should have anyway.
 *
 * Falls back to counting the visit when storage is unavailable. Over-counting an
 * arrival is a far better failure than a landing page that throws.
 */
export function isNewVisit() {
  return firstThisSession(VISIT_KEY);
}

const ACTIVATED_KEY = 'aidash_activated';

/**
 * True the first time an analysis succeeds in this browser session.
 *
 * The difference between a visitor and a user. Landing on the page is a visit;
 * actually putting data through the pipeline is what makes someone a user, and
 * counting the two as the same thing would make the headline number mostly
 * measure curiosity. Analysing several files in one sitting is still one user -
 * the same "once per browser session" rule the visit count uses.
 */
export function isNewlyActiveUser() {
  return firstThisSession(ACTIVATED_KEY);
}

/** True the first time this key is asked for in a browser session, false after.
 *  Falls back to true when storage is blocked - over-counting beats throwing. */
function firstThisSession(key) {
  try {
    if (window.sessionStorage.getItem(key)) return false;
    window.sessionStorage.setItem(key, '1');
    return true;
  } catch {
    return true;
  }
}
