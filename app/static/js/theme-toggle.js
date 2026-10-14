/**
 * Theme toggle — persists user preference in localStorage and respects
 * the OS-level color-scheme preference on first visit.
 *
 * Toggles the `dark` class on <html>, which is what Tailwind's
 * `darkMode: 'class'` strategy keys off of.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "theme";
  var DARK_CLASS = "dark";
  var THEME_DARK = "dark";
  var THEME_LIGHT = "light";
  var THEME_SYSTEM = "system";

  function getStoredTheme() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  }

  function setStoredTheme(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      // localStorage unavailable (private mode, etc.) — ignore
    }
  }

  function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? THEME_DARK
      : THEME_LIGHT;
  }

  function applyTheme(theme) {
    var resolved = theme === THEME_SYSTEM ? getSystemTheme() : theme;
    var html = document.documentElement;
    if (resolved === THEME_DARK) {
      html.classList.add(DARK_CLASS);
    } else {
      html.classList.remove(DARK_CLASS);
    }
  }

  function updateToggleIcon(theme) {
    var resolved = theme === THEME_SYSTEM ? getSystemTheme() : theme;
    var icon = document.querySelector(".theme-toggle-icon");
    if (!icon) return;
    // The icon is a sun/moon glyph; we swap the SVG path content.
    if (resolved === THEME_DARK) {
      // Moon icon
      icon.innerHTML =
        '<path d="M10 2a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1Z"/>' +
        '<path d="M4.22 3.22a.75.75 0 0 1 1.06 0l.75.75a.75.75 0 1 1-1.06 1.06l-.75-.75a.75.75 0 0 1 0-1.06Z"/>' +
        '<path d="M15.36 4.28a.75.75 0 0 1 1.06 0l.75.75a.75.75 0 1 1-1.06 1.06l-.75-.75a.75.75 0 0 1 0-1.06Z"/>' +
        '<path d="M10 14a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/>' +
        '<path d="M2 10a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2H3a1 1 0 0 1-1-1Z"/>' +
        '<path d="M16 10a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2h-1a1 1 0 0 1-1-1Z"/>' +
        '<path d="M4.22 15.78a.75.75 0 0 1 0-1.06l.75-.75a.75.75 0 1 1 1.06 1.06l-.75.75a.75.75 0 0 1-1.06 0Z"/>' +
        '<path d="M14.95 15.78a.75.75 0 0 1 0-1.06l.75-.75a.75.75 0 1 1 1.06 1.06l-.75.75a.75.75 0 0 1-1.06 0Z"/>';
    } else {
      // Sun icon
      icon.innerHTML =
        '<path d="M10 5a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V6a1 1 0 0 1 1-1Z"/>' +
        '<path d="M10 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>' +
        '<path d="M4.22 10a.75.75 0 0 1 .75-.75H5a.75.75 0 1 1 0 1.5h-.03A.75.75 0 0 1 4.22 10Z"/>' +
        '<path d="M15.78 10a.75.75 0 0 1 .75-.75h.03a.75.75 0 1 1 0 1.5H16a.75.75 0 0 1-.75-.75Z"/>' +
        '<path d="M4.22 4.22a.75.75 0 0 1 1.06 0l.5.5a.75.75 0 1 1-1.06 1.06l-.5-.5a.75.75 0 0 1 0-1.06Z"/>' +
        '<path d="M14.95 14.95a.75.75 0 0 1 1.06 0l.5.5a.75.75 0 1 1-1.06 1.06l-.5-.5a.75.75 0 0 1 0-1.06Z"/>' +
        '<path d="M4.22 15.78a.75.75 0 0 1 0-1.06l.5-.5a.75.75 0 1 1 1.06 1.06l-.5.5a.75.75 0 0 1-1.06 0Z"/>' +
        '<path d="M15.78 4.22a.75.75 0 0 1 0 1.06l-.5.5a.75.75 0 1 1-1.06-1.06l.5-.5a.75.75 0 0 1 1.06 0Z"/>';
    }
  }

  function cycleTheme() {
    var current = getStoredTheme();
    var next;
    if (current === THEME_DARK) {
      next = THEME_LIGHT;
    } else if (current === THEME_LIGHT) {
      next = THEME_SYSTEM;
    } else {
      next = THEME_DARK;
    }
    setStoredTheme(next);
    applyTheme(next);
    updateToggleIcon(next);
  }

  function init() {
    var theme = getStoredTheme() || THEME_SYSTEM;
    applyTheme(theme);
    updateToggleIcon(theme);

    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", cycleTheme);
    }

    // Listen for OS preference changes when in system mode
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", function () {
        if (getStoredTheme() === THEME_SYSTEM) {
          applyTheme(THEME_SYSTEM);
          updateToggleIcon(THEME_SYSTEM);
        }
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
