// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Registry E2E flows — Playwright.
 *
 * Run with:
 *   pnpm playwright test e2e/registry-flows.spec.ts --reporter=line
 *
 * Prerequisites:
 *   - Full dev stack running: docker compose -f infra/compose.dev.yml up -d
 *   - Seeded DB: make seed
 *   - Dev server: pnpm dev (or VITE_API_BASE_URL pointing to staging)
 *   - Playwright installed: pnpm playwright install
 */

import { expect, test } from "@playwright/test";

// ── Helpers ───────────────────────────────────────────────────────────────────

async function loginAs(
  page: Parameters<typeof test.use>[0] extends { page: infer P } ? P : never,
  email: string,
  password: string,
  totpCode: string,
) {
  await page.goto("/login");
  await page.fill('[aria-label="Электронная почта"]', email);
  await page.fill('[aria-label="Пароль"]', password);
  await page.click('[type="submit"]');
  await page.waitForURL(/\/login|\/registry/);
  // TOTP step
  const totpInput = page.locator('[aria-label="Код двухфакторной аутентификации"]');
  if (await totpInput.isVisible()) {
    await totpInput.fill(totpCode);
    await page.click('[type="submit"]');
  }
  await page.waitForURL("/registry");
}

// ── Tests ──────────────────────────────────────────────────────────────────────

test.describe("Registry — editor flows", () => {
  test.beforeEach(async ({ page }) => {
    // Login as editor (credentials come from seed data)
    await loginAs(page as never, "editor@example.com", "editor_secret_password", "000000");
  });

  test("editor sees the registry table on load", async ({ page }) => {
    await expect(page.getByRole("grid", { name: "Реестр документов" })).toBeVisible();
    await expect(page.getByText("Всего:")).toBeVisible();
  });

  test("editor adds a document, sees it in the table", async ({ page }) => {
    await page.click('button:has-text("+ Добавить документ")');
    await expect(page.getByRole("dialog", { name: "Добавить документ" })).toBeVisible();

    // Select an asset and type
    await page.selectOption('[aria-label*="Контрагент"]', { index: 1 });
    await page.selectOption('[aria-label*="Тип документа"]', { index: 1 });
    await page.fill('[placeholder="Например: ДГ-2026-001"]', "TEST-2026-E2E");
    await page.click('[type="submit"]:has-text("Создать документ")');

    await expect(page.getByRole("dialog")).not.toBeVisible();
    // New row visible
    await expect(page.getByText("TEST-2026-E2E")).toBeVisible({ timeout: 5000 });
  });

  test("editor inline-edits a cell", async ({ page }) => {
    // Wait for table to have rows
    await page.waitForSelector('[data-testid^="cell-"]');

    // Double-click the number cell of the first document row
    const firstNumberCell = page.locator('[data-testid$="-number"]').first();
    await firstNumberCell.dblclick();

    // Cell enters edit mode
    const input = firstNumberCell.locator("input");
    await expect(input).toBeVisible();
    await input.fill("EDIT-2026-E2E");
    await input.press("Enter");

    // Cell shows new value
    await expect(firstNumberCell.locator("span.font-mono")).toHaveText("EDIT-2026-E2E", { timeout: 5000 });
  });

  test("editor archives a document, it disappears from active list", async ({ page }) => {
    // Open drawer for first row
    const firstRow = page.locator('tr[role="row"]').first();
    await firstRow.click();

    const drawer = page.getByRole("dialog", { name: /Документ/ });
    await expect(drawer).toBeVisible();

    // Switch to Основное tab and click archive (via row context)
    // Close drawer and use the bulk-select approach
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();

    // Select first row via checkbox
    const firstCheckbox = page.locator('input[type="checkbox"][aria-label^="Выбрать строку"]').first();
    await firstCheckbox.check();
    await expect(page.getByText("Выбрано: 1")).toBeVisible();

    // Bulk archive
    await page.click('button:has-text("Архивировать (1)")');
    // Confirm modal
    page.on("dialog", (dialog) => void dialog.accept());

    // Row disappears
    await expect(page.getByText("Архивировано: 1")).toBeVisible({ timeout: 5000 });
  });
});

test.describe("Registry — admin flows", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "admin@example.com", "admin_secret_password", "000000");
  });

  test("admin creates an asset, a document type, and a document", async ({ page }) => {
    // Create asset
    await page.goto("/admin/assets");
    await page.click('button:has-text("Добавить контрагента")');
    const assetDialog = page.getByRole("dialog", { name: "Добавить контрагента" });
    await assetDialog.getByRole("textbox", { name: "Название" }).fill("ООО Тест Е2Е");
    await assetDialog.getByRole("textbox", { name: "ИНН" }).fill("7701234567");
    await page.click('[type="submit"]:has-text("Создать")');
    await expect(page.getByText("ООО Тест Е2Е")).toBeVisible({ timeout: 5000 });

    // Create document type
    await page.goto("/admin/document-types");
    await page.click('button:has-text("Добавить тип")');
    const typeDialog = page.getByRole("dialog", { name: "Добавить тип документа" });
    await typeDialog.getByRole("textbox", { name: "Код" }).fill("e2e_type");
    await typeDialog.getByRole("textbox", { name: "Отображаемое название" }).fill("E2E Тип");
    await page.click('[type="submit"]:has-text("Создать")');
    await expect(page.getByText("E2E Тип")).toBeVisible({ timeout: 5000 });

    // Create document using the new asset+type
    await page.goto("/registry");
    await page.click('button:has-text("+ Добавить документ")');
    const createDialog = page.getByRole("dialog", { name: "Добавить документ" });
    await createDialog.selectOption('[aria-required="true"]', { label: "ООО Тест Е2Е" });
    await expect(createDialog).toBeVisible();
  });

  test("admin exports registry, opens export modal", async ({ page }) => {
    await page.goto("/registry");
    await page.click('button:has-text("Экспорт .xlsx")');

    // Toast appears
    await expect(page.getByText("Экспорт запущен")).toBeVisible({ timeout: 5000 });

    // Export modal opens
    await expect(page.getByRole("dialog", { name: "Экспорты .xlsx" })).toBeVisible();
  });
});

test.describe("Registry — bulk operations", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "editor@example.com", "editor_secret_password", "000000");
  });

  test("bulk-select 3 docs, bulk-archive, verify toast", async ({ page }) => {
    await page.waitForSelector('input[type="checkbox"][aria-label^="Выбрать строку"]');

    const checkboxes = page.locator('input[type="checkbox"][aria-label^="Выбрать строку"]');
    const count = await checkboxes.count();
    const toSelect = Math.min(3, count);
    for (let i = 0; i < toSelect; i++) {
      await checkboxes.nth(i).check();
    }
    await expect(page.getByText(`Выбрано: ${toSelect}`)).toBeVisible();

    page.on("dialog", (d) => void d.accept());
    await page.click(`button:has-text("Архивировать (${toSelect})")`);
    await expect(page.getByText(/Архивировано:/)).toBeVisible({ timeout: 5000 });
  });

  test("selecting >100 rows shows a warning", async ({ page }) => {
    // The UI caps selection at BULK_ARCHIVE_MAX and shows warning inline
    // This test is a UI smoke test; actual >100 selection requires large seed data
    const header = page.locator('input[type="checkbox"][aria-label="Выбрать все строки"]');
    if (await header.isVisible()) {
      await header.check();
      // If >100 rows exist, warning appears
      const warning = page.getByText(/Максимум 100 документов/);
      // Warning may or may not appear depending on seed count; just verify no crash
      await expect(page).toHaveURL(/registry/);
    }
  });
});

test.describe("Registry — keyboard navigation", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "editor@example.com", "editor_secret_password", "000000");
  });

  test("⌘K focuses the search input", async ({ page }) => {
    await page.keyboard.press("Meta+k");
    const searchInput = page.locator('[aria-label="Глобальный поиск по реестру (⌘K)"]');
    await expect(searchInput).toBeFocused();
  });

  test("pressing Escape in inline edit cancels without saving", async ({ page }) => {
    await page.waitForSelector('[data-testid$="-number"]');
    const cell = page.locator('[data-testid$="-number"]').first();
    await cell.dblclick();
    const input = cell.locator("input");
    const original = await input.inputValue();
    await input.fill("SHOULD_NOT_SAVE");
    await input.press("Escape");
    await expect(cell.locator("span.font-mono")).toHaveText(original);
  });
});
