const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

// DOM minimal: modal menyentuh document saat dibuka/ditutup dan saat efek gift.
const stubDocument = {
  body: { style: {} },
  activeElement: null,
  querySelector: () => null,
  getElementById: () => null,
  addEventListener() {},
};

function load(file, globals = {}) {
  const context = vm.createContext({
    Intl, Date, crypto: require('node:crypto').webcrypto, console: { error() {} },
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    EventSource: undefined, matchMedia: () => ({ matches: false }),
    document: stubDocument,
    // window.location.href dipakai untuk navigasi ke halaman pembayaran.
    window: { addEventListener() {}, location: { href: '', search: '' } },
    ...globals,
  });
  vm.runInContext(readFileSync(join(__dirname, '../app/static/js', file), 'utf8'), context);
  return context;
}

// Checkout voting sekarang ada di modal (vote_modal.js), pengganti vote.js.
function checkout(globals = {}) {
  const context = load('vote_modal.js', globals);
  const form = context.voteModal();
  // $nextTick dan $watch disediakan Alpine saat komponen dipasang ke DOM.
  form.$nextTick = (fn) => fn();
  form.$watch = () => {};
  form.show({ team: { id: 'team', name: 'Tim Uji' }, event: { id: 'event', name: 'Event Uji', is_voting_open: true, price_per_vote: 1000 } });
  Object.assign(form.form, { name: 'Preview supporter', phone: '81234567890', email: 'sandbox-test@example.com', method: 'NQ' });
  return { form, context };
}

// Alur yang dites adalah alur yang dipakai tombol submit.
function submit(form) {
  form.submit();
}

// Panggilan fetch dicatat agar jumlah berat dan isi body bisa diperiksa.
function recordingFetch(responder) {
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({ url, options });
    return responder(url, options);
  };
  // Hanya panggilan pembuatan invoice, bukan daftar metode/status/feed.
  const invoices = () => calls.filter(c => String(c.url).endsWith('/api/v1/payments'));
  return { fetch, calls, invoices };
}

test('checkout rejects fractional, zero and excessive vote quantities', () => {
  for (const qty of [0, -1, 1.5, 10001]) {
    const { form } = checkout();
    form.form.qty = qty;
    form.submit();
    assert.ok(form.error);
    assert.equal(form.payment, null);
  }
});

test('closed voting cannot enter payment confirmation', () => {
  const { form } = checkout();
  form.form.qty = 1;
  form.event.closes_at = '2000-01-01T00:00:00Z';
  form.submit();
  assert.match(form.error, /Voting/);
  assert.equal(form.payment, null);
});

test('checkout requires a valid payer email before confirmation', () => {
  for (const email of ['', 'invalid', 'a b@example.com', 'a'.repeat(40) + '@example.com']) {
    const { form } = checkout();
    form.form.qty = 1;
    form.form.email = email;
    form.submit();
    assert.match(form.error, /email/);
    assert.equal(form.payment, null);
  }
});

test('invoice rejection leaves the confirmation available for retry', async () => {
  const { fetch } = recordingFetch(async () => ({ ok: false, json: async () => ({ success: false, message: 'Service unavailable' }) }));
  const { form } = checkout({ fetch });
  form.form.qty = 1;
  await form.createInvoice();
  assert.equal(form.payment, null);
  assert.equal(form.error, 'Service unavailable');
  assert.equal(form.isLoading, false);
});

test('pending invoice with payment URL is stored for direct navigation', async () => {
  const { fetch } = recordingFetch(async () => ({ ok: true, json: async () => ({ success: true, data: { id: 'preview-payment', status: 'PENDING', payment_url: 'https://sandbox.duitku.com/checkout/test', votes: 1, amount: 1000 } }) }));
  const { form } = checkout({ fetch });
  form.form.qty = 1;
  await form.createInvoice();
  assert.equal(form.payment.id, 'preview-payment');
  assert.equal(form.payment.status, 'PENDING');
  assert.equal(form.payment.payment_url, 'https://sandbox.duitku.com/checkout/test');
  assert.equal(form.error, '');
  assert.equal(form.isLoading, false);
});

test('pending invoice without payment URL shows error and does not navigate', async () => {
  const { fetch } = recordingFetch(async () => ({ ok: true, json: async () => ({ success: true, data: { id: 'preview-payment', status: 'PENDING', payment_url: null, votes: 1, amount: 1000 } }) }));
  const { form } = checkout({ fetch });
  form.form.qty = 1;
  await form.createInvoice();
  assert.equal(form.payment.id, 'preview-payment');
  assert.match(form.error, /Tautan pembayaran belum tersedia/);
  assert.equal(form.isNavigating, false);
});

test('goToPayment navigates once to the payment URL', () => {
  const { form, context } = checkout();
  context.window.location.href = '';
  form.payment = { id: 'preview-payment', status: 'PENDING', payment_url: 'https://sandbox.duitku.com/checkout/test' };
  form.goToPayment();
  assert.equal(context.window.location.href, 'https://sandbox.duitku.com/checkout/test');
  assert.equal(form.isNavigating, true);
});

test('goToPayment does nothing without payment URL', () => {
  const { form, context } = checkout();
  context.window.location.href = '';
  form.payment = { id: 'preview-payment', status: 'PENDING', payment_url: null };
  form.goToPayment();
  assert.equal(context.window.location.href, '');
  assert.equal(form.isNavigating, false);
});

test('network retry reuses the same invoice idempotency key', async () => {
  const { fetch, invoices } = recordingFetch(async () => { throw new Error('Response lost'); });
  const { form } = checkout({ fetch });
  form.form.qty = 1;
  await form.createInvoice();
  await form.createInvoice();
  const keys = invoices().map(c => c.options.headers['Idempotency-Key']);
  assert.ok(keys[0]);
  assert.equal(keys[0], keys[1]);
});

test('invoice sends the chosen method, gift-free qty and public fields only', async () => {
  const { fetch, invoices } = recordingFetch(async () => ({ ok: true, json: async () => ({ success: true, data: { id: 'p', status: 'PENDING', payment_url: 'https://sandbox.duitku.com/x' } }) }));
  const { form } = checkout({ fetch });
  form.form.qty = 3;
  form.form.method = 'DA';
  await form.createInvoice();
  const body = JSON.parse(invoices()[0].options.body);
  assert.equal(body.payment_method, 'DA');
  assert.equal(body.qty, 3);
  assert.equal(body.effect_code, null);
  assert.equal(body.supporter_email, 'sandbox-test@example.com');
});

test('selecting a gift sets its package qty and dropping the qty releases the gift', () => {
  const { form } = checkout();
  form.event.vote_effects = [{ code: 'mawar', label: 'Mawar', level: 'Basic', votes: 50, duration_ms: 2000, animation: 'rose' }];
  form.toggleEffect(form.allEffects[0]);
  assert.equal(form.form.qty, 50);
  assert.equal(form.form.effect, 'mawar');
  form.setQty(3);
  form.enforceEffectRule();
  assert.equal(form.form.effect, '');
});

test('event API failures show a retryable error instead of a false empty list', async () => {
  const context = load('events.js', { fetch: async () => ({ ok: false, json: async () => ({ success: false, message: 'Unavailable' }) }) });
  const page = context.eventsPage({ id: 'event' }, []);
  await page.fetchIfNeeded('peserta');
  assert.ok(page.error.peserta);
  assert.equal(page.loading.peserta, false);
  assert.equal(page.loadedTabs.peserta, undefined);
});
