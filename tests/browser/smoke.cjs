const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const zlib = require('node:zlib');

(async () => {
  const baseURL = 'http://127.0.0.1:8765';
  const fixture = JSON.parse(execFileSync('python', ['tests/browser/seed.py'], { encoding: 'utf8' }));
  const browser = await chromium.launch({ headless: true });
  fs.mkdirSync('tests/browser/artifacts', { recursive: true });
  let page;
  try {
    const context = await browser.newContext({ colorScheme: 'dark' });
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    async function layout(label) {
      const dimensions = await page.evaluate(() => ({
        width: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      assert.ok(dimensions.scroll <= dimensions.width + 1, `${label}: horizontal overflow ${JSON.stringify(dimensions)}`);
    }
    async function capture(label, width) {
      if ([390, 1440].includes(width)) {
        const name = `${label}-${width}`;
        const jpeg = await page.screenshot({ path: `tests/browser/artifacts/${name}.jpg`, type: 'jpeg', quality: 65, animations: 'disabled', fullPage: false });
        // Keep bounded visual proof in CI logs; no workflow permission or extra package is required.
        if (process.env.CI) console.log(`RVD_PROOF_JPEG:${name}:` + jpeg.toString('base64'));
      }
    }
    for (const width of [320, 390, 820, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal((await page.goto(baseURL)).status(), 200);
      await page.locator('.landing-slide[data-slide="hero"]').waitFor({ state: 'visible' });
      await page.locator('.landing-section-inner.section-visible').first().waitFor({ state: 'visible' });
      assert.equal(await page.locator('html').evaluate(el => el.classList.contains('dark')), false);
      await layout(`landing ${width}`);
      const header = await page.locator('.public-header').boundingBox();
      const title = await page.locator('.home-hero-copy h1').boundingBox();
      assert.ok(header.height >= 54 && header.height <= 100, `header height ${header.height}`);
      assert.ok(title.y >= header.y + header.height - 100, 'hero title is unexpectedly obscured by header');
      assert.ok(title.height < 300, `hero copy became a display-sized paragraph at ${width}px: ${JSON.stringify(title)}`);
      const logo = await page.locator('.public-header .wordmark').boundingBox();
      const theme = await page.locator('.theme-toggle').boundingBox();
      assert.ok(logo.x + logo.width <= theme.x, 'brand overlaps theme control');
      if (width > 700) {
        assert.equal(await page.locator('.desktop-nav').isVisible(), true, `desktop nav visible at ${width}px`);
        assert.equal(await page.locator('#mobile-nav').isVisible(), false, `mobile nav hidden at ${width}px`);
      } else {
        assert.equal(await page.locator('.desktop-nav').isVisible(), false, `desktop nav hidden at ${width}px`);
        await page.getByRole('button', { name: 'Buka navigasi', exact: true }).click();
        await page.locator('#mobile-nav').waitFor({ state: 'visible' });
        const mobileNav = await page.locator('#mobile-nav').boundingBox();
        assert.ok(mobileNav.width >= width - 80, `mobile nav width at ${width}px: ${JSON.stringify(mobileNav)}`);
        assert.equal(await page.locator('#mobile-nav').getByRole('link', { name: 'Bantuan' }).isVisible(), true);
      }
      await capture('landing', width);
      if (width <= 700) {
        await page.locator('#mobile-nav').getByRole('link', { name: 'Bantuan' }).click();
      } else {
        await page.locator('.desktop-nav').getByRole('link', { name: 'Bantuan' }).click();
      }
      await page.waitForURL(`${baseURL}/support`);
      await layout(`support ${width}`);
      await capture('support', width);
      await page.goto(`${baseURL}/events?event_id=${fixture.event_id}`);
      await page.getByText('Cakrawala', { exact: true }).first().waitFor({ state: 'visible' });
      await layout(`event ${width}`);
      await capture('event', width);
      await page.getByRole('button', { name: 'Leaderboard', exact: true }).click();
      await page.locator('.podium-person[data-place="3"]').waitFor({ state: 'visible' });
      assert.equal(await page.locator('.podium-person').count(), 3);
      const first = await page.locator('.podium-person[data-place="1"]').boundingBox();
      const second = await page.locator('.podium-person[data-place="2"]').boundingBox();
      const third = await page.locator('.podium-person[data-place="3"]').boundingBox();
      assert.ok(second.x < first.x && first.x < third.x, 'winner must be centered');
      assert.ok(first.y < second.y, 'winner must be elevated');
      await layout(`podium ${width}`);
      await capture('leaderboard', width);
      await page.goto(`${baseURL}/events/${fixture.event_id}/vote/${fixture.team_id}`);
      await page.getByLabel('Nama lengkap').waitFor({ state: 'visible' });
      await layout(`checkout ${width}`);
      await capture('checkout', width);
      await page.getByRole('button', { name: 'Aktifkan tema gelap' }).click();
      assert.equal(await page.locator('html').evaluate(el => el.classList.contains('dark')), true);
      const ticket = await page.locator('.rv-ticket').evaluate(el => getComputedStyle(el).backgroundImage);
      assert.ok(!ticket.includes('rgb(252, 247, 229)'), 'dark ticket keeps readable contrast');
      await page.getByRole('button', { name: 'Aktifkan tema terang' }).click();
      await page.goto(`${baseURL}/admin/login`);
      await page.getByLabel('Alamat email', { exact: true }).waitFor({ state: 'visible' });
      await layout(`admin login ${width}`);
      await capture('admin-login', width);
    }
    // Direct payment URL navigation; no intermediate dialog or status polling.
    // Tested on mobile and desktop viewports.
    const pending = { id: 'pay_browser_navigation', status: 'PENDING', qty: 1,
      amount: 1000, is_mock: false, is_sandbox: true,
      payment_url: 'https://sandbox.duitku.com/checkout/test' };
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      let creations = 0;
      let navigatedTo = null;
      await page.route('**/api/v1/payments', async route => {
        assert.ok(route.request().headers()['idempotency-key']);
        creations++;
        await route.fulfill({ json: { success: true, data: pending } });
      });
      await page.route('**/sandbox.duitku.com/**', async route => {
        navigatedTo = route.request().url();
        await route.fulfill({ status: 200, body: 'ok' });
      });
      await page.goto(`${baseURL}/events/${fixture.event_id}/vote/${fixture.team_id}`);
      await page.getByLabel('Nama lengkap').fill(`Browser Navigation ${width}`);
      await page.getByLabel('Nomor WhatsApp').fill('81000000002');
      await page.getByLabel('Email', { exact: true }).fill(`navigation${width}@example.com`);
      await page.getByRole('button', { name: 'Periksa dukungan', exact: true }).click();
      // createInvoice() is called directly after form submit; no "Buat pembayaran" button.
      // "Lanjutkan pembayaran" appears when PENDING with payment_url.
      await page.getByRole('button', { name: 'Lanjutkan pembayaran', exact: true }).waitFor({ state: 'visible' });
      // No intermediate dialog, no "Buka pembayaran Duitku", no "Periksa status".
      assert.equal(await page.getByRole('dialog').count(), 0, `${width}: no dialog`);
      assert.equal(await page.getByRole('link', { name: 'Buka pembayaran Duitku' }).count(), 0, `${width}: no Duitku link`);
      assert.equal(await page.getByRole('button', { name: 'Periksa status', exact: true }).count(), 0, `${width}: no status button`);
      assert.equal(await page.getByText('Tautan pembayaran belum tersedia.', { exact: false }).filter({ visible: true }).count(), 0, `${width}: no missing URL error`);
      assert.equal(creations, 1, `${width}: single payment creation`);
      // Click "Lanjutkan pembayaran" → navigates to backend payment_url.
      await page.getByRole('button', { name: 'Lanjutkan pembayaran', exact: true }).click();
      await page.waitForTimeout(1000);
      assert.equal(navigatedTo, 'https://sandbox.duitku.com/checkout/test', `${width}: navigated to backend payment_url`);
      await page.unroute('**/api/v1/payments');
      await page.unroute('**/sandbox.duitku.com/**');
      await capture(`checkout-navigation-${width}`, width);
    }

    // Exercise the real admin login and protected APIs using an ephemeral account.
    await page.goto(`${baseURL}/admin/login`);
    await page.getByLabel('Alamat email', { exact: true }).fill(fixture.email);
    await page.getByLabel('Kata sandi', { exact: true }).fill(fixture.password);
    await page.getByRole('button', { name: 'Masuk ke Workspace' }).click();
    await page.waitForURL(`${baseURL}/admin`);
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      for (const path of ['/admin', '/admin/events', '/admin/leaderboard', '/admin/transactions', '/admin/settings', '/admin/audit-logs', '/admin/progress']) {
        assert.equal((await page.goto(baseURL + path)).status(), 200);
        await page.locator('.admin-page-heading h1').waitFor({ state: 'visible' });
        if (path !== '/admin/progress') {
          const section = path === '/admin' ? 'dashboard' : path.split('/').pop();
          await page.locator(`#${section}-content`).waitFor({ state: 'visible' });
          assert.equal(await page.locator('#error-state').isVisible(), false, `${path} failed to load its API data`);
        }
        await layout(`${path} ${width}`);
        await capture(path === '/admin' ? 'admin-dashboard' : path.slice(1).replace('/', '-'), width);
      }
    }
    assert.deepEqual(errors, []);
    console.log('PASS ivory default, responsive header, navigation, centered premium podium, checkout, direct Duitku payment URL navigation, theme, admin login and seven admin pages; widths 320/390/820/1440; no JavaScript errors.');
  } catch (error) {
    if (page) {
      console.error('Layout diagnostics:', await page.locator('.home-hero-copy h1').evaluateAll(elements => elements.map(el => ({ text: el.textContent, width: el.clientWidth, font: getComputedStyle(el).font, lineHeight: getComputedStyle(el).lineHeight }))));
      // A compressed failure image is recoverable from CI logs without runtime credentials.
      const png = await page.screenshot();
      console.error('RVD_FAILURE_PNG_GZIP:' + zlib.gzipSync(png).toString('base64'));
    }
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
