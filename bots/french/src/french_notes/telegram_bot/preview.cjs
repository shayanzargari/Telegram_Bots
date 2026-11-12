// Local screenshot of the offline screen report, never a live phone claim.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const work = process.argv[2]||process.env.BOT_CENTER_TEST_OUTPUT;
  if(!work)throw new Error('Provide an explicit isolated audit work directory.');
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1220, height: 1000}});
    await page.goto(pathToFileURL(path.join(work, 'screen-preview.html')).href);
    await page.screenshot({path: path.join(work, 'screen-preview.png'), fullPage: true});
    const overflow = await page.locator('.text').evaluateAll(elements => elements.some(e => e.scrollWidth > e.clientWidth + 1));
    if (overflow) throw new Error('Offline preview text overflows its reader card.');
    console.log(JSON.stringify({screens: await page.locator('section').count(), textOverflow: overflow, screenshot: path.join(work, 'screen-preview.png')}));
  } finally {
    await browser.close();
  }
})();
