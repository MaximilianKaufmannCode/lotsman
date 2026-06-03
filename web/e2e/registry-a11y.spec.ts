// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * E2E: Registry accessibility audit (US-26, WCAG 2.2 AA).
 *
 * Uses axe-core via @axe-core/playwright on all registry-facing pages:
 *  - /registry (main table, empty state, loaded state)
 *  - /registry with DocumentDetailDrawer open
 *  - DocumentCreateDialog open
 *  - ExportJobsModal open
 *
 * Violations in the "color-contrast" rule are excluded from the assertion
 * because Tailwind's built-in palette may have minor contrast gaps in theming
 * that are tracked separately by the UX designer.
 *
 * Prerequisites:
 *   - Full dev stack + seeded DB.
 *   - @axe-core/playwright installed: pnpm add -D @axe-core/playwright
 *
 * Run:
 *   pnpm playwright test e2e/registry-a11y.spec.ts --reporter=line
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const BASE_URL = process.env["TEST_BASE_URL"] ?? "http://localhost:5173";

// Skip the entire file if the backend is not running
test.skip(
  !process.env["E2E_ENABLED"],
  "E2E_ENABLED not set — skipping registry-a11y e2e tests (requires running backend)",
);

async function loginAs(page: any, email: string, password: string) {
  await page.goto(`${BASE_URL}/login`);
  await page.fill('[aria-label="Электронная почта"]', email);
  await page.fill('[aria-label="Пароль"]', password);
  await page.click('[type="submit"]');
  const totpInput = page.locator('[aria-label="Код двухфакторной аутентификации"]');
  if (await totpInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await totpInput.fill("000000");
    await page.click('[type="submit"]');
  }
  await page.waitForURL(`${BASE_URL}/registry`, { timeout: 10_000 });
}

const EXCLUDED_RULES = ["color-contrast"];

// ---------------------------------------------------------------------------
// /registry — main table
// ---------------------------------------------------------------------------

test.describe("Accessibility — /registry main table", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "viewer@example.com", "viewer_secret_password");
  });

  test("axe finds zero violations on the registry table page", async ({ page }) => {
    await page.waitForSelector('[role="grid"]', { timeout: 10_000 });

    const results = await new AxeBuilder({ page })
      .disableRules(EXCLUDED_RULES)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("axe finds zero violations on the registry empty state", async ({ page }) => {
    // Navigate with a filter that returns zero results
    await page.goto(`${BASE_URL}/registry?q=НЕСУЩЕСТВУЮЩИЙ_ЗАПРОС_AXE_SCAN_12345`);
    await page.waitForLoadState("networkidle");

    const results = await new AxeBuilder({ page })
      .disableRules(EXCLUDED_RULES)
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// DocumentDetailDrawer open
// ---------------------------------------------------------------------------

test.describe("Accessibility — DocumentDetailDrawer", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "viewer@example.com", "viewer_secret_password");
  });

  test("axe finds zero violations with the detail drawer open", async ({ page }) => {
    await page.waitForSelector('[data-testid^="row-"]', { timeout: 10_000 });

    // Open the first document's detail drawer
    await page.locator('[data-testid^="row-"]').first().click();
    await expect(page.getByRole("dialog")).toBeVisible({ timeout: 5_000 });

    const results = await new AxeBuilder({ page })
      .disableRules(EXCLUDED_RULES)
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// DocumentCreateDialog open
// ---------------------------------------------------------------------------

test.describe("Accessibility — DocumentCreateDialog", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "editor@example.com", "editor_secret_password");
  });

  test("axe finds zero violations with the create dialog open", async ({ page }) => {
    await page.waitForSelector('[role="grid"]', { timeout: 10_000 });

    // Open create dialog
    const addBtn = page.getByRole("button", { name: /Добавить документ/i });
    if (!(await addBtn.isVisible({ timeout: 3_000 }).catch(() => false))) {
      test.skip(); // seeded DB may have no editor accessible via UI
      return;
    }
    await addBtn.click();
    await expect(page.getByRole("dialog", { name: /Добавить документ/i })).toBeVisible({ timeout: 3_000 });

    const results = await new AxeBuilder({ page })
      .disableRules(EXCLUDED_RULES)
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// ExportJobsModal open
// ---------------------------------------------------------------------------

test.describe("Accessibility — ExportJobsModal", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "editor@example.com", "editor_secret_password");
  });

  test("axe finds zero violations with the export modal open", async ({ page }) => {
    await page.waitForSelector('[role="grid"]', { timeout: 10_000 });

    const exportBtn = page.getByRole("button", { name: /Экспорт/i });
    await expect(exportBtn).toBeVisible({ timeout: 5_000 });
    await exportBtn.click();

    await expect(page.getByRole("dialog", { name: /Экспорты/i })).toBeVisible({ timeout: 3_000 });

    const results = await new AxeBuilder({ page })
      .disableRules(EXCLUDED_RULES)
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Focus trap — detail drawer must trap focus while open
// ---------------------------------------------------------------------------

test.describe("Accessibility — focus trap in dialogs", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "viewer@example.com", "viewer_secret_password");
  });

  test("focus stays inside DocumentDetailDrawer while open", async ({ page }) => {
    await page.waitForSelector('[data-testid^="row-"]', { timeout: 10_000 });
    await page.locator('[data-testid^="row-"]').first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 5_000 });

    // Tab 15 times — focus should remain inside the dialog element
    for (let i = 0; i < 15; i++) {
      await page.keyboard.press("Tab");
      const activeInDialog = await dialog.evaluate((el) => el.contains(document.activeElement));
      expect(activeInDialog).toBe(true);
    }
  });
});
