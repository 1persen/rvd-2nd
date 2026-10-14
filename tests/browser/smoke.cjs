const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const baseURL = 'http://127.0.0.1:8765';
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal((await page.goto(baseURL)).status(), 200);
      await page.locator('.landing-slide[data-slide="hero"]').waitFor({ state: 'visible' });
      await page.locator('.landing-section-inner.section-visible').first().waitFor({ state: 'visible' });
      await page.locator('.hero-actions a[href="/events"]').click();
      await page.waitForURL(`${baseURL}/events`);
      assert.equal((await page.goto(`${baseURL}/admin/login`)).status(), 200);
      await page.getByLabel('Alamat email', { exact: true }).waitFor({ state: 'visible' });
    }
    assert.deepEqual(errors, []);
    console.log('PASS landing animations, event navigation and admin login at 390/1440 px without JavaScript errors.');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
