// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { chromium } from '@playwright/test';
import fs from 'fs';

const env = Object.fromEntries(
  fs.readFileSync('/tmp/lotsman-admin.env', 'utf8')
    .trim().split('\n').map(l => l.split('=', 2))
);
const TOTP = process.env.TOTP_NOW;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') console.log('[err]', m.text().slice(0,150)); });
  page.on('pageerror', e => console.log('[exc]', e.message.slice(0,150)));

  await page.goto('http://localhost:5173/login', { waitUntil: 'networkidle' });
  await page.fill('input[type="email"]', env.ADMIN_EMAIL);
  await page.fill('input[type="password"]', env.ADMIN_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  const totpInput = page.locator('input[inputmode="numeric"], input[autocomplete="one-time-code"]').first();
  await totpInput.waitFor({ timeout: 5000 });
  await totpInput.fill(TOTP);
  await page.click('button[type="submit"]');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  console.log('logged in, url=', page.url());

  // Click Import button
  console.log('clicking Импорт');
  await page.click('[data-testid="import-xlsx-button"]');
  await page.waitForTimeout(500);
  await page.screenshot({ path: '/tmp/lotsman-import-1.png', fullPage: true });

  // Upload file
  console.log('uploading file');
  const fileInput = page.locator('[data-testid="import-file-input"]');
  await fileInput.setInputFiles('/tmp/lotsman-import-sample.xlsx');
  await page.waitForTimeout(300);
  await page.click('[data-testid="import-submit"]');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: '/tmp/lotsman-import-2.png', fullPage: true });

  const text = await page.textContent('body');
  console.log('has "Импорт завершён":', text.includes('Импорт завершён'));
  console.log('has "Документов создано":', text.includes('Документов создано'));

  // Close dialog and check the table
  const closeBtn = page.locator('button', { hasText: 'Закрыть' }).first();
  if (await closeBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await closeBtn.click();
  }
  await page.waitForTimeout(2500);
  await page.screenshot({ path: '/tmp/lotsman-import-3.png', fullPage: true });
  const tableText = await page.textContent('body');
  console.log('table has "Agronexus":', tableText.includes('Agronexus'));
  console.log('table has "AN Holding":', tableText.includes('AN Holding'));
  console.log('table has "Business Registration":', tableText.includes('Business Registration'));

  await browser.close();
})();
