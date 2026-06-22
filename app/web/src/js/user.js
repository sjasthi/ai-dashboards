/**
 * user.js — user menu dropdown and auth state.
 *
 * Replace fakeSignIn / fakeSignOut with your real auth provider:
 *   - Clerk:    https://clerk.com
 *   - Auth0:    https://auth0.com
 *   - Supabase: https://supabase.com/docs/guides/auth
 */
import { state } from './state.js';
import { escHtml } from './utils.js';

// ---- Dropdown open/close ------------------------------------------------

export function toggleUserMenu() {
  const dropdown = document.getElementById('userDropdown');
  if (dropdown.classList.contains('open')) {
    closeUserMenu();
  } else {
    renderUserDropdown();
    dropdown.classList.add('open');
  }
}

export function closeUserMenu() {
  document.getElementById('userDropdown')?.classList.remove('open');
}

// ---- Render dropdown content --------------------------------------------

function renderUserDropdown() {
  const content = document.getElementById('userDropdownContent');
  if (!content) return;

  if (state.isLoggedIn) {
    content.innerHTML = `
      <div style="padding:10px 16px; font-size:0.75rem; color:var(--color-text-muted); border-bottom:1px solid var(--color-border);">
        Signed in as <strong>${escHtml(state.user)}</strong>
      </div>
      <button class="user-dropdown__item" id="dd-account">Account settings</button>
      <hr class="user-dropdown__divider" />
      <button class="user-dropdown__item danger" id="dd-signout">Sign out</button>
    `;
    document.getElementById('dd-account')?.addEventListener('click', () => closeUserMenu());
    document.getElementById('dd-signout')?.addEventListener('click', signOut);
  } else {
    content.innerHTML = `
      <button class="user-dropdown__item" id="dd-signin">Sign in</button>
      <button class="user-dropdown__item" id="dd-signup">Create account</button>
    `;
    document.getElementById('dd-signin')?.addEventListener('click', signIn);
    document.getElementById('dd-signup')?.addEventListener('click', signIn);
  }
}

// ---- Auth stubs (replace with real provider) ----------------------------

function signIn() {
  // TODO: trigger real auth flow
  state.isLoggedIn = true;
  state.user = 'user@example.com';
  document.getElementById('userLabel').textContent = state.user;
  closeUserMenu();
}

function signOut() {
  // TODO: call auth provider sign-out
  state.isLoggedIn = false;
  state.user = null;
  document.getElementById('userLabel').textContent = 'Sign in';
  closeUserMenu();
}
