/**
 * An anonymous, per-browser identifier, so usage stats can count people rather
 * than requests.
 *
 * This is deliberately not a login and not a fingerprint. It is a random UUID
 * kept in localStorage: clearing site data or opening a different browser makes a
 * new one, which is the right trade for a usage counter. It says nothing about
 * who the user is, and the server stores it as an opaque string.
 *
 * The value is only ever used for counting. Nothing is authorised by it -- see
 * the admin endpoints, which are token-gated precisely because a client-supplied
 * identifier can be edited from the console in a second.
 */

const STORAGE_KEY = 'aidash_client_id';

/** Fallback when localStorage is unavailable, so the id is at least stable per tab. */
let memoryId = null;

/**
 * A UUID, preferring the platform generator.
 *
 * crypto.randomUUID needs a secure context, which `http://localhost` counts as
 * but a plain-http LAN address does not -- so a demo served from another machine
 * would otherwise throw here. The fallback is not cryptographically strong and
 * does not need to be: collisions only ever inflate a usage count.
 */
function newId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `fallback-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * The current browser's id, creating and persisting one on first call.
 *
 * Every localStorage access is guarded: Safari's private mode and hardened
 * browser settings throw on read or write rather than returning null, and a
 * usage counter must never be able to break the page it is counting.
 */
export function getClientId() {
  try {
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing) return existing;
    const fresh = newId();
    window.localStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    if (!memoryId) memoryId = newId();
    return memoryId;
  }
}

/** Header bag to spread into a fetch call. */
export function clientHeaders() {
  return { 'X-Client-Id': getClientId() };
}
