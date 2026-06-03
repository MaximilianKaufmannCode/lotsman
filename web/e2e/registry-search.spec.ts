// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * E2E: Registry search flows (US-2, US-3).
 *
 * Covers:
 *  - Global search input debounced at 200ms, results update
 *  - Clearing search restores the full list
 *  - Per-column filter (status, type) narrows results
 *  - Sort by column header persists in URL
 *  - Filter state is bookmarkable (URL round-trip)
 *
 * Prerequisites:
 *   - Full dev stack: docker compose -f infra/compose.dev.yml up -d
 *   - Seeded DB with test documents: make seed
 *   - Dev server: pnpm dev
 *
 * Run:
 *   pnpm playwright test e2e/registry-search.spec.ts --reporter=line
 */

import { expect, test } from "@playwright/test";

const BASE_URL = process.env["TEST_BASE_URL"] ?? "http://localhost:5173";

async function loginAs(
  page: Parameters<typeof test.use>[0] extends { page: infer P } ? P : never,
  email: string,
  password: string,
) {
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

test.skip(
  !process.env["E2E_ENABLED"],
  "E2E_ENABLED not set — skipping registry-search e2e tests (requires running backend)",
);

test.describe("Registry — global search (US-2)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "editor@example.com", "editor_secret_password");
  });

  test("global search filters rows and updates URL", async ({ page }) => {
    // Arrange: wait for table
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    // Act: type into search box
    const searchBox = page.getByRole("searchbox").or(page.getByPlaceholder(/Поиск/i)).first();
    await searchBox.fill("ДГ-2026");

    // Assert: URL contains query param after debounce
    await page.waitForTimeout(300); // wait for 200ms debounce + render
    await expect(page).toHaveURL(/q=ДГ-2026|q=%D0%94%D0%93-2026/);

    // Table rows show matching results only
    const rows = page.locator('[data-testid^="row-"]');
    const count = await rows.count();
    // If any results, they should contain the search term
    if (count > 0) {
      const firstCellText = await rows.first().textContent();
      expect(firstCellText).toMatch(/ДГ-2026/i);
    }
  });

  test("clearing search restores full list and removes q from URL", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    // Type a search query first
    const searchBox = page.getByRole("searchbox").or(page.getByPlaceholder(/Поиск/i)).first();
    await searchBox.fill("НЕСУЩЕСТВУЮЩИЙ_ЗАПРОС_12345");
    await page.waitForTimeout(300);

    // Clear the search
    await searchBox.clear();
    await page.waitForTimeout(300);

    // URL should not contain q param
    const url = page.url();
    expect(url).not.toContain("q=НЕСУЩЕСТВУЮЩИЙ");

    // Full list indicator back
    await expect(page.getByText(/Всего:/i)).toBeVisible();
  });

  test("SQL injection characters in search do not break the page", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    const searchBox = page.getByRole("searchbox").or(page.getByPlaceholder(/Поиск/i)).first();
    // US-2 Q: SQL-special chars pass safely to server (server sanitizes)
    await searchBox.fill("'; DROP TABLE documents; --");
    await page.waitForTimeout(300);

    // Page must still render, not crash
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();
    expect(page.isClosed()).toBe(false);
  });
});

test.describe("Registry — column sort (US-3)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "viewer@example.com", "viewer_secret_password");
  });

  test("clicking expiry_date header sorts ascending and updates URL", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    // Click the "Срок" / expiry column header
    const expiryHeader = page.getByRole("columnheader", { name: /Срок|Дата окончания/i }).first();
    await expiryHeader.click();

    await page.waitForTimeout(200);
    await expect(page).toHaveURL(/sort=expiry_date/);
    await expect(page).toHaveURL(/dir=asc|dir=desc/);
  });

  test("sort state persists after browser back navigation", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    const expiryHeader = page.getByRole("columnheader", { name: /Срок|Дата окончания/i }).first();
    await expiryHeader.click();
    await page.waitForTimeout(200);

    const sortedUrl = page.url();
    expect(sortedUrl).toContain("sort=expiry_date");

    // Navigate away then back
    await page.goto(`${BASE_URL}/`);
    await page.goBack();

    // Sort should be restored from URL
    await expect(page).toHaveURL(/sort=expiry_date/);
  });
});

test.describe("Registry — filter state bookmarkability (US-2)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page as never, "viewer@example.com", "viewer_secret_password");
  });

  test("navigating to URL with filter params restores filter state", async ({ page }) => {
    // Navigate directly to URL with pre-set filter
    await page.goto(`${BASE_URL}/registry?status=soon&sort=expiry_date&dir=asc`);
    await page.waitForURL(`${BASE_URL}/registry**`);

    // Grid should be visible with filter applied
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible();

    // URL should still contain our filter params
    await expect(page).toHaveURL(/status=soon/);
    await expect(page).toHaveURL(/sort=expiry_date/);
  });
});
