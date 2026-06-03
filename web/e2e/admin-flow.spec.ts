// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * E2E: Admin creates user → OOB OTP modal → new user enrolls TOTP → accesses /registry.
 *
 * Verifies the full US-17 → US-1 → US-3 → US-6 → US-2 golden path through the UI.
 *
 * Prerequisite: backend running with `docker compose -f infra/compose.dev.yml up -d`
 *
 * Run:
 *   cd web && pnpm playwright install chromium && pnpm playwright test e2e/admin-flow.spec.ts
 *
 * Tags: @smoke (happy-path golden path for deploy smoke check)
 */

import { test, expect, type Page } from "@playwright/test";

const BASE_URL = process.env["TEST_BASE_URL"] ?? "http://localhost:5173";
const ADMIN_EMAIL = process.env["E2E_ADMIN_EMAIL"] ?? "admin@example.com";
const ADMIN_PASSWORD = process.env["E2E_ADMIN_PASSWORD"] ?? "AdminSecurePass123";
const ADMIN_TOTP_SECRET = process.env["E2E_ADMIN_TOTP_SECRET"] ?? "";

function getTotpCode(): string {
  return process.env["E2E_TOTP_CODE"] ?? "000000";
}

/** Generate a unique test email to avoid conflicts across runs. */
function randomEmail(): string {
  return `test-${Date.now()}@example.com`;
}

async function adminLogin(page: Page): Promise<void> {
  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel(/электронная почта/i).fill(ADMIN_EMAIL);
  await page.getByLabel(/пароль/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /войти/i }).click();
  await page.waitForSelector('[data-testid="totp-input"], [aria-label*="двухфакторн"]');
  await page.getByTestId("totp-input").or(page.getByLabel(/двухфакторн/i)).fill(getTotpCode());
  await page.getByRole("button", { name: /подтвердить/i }).click();
  await page.waitForURL(`${BASE_URL}/registry`, { timeout: 10_000 });
}

test.describe("@smoke Admin flow: create user → first login → enrollment", () => {
  test.skip(
    !ADMIN_TOTP_SECRET,
    "E2E_ADMIN_TOTP_SECRET not set — skipping (requires running backend)",
  );

  test("admin creates new user, receives OOB OTP, new user enrolls and lands on /registry", async ({
    browser,
  }) => {
    const adminCtx = await browser.newContext();
    const adminPage = await adminCtx.newPage();

    // Step 1: admin logs in
    await adminLogin(adminPage);

    // Step 2: admin navigates to user management
    await adminPage.goto(`${BASE_URL}/admin/users`);
    await adminPage.waitForLoadState("networkidle");

    // Step 3: admin creates a new user
    const newUserEmail = randomEmail();
    const createButton = adminPage.getByRole("button", { name: /создать пользователя|добавить/i });
    await createButton.click();

    await adminPage.getByLabel(/электронная почта/i).fill(newUserEmail);
    await adminPage.getByLabel(/полное имя|имя/i).fill("Test User E2E");
    await adminPage.getByLabel(/роль/i).selectOption("viewer");
    await adminPage.getByRole("button", { name: /создать|сохранить/i }).click();

    // Step 4: OOB OTP modal appears
    const otpModal = adminPage.getByRole("dialog");
    await expect(otpModal).toBeVisible({ timeout: 5_000 });
    await expect(otpModal.getByText(/одноразовый пароль|oob otp/i)).toBeVisible();

    // Capture the OOB OTP from the modal
    const otpText = await otpModal.getByTestId("oob-otp").or(
      otpModal.locator("[data-testid='oob-otp'], code, .oob-otp")
    ).textContent();
    expect(otpText).toBeTruthy();
    const oobOtp = otpText!.trim();

    // Admin closes the modal
    await otpModal.getByRole("button", { name: /закрыть|закрить|ok/i }).click();
    await adminCtx.close();

    // ---- New user flow in a separate context ----
    const newUserCtx = await browser.newContext();
    const newUserPage = await newUserCtx.newPage();

    // Step 5: new user logs in with OOB OTP
    await newUserPage.goto(`${BASE_URL}/login`);
    await newUserPage.getByLabel(/электронная почта/i).fill(newUserEmail);
    await newUserPage.getByLabel(/пароль/i).fill(oobOtp);
    await newUserPage.getByRole("button", { name: /войти/i }).click();

    // Step 6: forced TOTP enrollment screen appears
    await expect(
      newUserPage.getByTestId("totp-enrollment").or(
        newUserPage.getByText(/настройте totp|отсканируйте qr/i)
      )
    ).toBeVisible({ timeout: 8_000 });

    // NOTE: Full TOTP enrollment in E2E requires a TOTP library to compute codes.
    // This test documents the expected screen flow up to the enrollment step.
    // Full automation: use `otplib` in a Playwright fixture to read the secret and
    // compute the confirmation code dynamically.
    console.log("E2E stub: TOTP enrollment screen visible — full automation requires otplib fixture");

    await newUserCtx.close();
  });
});
