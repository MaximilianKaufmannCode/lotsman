// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * LoginPage tests.
 *
 * The page now uses AuthProvider — we stub it to avoid real network calls.
 * Covers: render, step 1 validation, step 2 TOTP transition, accessibility.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import i18n from "@/i18n/index";
import { Toaster } from "@/shared/ui/toast";
import { LoginPage } from "./LoginPage";

// ── Stubs ─────────────────────────────────────────────────────────────────────

// Mock TanStack Router hooks used by LoginPage
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-router")>();
  return {
    ...original,
    Navigate: ({ to }: { to: string }) => <div data-testid="navigate" data-to={to} />,
    useSearch: () => ({}),
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: "/login" }),
  };
});

// Mock BroadcastChannel
class MockBC {
  onmessage = null;
  postMessage = vi.fn();
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  close = vi.fn();
}
// @ts-expect-error mock
globalThis.BroadcastChannel = MockBC;

// Mock the auth api
vi.mock("@/features/auth/api", () => ({
  login: vi.fn(),
  verifyTotp: vi.fn(),
  verifyBackupCode: vi.fn(),
  refreshToken: vi.fn().mockRejectedValue(new Error("no cookie")),
  logout: vi.fn(),
  registerTokenAccessor: vi.fn(),
  ApiResponseError: class ApiResponseError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
      this.name = "ApiResponseError";
    }
  },
}));

import { AuthProvider } from "@/features/auth/AuthProvider";
import * as authApi from "@/features/auth/api";

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderLoginPage() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>
        <I18nextProvider i18n={i18n}>
          <LoginPage />
          <Toaster />
        </I18nextProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.refreshToken).mockRejectedValue(new Error("no cookie"));
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("LoginPage", () => {
  it("renders without throwing", () => {
    expect(() => renderLoginPage()).not.toThrow();
  });

  it("shows step 1 with email and password inputs", async () => {
    renderLoginPage();
    // Wait for auth status to settle
    await waitFor(() => {
      expect(screen.getByLabelText(/электронная почта/i)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/пароль/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /войти/i })).toBeInTheDocument();
  });

  it("submit button is disabled when fields are empty", async () => {
    renderLoginPage();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /войти/i })).toBeDisabled();
    });
  });

  it("shows validation error for invalid email", async () => {
    const user = userEvent.setup();
    renderLoginPage();
    await waitFor(() => screen.getByLabelText(/электронная почта/i));
    const emailInput = screen.getByLabelText(/электронная почта/i);
    await user.type(emailInput, "not-an-email");
    await user.tab();
    await waitFor(() => {
      expect(screen.getByText(/некорректный адрес/i)).toBeInTheDocument();
    });
  });

  it("shows validation error for password too short (< 12 chars)", async () => {
    const user = userEvent.setup();
    renderLoginPage();
    await waitFor(() => screen.getByLabelText(/пароль/i));
    const passwordInput = screen.getByLabelText(/пароль/i);
    await user.type(passwordInput, "short");
    await user.tab();
    await waitFor(() => {
      expect(screen.getByText(/не менее 12/i)).toBeInTheDocument();
    });
  });

  it("transitions to TOTP step on successful step-1 submission", async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      next_step: "verify_totp",
      totp_session_token: "tok123",
    });

    const user = userEvent.setup();
    renderLoginPage();
    await waitFor(() => screen.getByLabelText(/электронная почта/i));

    await user.type(screen.getByLabelText(/электронная почта/i), "user@corp.ru");
    await user.type(screen.getByLabelText(/пароль/i), "SecurePassword1");
    await user.click(screen.getByRole("button", { name: /войти/i }));

    await waitFor(() => {
      expect(screen.getByLabelText(/код двухфакторной/i)).toBeInTheDocument();
    });
    // "use backup code" button should appear
    expect(screen.getByRole("button", { name: /резервный код/i })).toBeInTheDocument();
  });

  it("shows generic error on 401 — no enumeration", async () => {
    vi.mocked(authApi.login).mockRejectedValue(
      new authApi.ApiResponseError(401, "Invalid credentials"),
    );

    const user = userEvent.setup();
    renderLoginPage();
    await waitFor(() => screen.getByLabelText(/электронная почта/i));

    await user.type(screen.getByLabelText(/электронная почта/i), "user@corp.ru");
    await user.type(screen.getByLabelText(/пароль/i), "SecurePassword1");
    await user.click(screen.getByRole("button", { name: /войти/i }));

    await waitFor(() => {
      expect(screen.getByText(/неверные учётные данные/i)).toBeInTheDocument();
    });
  });

  it("is accessible — zero axe violations on step 1", async () => {
    const { container } = renderLoginPage();
    await waitFor(() => screen.getByRole("button", { name: /войти/i }));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  }, 15_000);
});

describe("LoginPage TOTP step", () => {
  it("shows backup code link in step 2", async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      next_step: "verify_totp",
      totp_session_token: "tok",
    });

    const user = userEvent.setup();
    renderLoginPage();
    await waitFor(() => screen.getByLabelText(/электронная почта/i));

    await user.type(screen.getByLabelText(/электронная почта/i), "user@corp.ru");
    await user.type(screen.getByLabelText(/пароль/i), "SecurePassword1");
    await user.click(screen.getByRole("button", { name: /войти/i }));

    await waitFor(() => screen.getByLabelText(/код двухфакторной/i));

    // Click "use backup code" — use role query to avoid ambiguity with form label
    await user.click(screen.getByRole("button", { name: /резервный код/i }));

    await waitFor(() => {
      // The backup code input is labeled "Резервный код"
      expect(screen.getByRole("textbox", { name: /резервный код/i })).toBeInTheDocument();
    });
  });

  it("is accessible on TOTP step", async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      next_step: "verify_totp",
      totp_session_token: "tok",
    });

    const user = userEvent.setup();
    const { container } = renderLoginPage();
    await waitFor(() => screen.getByLabelText(/электронная почта/i));

    await user.type(screen.getByLabelText(/электронная почта/i), "user@corp.ru");
    await user.type(screen.getByLabelText(/пароль/i), "SecurePassword1");
    await user.click(screen.getByRole("button", { name: /войти/i }));

    await waitFor(() => screen.getByLabelText(/код двухфакторной/i));

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  }, 15_000);
});
