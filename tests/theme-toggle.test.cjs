const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/theme-toggle.js', 'utf8');
function theme(stored, blocked = false) {
  const classes = new Set();
  const attributes = {};
  let click;
  const button = { setAttribute: (key, value) => attributes[key] = value,
    querySelector: () => null, addEventListener: (_, handler) => click = handler };
  vm.runInNewContext(source, {
    document: { readyState: 'complete', documentElement: { classList: {
      toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
    } }, querySelectorAll: () => [button] },
    window: { matchMedia: () => ({ matches: true }) },
    localStorage: { getItem() { if (blocked) throw Error('blocked'); return stored; },
      setItem(_, value) { if (blocked) throw Error('blocked'); stored = value; } },
  });
  return { classes, attributes, click: () => click(), stored: () => stored };
}
test('first visit and legacy system preference keep ivory even on a dark OS', () => {
  for (const preference of [null, 'system', 'invalid']) {
    const state = theme(preference);
    assert.equal(state.classes.has('dark'), false);
    assert.equal(state.attributes['aria-label'], 'Aktifkan tema gelap');
  }
});
test('explicit theme selection persists and has an accessible toggle state', () => {
  const state = theme('dark');
  assert.equal(state.classes.has('dark'), true);
  state.click();
  assert.equal(state.stored(), 'light');
  assert.equal(state.attributes['aria-pressed'], 'false');
  state.click();
  assert.equal(state.stored(), 'dark');
});
test('theme control still works when storage is unavailable', () => {
  const state = theme(null, true);
  state.click();
  assert.equal(state.classes.has('dark'), true);
  state.click();
  assert.equal(state.classes.has('dark'), false);
});
