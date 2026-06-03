// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * MaintenanceConfirmDialog tests.
 *
 * Covers:
 * - Submit disabled until both TOTP (6 digits) AND confirmation === expected
 * - Partial TOTP → disabled
 * - Wrong confirmation text → disabled
 * - Correct both → enabled
 * - REMFA_REPLAY error clears TOTP
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type * as React from "react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SystemApiResponseError } from "@/features/system/api";
import i18n from "@/i18n/index";
import { MaintenanceConfirmDialog } from "../components/MaintenanceConfirmDialog";

// ── Router mock (for Dialog) ──────────────────────────────────────────────────

vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...original,
    Link: ({
      to,
      children,
      ...props
    }: {
      to: string;
      children: React.ReactNode;
      [k: string]: unknown;
    }) => (
      <a href={to} {...props}>
        {children}
      </a>
    ),
    useLocation: () => ({ pathname: "/" }),
  };
});

function renderDialog(onConfirm = vi.fn()) {
  return render(
    <I18nextProvider i18n={i18n}>
      <MaintenanceConfirmDialog
        open
        onClose={vi.fn()}
        title="Test operation"
        expected="BACKUP NOW"
        onConfirm={onConfirm}
      />
    </I18nextProvider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MaintenanceConfirmDialog — submit gating", () => {
  it("submit button is disabled initially", () => {
    renderDialog();
    const submitBtn = screen.getByTestId("maint-submit");
    expect(submitBtn).toBeDisabled();
  });

  it("submit remains disabled with partial TOTP (3 digits) and correct confirmation", async () => {
    const user = userEvent.setup();
    renderDialog();

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123");
    await user.type(confirmInput, "BACKUP NOW");

    expect(screen.getByTestId("maint-submit")).toBeDisabled();
  });

  it("submit remains disabled with full TOTP but wrong confirmation", async () => {
    const user = userEvent.setup();
    renderDialog();

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123456");
    await user.type(confirmInput, "BACKUP");

    expect(screen.getByTestId("maint-submit")).toBeDisabled();
  });

  it("submit becomes enabled with full TOTP AND correct confirmation", async () => {
    const user = userEvent.setup();
    renderDialog();

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123456");
    await user.type(confirmInput, "BACKUP NOW");

    const submitBtn = screen.getByTestId("maint-submit");
    expect(submitBtn).not.toBeDisabled();
  });

  it("calls onConfirm with correct args when submitted", async () => {
    const mockConfirm = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderDialog(mockConfirm);

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123456");
    await user.type(confirmInput, "BACKUP NOW");
    await user.click(screen.getByTestId("maint-submit"));

    expect(mockConfirm).toHaveBeenCalledWith("123456", "BACKUP NOW");
  });
});

describe("MaintenanceConfirmDialog — error handling", () => {
  it("clears TOTP and shows error on REMFA_REPLAY", async () => {
    const mockConfirm = vi
      .fn()
      .mockRejectedValue(new SystemApiResponseError(401, "Replay detected", "REMFA_REPLAY"));
    const user = userEvent.setup();
    renderDialog(mockConfirm);

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123456");
    await user.type(confirmInput, "BACKUP NOW");
    await user.click(screen.getByTestId("maint-submit"));

    // TOTP should be cleared
    expect(totpInput).toHaveValue("");
    // Error message should appear (totp-error paragraph has role=alert)
    expect(screen.getByText(/уже был использован|replay/i)).toBeInTheDocument();
  });

  it("submit is disabled again after TOTP cleared by REMFA_REPLAY", async () => {
    const mockConfirm = vi
      .fn()
      .mockRejectedValue(new SystemApiResponseError(401, "Replay", "REMFA_REPLAY"));
    const user = userEvent.setup();
    renderDialog(mockConfirm);

    const totpInput = screen.getByLabelText(/двухфакторной|totp/i);
    const confirmInput = screen.getByLabelText(/подтверждение|confirmation/i);

    await user.type(totpInput, "123456");
    await user.type(confirmInput, "BACKUP NOW");
    await user.click(screen.getByTestId("maint-submit"));

    // After replay error, TOTP cleared → button disabled
    expect(screen.getByTestId("maint-submit")).toBeDisabled();
  });
});
