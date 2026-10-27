const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const payload = '<img src=x onerror=alert(1)>';

function adminPage(file, responses) {
  const elements = new Map();
  const document = {
    querySelector() { return null; },
    addEventListener() {},
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: '', innerHTML: '', textContent: '', classList: { add() {}, remove() {} }, addEventListener() {}, querySelector() { return { value: '', textContent: '', innerHTML: '', disabled: false, files: [], classList: { add() {}, remove() {} }, addEventListener() {}, setAttribute() {} }; }, querySelectorAll() { return []; } });
      return elements.get(id);
    },
  };
  const context = vm.createContext({ document, Date, console, localStorage: { getItem() { return 'test'; } }, fetch: async url => ({ ok: true, status: 200, json: async () => ({ success: true, data: responses(url) }) }) });
  context.window = context;
  vm.runInContext(readFileSync(join(__dirname, '../app/static/js/admin.js'), 'utf8'), context);
  // admin_events.html loads the shared Excel-import controller before its inline
  // script, so the harness must do the same for the inline init to resolve.
  vm.runInContext(readFileSync(join(__dirname, '../app/static/js/team_import.js'), 'utf8'), context);
  const source = readFileSync(join(__dirname, '../app/templates/pages', file), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInContext(source, context);
  return { context, elements };
}

test('public supporter names and payment text are escaped before rendering admin transactions', async () => {
  const { context, elements } = adminPage('admin_transactions.html', url => url.endsWith('/events') ? [{ id: 'event', name: payload }] : [{ id: 'payment', event_name: payload, team_name: payload, package: payload, supporter_name: payload, supporter_phone: payload, status: 'PENDING', amount: 1000, votes: 1 }]);
  await context.loadTransactions();
  const html = elements.get('transactions-body').innerHTML;
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes(payload));
  assert.ok(!elements.get('filter-event').innerHTML.includes(payload));
});

test('audit entries render untrusted actors, details and identifiers as text', async () => {
  const { context, elements } = adminPage('admin_audit_logs.html', () => [{ actor_name: payload, action: payload, entity_type: payload, entity_id: payload, detail: { name: payload }, ip_address: payload }]);
  await context.loadAuditLogs();
  const html = elements.get('audit-logs-list').innerHTML;
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes(payload));
});

test('event names are text and quoted identifiers remain one JavaScript argument', async () => {
  const id = '\");globalThis.injected=true;//';
  const { context, elements } = adminPage('admin_events.html', () => [{ id, name: payload, status: 'DRAFT' }]);
  await context.loadEvents();
  const html = elements.get('events-body').innerHTML;
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes(payload));
  const attribute = html.match(/onclick="([^"]*)"/)[1];
  const handler = attribute.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  let selected;
  context.editEvent = value => { selected = value; };
  vm.runInContext(handler, context);
  assert.equal(selected, id);
  assert.equal(context.injected, undefined);
});
