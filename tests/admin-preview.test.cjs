const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

/* The preview tab is opened synchronously, filled with the fetched HTML, then
   detached from the admin window. These checks guard the two behaviours that
   are easy to lose in a refactor: releasing window.opener and resetting the
   reused tab's scroll position. */
function previewHarness() {
  const elements = new Map();
  const calls = { scrollTo: [], written: [], opened: 0, closed: 0 };

  const element = () => ({
    value: '',
    textContent: '',
    innerHTML: '',
    checked: false,
    disabled: false,
    files: [],
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    removeAttribute() {},
    setAttribute() {},
    querySelector() { return element(); },
    querySelectorAll() { return []; },
  });

  const previewWindow = {
    opener: { name: 'admin' },
    closed: false,
    document: {
      write(html) { calls.written.push(html); },
      open() {},
      close() { calls.closed += 1; },
    },
    scrollTo(x, y) { calls.scrollTo.push([x, y]); },
    close() { this.closed = true; },
  };

  const document = {
    querySelector(selector) {
      return selector === 'button[onclick="openEventPreview()"]' ? element() : null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
  };

  const sandbox = {
    document,
    console,
    Date,
    FormData,
    URL,
    JSON,
    Number,
    Object,
    String,
    Math,
    setTimeout,
    localStorage: { getItem() { return 'token'; }, removeItem() {} },
    fetch: async () => ({ ok: true, status: 200, text: async () => '<!doctype html><title>preview</title>' }),
    open() { calls.opened += 1; return previewWindow; },
    location: { href: '' },
  };
  const context = vm.createContext(sandbox);
  // The admin script attaches its globals to `window`; keep it the same object
  // as the VM global so `window.openEventPreview` is reachable from the test.
  context.window = context;

  vm.runInContext(readFileSync(join(__dirname, '../app/static/js/admin.js'), 'utf8'), context);
  // admin_events.html loads the shared Excel-import controller before its inline
  // script, so the harness must do the same for the inline init to resolve.
  vm.runInContext(readFileSync(join(__dirname, '../app/static/js/team_import.js'), 'utf8'), context);
  const source = readFileSync(join(__dirname, '../app/templates/pages/admin_events.html'), 'utf8')
    .match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInContext(source, context);

  return { context, calls, previewWindow };
}

test('preview releases window.opener so the page cannot script the admin tab', async () => {
  const { context, previewWindow } = previewHarness();
  await context.openEventPreview();
  assert.equal(previewWindow.opener, null);
});

test('preview resets the reused tab scroll position and writes the fetched page', async () => {
  const { context, calls, previewWindow } = previewHarness();
  await context.openEventPreview();
  assert.deepEqual(calls.scrollTo.at(-1), [0, 0]);
  assert.equal(calls.opened, 1);
  assert.equal(calls.closed, 1);
  assert.ok(calls.written.at(-1).includes('<title>preview</title>'));
  assert.equal(previewWindow.closed, false);
});

test('preview reports a blocked popup instead of throwing', async () => {
  const { context, calls } = previewHarness();
  context.open = () => null;
  await context.openEventPreview();
  const feedback = context.document.getElementById('event-form-feedback');
  assert.equal(calls.scrollTo.length, 0);
  assert.match(feedback.textContent, /popup/i);
});
