// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * E2E: Wrong TOTP shows a generic error (no enumeration of account state).
 *
 * Verifies ADR-0003 §12: the 401 response body is always {"detail": "Invalid credentials"}
 * regardless of the underlying cause — wrong password, wrong TOTP, locked account.
 *
 * Prerequisite: backend running with `docker compose -f infra/compose.dev.yml up -d`
 * Run: cd web && pnpm playwright test e2e/wrong-totp-shows-generic-error.spec.ts
 */

import { test, expect } from "@playwright/test";

const BASE_URL = process.env["TEST_BASE_URL"] ?? "http://localhost:5173";
const ADMIN_EMAIL = process.env["E2E_ADMIN_EMAIL"] ?? "admin@example.com";
const ADMIN_PASSWORD = process.env["E2E_ADMIN_PASSWORD"] ?? "AdminSecurePass123";
const ADMIN_TOTP_SECRET = process.env["E2E_ADMIN_TOTP_SECRET"] ?? "";

test.describe("@smoke Wrong TOTP shows generic error", () => {
  test.skip(
    !ADMIN_TOTP_SECRET,
    "E2E_ADMIN_TOTP_SECRET not set — skipping (requires running backend)",
  );

  test("wrong TOTP code shows generic error without revealing TOTP invalidity", async ({
    page,
  }) => {
    // Arrange: submit correct password
    await page.goto(`${BASE_URL}/login`);
    await page.getByLabel(/электронная почта/i).fill(ADMIN_EMAIL);
    await page.getByLabel(/пароль/i).fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: /войти/i }).click();

    // Wait for TOTP step
    await page.waitForSelector('[data-testid="totp-input"], [aria-label*="двухфакторн"]');

    // Act: submit obviously wrong TOTP
    const totpInput = page.getByTestId("totp-input").or(
      page.getByLabel(/двухфакторн/i)
    );
    await totpInput.fill("000000");
    await page.getByRole("button", { name: /подтвердить/i }).click();

    // Assert: generic error visible
    await expect(
      page.getByText(/неверные учётные данные/i)
    ).toBeVisible({ timeout: 5000 });

    // Assert: NO hint about TOTP specifically (enumeration prevention)
    await expect(page.getByText(/неверный totp/i)).not.toBeVisible();
    await expect(page.getByText(/invalid totp/i)).not.toBeVisible();
    await expect(page.getByText(/wrong code/i)).not.toBeVisible();

    // Assert: still on login page (not redirected)
    expect(page.url()).toContain("/login");
  });

  test("wrong password shows the same generic error as wrong TOTP", async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.getByLabel(/электронная почта/i).fill(ADMIN_EMAIL);
    await page.getByLabel(/пароль/i).fill("wrong-password-that-is-long-enough");
    await page.getByRole("button", { name: /войти/i }).click();

    await expect(
      page.getByText(/неверные учётные данные/i)
    ).toBeVisible({ timeout: 5000 });
  });
});
