/** Keep the ivory identity on first visit; dark mode is an explicit preference. */
(function () {
  'use strict';
  const storageKey = 'theme';
  const root = document.documentElement;
  let current = 'light';
  try { current = localStorage.getItem(storageKey) === 'dark' ? 'dark' : 'light'; } catch (_) {}

  function apply() {
    const dark = current === 'dark';
    root.classList.toggle('dark', dark);
    document.querySelectorAll('.theme-toggle').forEach(button => {
      button.setAttribute('aria-label', dark ? 'Aktifkan tema terang' : 'Aktifkan tema gelap');
      button.setAttribute('aria-pressed', String(dark));
      const icon = button.querySelector('.theme-toggle-icon');
      if (icon) icon.innerHTML = dark
        ? '<circle cx="10" cy="10" r="4"/><path d="M10 1v2m0 14v2M1 10h2m14 0h2M3.6 3.6 5 5m10 10 1.4 1.4M3.6 16.4 5 15M15 5l1.4-1.4" fill="none" stroke="currentColor" stroke-width="1.5"/>'
        : '<path d="M16.8 12.3A7.2 7.2 0 0 1 7.7 3.2 7.2 7.2 0 1 0 16.8 12.3Z"/>';
    });
  }
  function init() {
    apply();
    document.querySelectorAll('.theme-toggle').forEach(button => button.addEventListener('click', () => {
      current = current === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(storageKey, current); } catch (_) {}
      apply();
    }));
  }
  root.classList.toggle('dark', current === 'dark');
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
