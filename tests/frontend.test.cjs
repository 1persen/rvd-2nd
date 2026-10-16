const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function load(file, globals = {}) {
  const context = vm.createContext({ Intl, Date, crypto: require('node:crypto').webcrypto, console: { error() {} }, setInterval: () => 1, clearInterval() {}, ...globals });
  vm.runInContext(readFileSync(join(__dirname, '../app/static/js', file), 'utf8'), context);
  return context;
}

function checkout(globals = {}) {
  const context = load('vote.js', globals);
  const form = context.votePage({ id: 'team' }, { id: 'event', is_voting_open: true, price_per_vote: 1000 }, '/payments', '/payments');
  const dialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  form.$refs = { paymentDialog: dialog };
  form.voter = { name: 'Preview supporter', phone: '81234567890', email: 'sandbox-test@example.com' };
  return { form, dialog };
}

test('checkout rejects fractional, zero and excessive vote quantities', () => {
  for (const qty of [0, -1, 1.5, 10001]) {
    const { form, dialog } = checkout();
    form.qty = qty;
    form.nextStep();
    assert.equal(dialog.open, false);
    assert.ok(form.error);
  }
});

test('closed voting cannot enter payment confirmation', () => {
  const { form, dialog } = checkout();
  form.event.closes_at = '2000-01-01T00:00:00Z';
  form.nextStep();
  assert.equal(dialog.open, false);
  assert.match(form.error, /Voting/);
});

test('checkout requires a valid payer email before confirmation', () => {
  for (const email of ['', 'invalid', 'a b@example.com', 'a'.repeat(40) + '@example.com']) {
    const { form, dialog } = checkout();
    form.voter.email = email;
    form.nextStep();
    assert.equal(dialog.open, false);
    assert.match(form.error, /email/);
  }
});

test('invoice rejection leaves the confirmation available for retry', async () => {
  const { form, dialog } = checkout({ fetch: async () => ({ ok: false, json: async () => ({ success: false, message: 'Service unavailable' }) }) });
  form.nextStep();
  await form.createInvoice();
  assert.equal(form.payment, null);
  assert.equal(form.error, 'Service unavailable');
  assert.equal(form.isLoading, false);
  assert.equal(dialog.open, true);
});

test('pending invoices can resume without creating another payment', async () => {
  let calls = 0;
  const { form, dialog } = checkout({ fetch: async () => { calls++; return { ok: true, json: async () => ({ success: true, data: { id: 'preview-payment', status: 'PENDING', is_mock: true, votes: 1, amount: 1000 } }) }; } });
  form.nextStep();
  await form.createInvoice();
  form.closeModal();
  form.resumePayment();
  assert.equal(calls, 1);
  assert.equal(form.payment.id, 'preview-payment');
  assert.equal(dialog.open, true);
  assert.equal(form.showMockSimulator, true);
  form.applyPayment({ id: 'preview-payment', status: 'SUCCESS' });
  assert.equal(form.showSuccessModal, true);
  assert.equal(form.pollInterval, null);
});

test('event API failures show a retryable error instead of a false empty list', async () => {
  const context = load('events.js', { fetch: async () => ({ ok: false, json: async () => ({ success: false, message: 'Unavailable' }) }) });
  const page = context.eventsPage({ id: 'event' }, []);
  await page.fetchIfNeeded('peserta');
  assert.ok(page.error.peserta);
  assert.equal(page.loading.peserta, false);
  assert.equal(page.loadedTabs.peserta, undefined);
});

test('network retry reuses the same invoice idempotency key', async () => {
  const keys = [];
  const { form } = checkout({ fetch: async (_, options) => {
    keys.push(options.headers['Idempotency-Key']);
    throw new Error('Response lost');
  } });
  await form.createInvoice();
  await form.createInvoice();
  assert.ok(keys[0]);
  assert.equal(keys[0], keys[1]);
});

test('refresh restores the pending invoice without creating another order', async () => {
  const memory = new Map();
  const sessionStorage = { getItem: key => memory.get(key), setItem: (key, value) => memory.set(key, value), removeItem: key => memory.delete(key) };
  let creates = 0;
  const payment = { id: 'saved-payment', team_id: 'team', event_id: 'event', qty: 3, status: 'PENDING', amount: 3000 };
  const fetch = async (_, options = {}) => {
    if (options.method === 'POST') creates++;
    return { ok: true, json: async () => ({ success: true, data: payment }) };
  };
  const first = checkout({ fetch, sessionStorage });
  await first.form.createInvoice();
  const refreshed = checkout({ fetch, sessionStorage });
  await refreshed.form.init();
  assert.equal(refreshed.form.payment.id, payment.id);
  assert.equal(refreshed.form.qty, 3);
  assert.equal(creates, 1);
});

test('automatic status polling stays local while manual check requests reconciliation', async () => {
  const calls = [];
  const { form } = checkout({ fetch: async (url, options = {}) => {
    calls.push([url, options.method || 'GET']);
    return { ok: true, json: async () => ({ success: true, data: { id: 'payment', status: 'PENDING' } }) };
  } });
  form.payment = { id: 'payment', status: 'PENDING' };
  await form.checkPayment();
  await form.checkPayment(true);
  assert.deepEqual(calls, [['/payments/payment/status', 'GET'], ['/payments/payment/reconcile', 'POST']]);
});
