// Open archived reports with networking disabled. Requires playwright-core and Chromium.
const { chromium } = require(process.env.ARCHE_PLAYWRIGHT || 'playwright-core');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

async function check(browser, file, checkScreens) {
  const page = await browser.newPage({ offline: true });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(pathToFileURL(path.resolve(file)).href);
    await page.locator('#files details[data-file]').first().waitFor({ state: 'attached' });
    if (checkScreens) {
      await page.locator('nav a[href="#screens"]').click();
      const screen = page.locator('#screens details').first();
      if (!(await screen.getAttribute('open') !== null)) await screen.locator('summary').click();
      await page.locator('#screens details[open] a[href^="#file-"]').first().click();
      await page.locator('#files details:target').waitFor({ state: 'visible' });
    }
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('OK', file);
  } finally {
    await page.close();
  }
}

async function checkMap(browser, file) {
  const page = await browser.newPage({ offline: true });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  try {
    await page.goto(pathToFileURL(path.resolve(file)).href);
    const card = page.locator('.card[data-type="part"]').first();
    await card.click();
    await page.locator('#panel h2').waitFor();
    await card.click();
    await page.locator('#crumbs button').nth(1).waitFor();
    await page.locator('#tabs button').nth(2).click();
    await page.locator('.tree a[href^="report.html#file-"]').first().waitFor({ state: 'attached' });
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('OK', file);
  } finally {
    await page.close();
  }
}

async function main() {
  const [runs, ...additional] = process.argv.slice(2);
  if (!runs) throw new Error('Usage: node evals/check_reports.cjs RUNS [REPORT.html ...]');
  const browser = await chromium.launch({ channel: process.env.ARCHE_BROWSER_CHANNEL || undefined });
  try {
    await check(browser, path.join(runs, 'cli', 'report.html'), false);
    await check(browser, path.join(runs, 'ui', 'report.html'), true);
    for (const name of ['cli', 'ui']) await checkMap(browser, path.join(runs, name, 'map.html'));
    for (const file of additional) await check(browser, file, false);
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
