'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('./playwright/node_modules/@playwright/test');

(async () => {
  const browser = await chromium.launch({ channel: 'chromium', headless: true });
  try {
    const page = await browser.newPage();
    await page.setContent('<button onclick="this.textContent=\'Saved\'">Save</button>');
    await page.getByRole('button', { name: 'Save' }).click();
    assert.equal(await page.getByRole('button').innerText(), 'Saved');
    console.log('Relocated Chromium OK:', browser.version());
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
