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
  form.voter = { name: 'Preview supporter', phone: '81234567890', email: 'sandbox-test@example.com' };
  return { form };
}

test('checkout rejects fractional, zero and excessive vote quantities', () => {
  for (const qty of [0, -1, 1.5, 10001]) {
    const { form } = checkout();
    form.qty = qty;
    form.nextStep();
    assert.ok(form.error);
    assert.equal(form.payment, null);
  }
});

test('closed voting cannot enter payment confirmation', () => {
  const { form } = checkout();
  form.event.closes_at = '2000-01-01T00:00:00Z';
  form.nextStep();
  assert.match(form.error, /Voting/);
  assert.equal(form.payment, null);
});

test('checkout requires a valid payer email before confirmation', () => {
  for (const email of ['', 'invalid', 'a b@example.com', 'a'.repeat(40) + '@example.com']) {
    const { form } = checkout();
    form.voter.email = email;
    form.nextStep();
    assert.match(form.error, /email/);
    assert.equal(form.payment, null);
  }
});

test('invoice rejection leaves the confirmation available for retry', async () => {
  const { form } = checkout({ fetch: async () => ({ ok: false, json: async () => ({ success: false, message: 'Service unavailable' }) }) });
  await form.createInvoice();
  assert.equal(form.payment, null);
  assert.equal(form.error, 'Service unavailable');
  assert.equal(form.isLoading, false);
});

test('pending invoice with payment URL is stored for direct navigation', async () => {
  const { form } = checkout({ fetch: async () => ({ ok: true, json: async () => ({ success: true, data: { id: 'preview-payment', status: 'PENDING', payment_url: 'https://sandbox.duitku.com/checkout/test', votes: 1, amount: 1000 } }) }) });
  await form.createInvoice();
  assert.equal(form.payment.id, 'preview-payment');
  assert.equal(form.payment.status, 'PENDING');
  assert.equal(form.payment.payment_url, 'https://sandbox.duitku.com/checkout/test');
  assert.equal(form.error, '');
  assert.equal(form.isLoading, false);
});

test('pending invoice without payment URL shows error and does not navigate', async () => {
  const { form } = checkout({ fetch: async () => ({ ok: true, json: async () => ({ success: true, data: { id: 'preview-payment', status: 'PENDING', payment_url: null, votes: 1, amount: 1000 } }) }) });
  await form.createInvoice();
  assert.equal(form.payment.id, 'preview-payment');
  assert.match(form.error, /Tautan pembayaran belum tersedia/);
  assert.equal(form.isNavigating, false);
});

test('goToPayment navigates to payment URL', () => {
  let navigatedTo = null;
  const { form } = checkout({});
  form.payment = { id: 'preview-payment', status: 'PENDING', payment_url: 'https://sandbox.duitku.com/checkout/test' };
  form.goToPayment = function() { if (!this.isNavigating && this.payment?.payment_url) { this.isNavigating = true; navigatedTo = this.payment.payment_url; } };
  form.goToPayment();
  assert.equal(navigatedTo, 'https://sandbox.duitku.com/checkout/test');
  assert.equal(form.isNavigating, true);
});

test('goToPayment does nothing without payment URL', () => {
  let navigatedTo = null;
  const { form } = checkout({});
  form.payment = { id: 'preview-payment', status: 'PENDING', payment_url: null };
  form.goToPayment = function() { if (!this.isNavigating && this.payment?.payment_url) { this.isNavigating = true; navigatedTo = this.payment.payment_url; } };
  form.goToPayment();
  assert.equal(navigatedTo, null);
  assert.equal(form.isNavigating, false);
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

test('event API failures show a retryable error instead of a false empty list', async () => {
  const context = load('events.js', { fetch: async () => ({ ok: false, json: async () => ({ success: false, message: 'Unavailable' }) }) });
  const page = context.eventsPage({ id: 'event' }, []);
  await page.fetchIfNeeded('peserta');
  assert.ok(page.error.peserta);
  assert.equal(page.loading.peserta, false);
  assert.equal(page.loadedTabs.peserta, undefined);
});
