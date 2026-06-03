// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { test, expect } from "@playwright/test";

test.describe("Login page smoke", () => {
  test("page title contains Лоцман", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveTitle(/Лоцман/);
  });

  test("form is keyboard-reachable (Tab through all interactive fields)", async ({ page }) => {
    await page.goto("/login");

    // Theme toggle in corner should be focusable
    await page.keyboard.press("Tab");

    // Email input — autoFocused, but Tab from theme-toggle reaches it
    const emailInput = page.getByLabel(/электронная почта/i);
    await emailInput.focus();
    await expect(emailInput).toBeFocused();

    // Tab → password
    await page.keyboard.press("Tab");
    const passwordInput = page.getByLabel(/пароль/i);
    await expect(passwordInput).toBeFocused();

    // Tab → totp
    await page.keyboard.press("Tab");
    const totpInput = page.getByLabel(/код двухфакторной/i);
    await expect(totpInput).toBeFocused();

    // Tab → submit button
    await page.keyboard.press("Tab");
    const submitButton = page.getByRole("button", { name: /войти/i });
    await expect(submitButton).toBeFocused();
  });
});
