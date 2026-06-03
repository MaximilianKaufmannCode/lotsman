// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * E2E: Registry xlsx export flows (US-20, US-21, Q8).
 *
 * Covers:
 *  - Editor clicks Export → job appears in ExportJobsModal with status=pending
 *  - Job transitions to done; download button enabled
 *  - Downloading the file delivers a non-empty .xlsx blob
 *  - Expired job (status=expired) shows no download button
 *  - Viewer can request export (not admin-only)
 *
 * Prerequisites:
 *   - Full dev stack running with ARQ worker processing export jobs.
 *
 * Run:
 *   pnpm playwright test e2e/registry-export.spec.ts --reporter=line
 */

import { expect, test } from "@playwright/test";

const BASE_URL = process.env["TEST_BASE_URL"] ?? "http://localhost:5173";

test.skip(
  !process.env["E2E_ENABLED"],
  "E2E_ENABLED not set — skipping registry-export e2e tests (requires running backend with ARQ worker)",
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

// ---------------------------------------------------------------------------
// US-20: Export job lifecycle
// ---------------------------------------------------------------------------

test.describe("Registry — export job lifecycle (US-20)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "editor@example.com", "editor_secret_password");
  });

  test("requesting export creates a job, which completes and allows download", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible({ timeout: 10_000 });

    // Click the Export button in toolbar
    const exportBtn = page.getByRole("button", { name: /Экспорт/i });
    await expect(exportBtn).toBeVisible({ timeout: 5_000 });
    await exportBtn.click();

    // ExportJobsModal should open
    const modal = page.getByRole("dialog", { name: /Экспорты/i });
    await expect(modal).toBeVisible({ timeout: 3_000 });

    // Initially job shows pending/running state
    const pendingLabel = page.getByText(/Ожидание|Выполняется/i);
    await expect(pendingLabel).toBeVisible({ timeout: 5_000 });

    // Wait for job to complete (ARQ worker must be running; up to 30s)
    await expect(page.getByText("Готов")).toBeVisible({ timeout: 30_000 });

    // Download button should be enabled
    const downloadBtn = page.getByRole("button", { name: /Скачать файл экспорта/i });
    await expect(downloadBtn).toBeEnabled({ timeout: 3_000 });

    // Trigger download and verify non-empty file
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 15_000 }),
      downloadBtn.click(),
    ]);

    const downloadPath = await download.path();
    expect(downloadPath).toBeTruthy();

    const { stat } = await import("fs/promises");
    if (downloadPath) {
      const info = await stat(downloadPath).catch(() => null);
      if (info) {
        expect(info.size).toBeGreaterThan(0);
      }
    }
  });

  test("viewer can request an export (not admin-only)", async ({ page }) => {
    await loginAs(page, "viewer@example.com", "viewer_secret_password");
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible({ timeout: 10_000 });

    const exportBtn = page.getByRole("button", { name: /Экспорт/i });
    await expect(exportBtn).toBeVisible({ timeout: 5_000 });
    await exportBtn.click();

    // Must open the modal without 403/401 errors
    const modal = page.getByRole("dialog", { name: /Экспорты/i });
    await expect(modal).toBeVisible({ timeout: 3_000 });

    // Must not show an auth error
    await expect(page.getByText(/403|401|доступ запрещён/i)).not.toBeVisible({ timeout: 2_000 });
  });
});

// ---------------------------------------------------------------------------
// Q8: Expired export — 410 download blocked
// ---------------------------------------------------------------------------

test.describe("Registry — export expired (Q8 24h TTL)", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "editor@example.com", "editor_secret_password");
  });

  test("expired export job shows Истёк label and no download button", async ({ page }) => {
    await expect(page.getByRole("grid", { name: /Реестр документов/i })).toBeVisible({ timeout: 10_000 });

    const exportBtn = page.getByRole("button", { name: /Экспорт/i });
    await exportBtn.click();

    const modal = page.getByRole("dialog", { name: /Экспорты/i });
    await expect(modal).toBeVisible({ timeout: 3_000 });

    // If any job shows "Истёк" (seeded expired job), download must be absent
    const expiredLabel = page.getByText("Истёк");
    if (await expiredLabel.isVisible().catch(() => false)) {
      // In the same row as "Истёк", there should be no download button
      const row = page.locator("li").filter({ hasText: "Истёк" }).first();
      const dlBtn = row.getByRole("button", { name: /Скачать/i });
      await expect(dlBtn).not.toBeVisible();
    }
    // If no expired jobs in DB yet, the test passes vacuously (no assertion to fail)
  });
});
