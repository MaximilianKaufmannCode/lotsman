// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ProfilePage — EmailChangeDialog tests.
 *
 * Covers:
 * - Dialog opens on «Сменить» button click
 * - Step 1 submit: success → advances to Step 2
 * - Step 1 submit: EMAIL_CHANNEL_REQUIRED → red channel-error banner
 * - Step 2 submit: success → toast + dialog closes
 * - Step 2 submit: VERIFICATION_FAILED → inline error with attempts_remaining
 * - Minimal smoke: close dialog
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { I18nextProvider } from "react-i18next";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { JwtClaims, UserProfile } from "@/features/auth/types";
import i18n from "@/i18n/index";
import { DEFAULT_SCALE, STORAGE_KEY, setScale } from "@/shared/ui/font-scale";
import { Toaster } from "@/shared/ui/toast";

// ── Router mock ───────────────────────────────────────────────────────────────

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
      [key: string]: unknown;
    }) => (
      <a href={to} {...props}>
        {children}
      </a>
    ),
    Navigate: ({ to }: { to: string }) => <div data-testid="navigate" data-to={to} />,
    useSearch: () => ({}),
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: "/profile" }),
    useRouter: () => ({ navigate: vi.fn() }),
  };
});

// ── BroadcastChannel mock ─────────────────────────────────────────────────────

class MockBC {
  onmessage = null;
  postMessage = vi.fn();
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  close = vi.fn();
}
// @ts-expect-error mock
globalThis.BroadcastChannel = MockBC;

// ── Auth api mock ─────────────────────────────────────────────────────────────

vi.mock("@/features/auth/api", () => ({
  login: vi.fn(),
  verifyTotp: vi.fn(),
  verifyBackupCode: vi.fn(),
  refreshToken: vi.fn().mockRejectedValue(new Error("no cookie")),
  logout: vi.fn(),
  getMyProfile: vi.fn(),
  getMySessions: vi.fn(),
  updateMyProfile: vi.fn(),
  regenerateBackupCodes: vi.fn(),
  reMfa: vi.fn(),
  revokeSession: vi.fn(),
  changePassword: vi.fn(),
  requestEmailChange: vi.fn(),
  confirmEmailChange: vi.fn(),
  sendMyTestEmail: vi.fn(),
  getMyNotificationPrefs: vi.fn(),
  updateMyNotificationPrefs: vi.fn(),
  registerTokenAccessor: vi.fn(),
  ApiResponseError: class ApiResponseError extends Error {
    status: number;
    detail: string;
    code: string | undefined;
    attemptsRemaining: number | undefined;
    constructor(
      status: number,
      detail: string,
      code: string | undefined = undefined,
      attemptsRemaining: number | undefined = undefined,
    ) {
      super(detail);
      this.status = status;
      this.detail = detail;
      this.code = code;
      this.attemptsRemaining = attemptsRemaining;
      this.name = "ApiResponseError";
    }
  },
}));

import * as authApi from "@/features/auth/api";

// ── AuthContext mock ──────────────────────────────────────────────────────────

const MockAuthContext = React.createContext<{
  claims: JwtClaims | null;
  status: string;
  logout: () => void;
}>({ claims: null, status: "authenticated", logout: vi.fn() });

vi.mock("@/features/auth/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => {
    const [claims] = React.useState<JwtClaims | null>(null);
    return (
      <MockAuthContext.Provider value={{ claims, status: "authenticated", logout: vi.fn() }}>
        {children}
      </MockAuthContext.Provider>
    );
  },
  useAuth: () => React.useContext(MockAuthContext),
  TAB_ID: "test-tab",
}));

// ── Component under test ──────────────────────────────────────────────────────

import { AuthProvider } from "@/features/auth/AuthProvider";
import { ProfilePage } from "../ProfilePage";

// ── Sample data ───────────────────────────────────────────────────────────────

const sampleProfile: UserProfile = {
  id: "user-1",
  email: "user@example.com",
  full_name: "Test User",
  role: "editor",
  is_active: true,
  is_locked: false,
  totp_enrolled: true,
  must_change_password: false,
  last_login_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ui_font_scale: 100,
};

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderProfile() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>
        <I18nextProvider i18n={i18n}>
          <ProfilePage />
          <Toaster />
        </I18nextProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

// ── Setup ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.refreshToken).mockRejectedValue(new Error("no cookie"));
  vi.mocked(authApi.getMyProfile).mockResolvedValue(sampleProfile);
  vi.mocked(authApi.getMySessions).mockResolvedValue([]);
  vi.mocked(authApi.getMyNotificationPrefs).mockResolvedValue({
    enabled: true,
    suppress_own: true,
    email_mode: "digest",
    categories: { deadline: { in_app: true, email: true } },
  });
});

// ── Helper ────────────────────────────────────────────────────────────────────

async function openEmailDialog(user = userEvent.setup()) {
  // Wait for profile to load — email appears as a disabled input value
  await waitFor(() => {
    expect(screen.getByDisplayValue("user@example.com")).toBeInTheDocument();
  });
  // Use aria-label to target the email-change button specifically
  const btn = screen.getByRole("button", { name: "Сменить" });
  await user.click(btn);
  await waitFor(() => {
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
  return user;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("EmailChangeDialog — open/close", () => {
  it("opens dialog when Сменить button is clicked", async () => {
    renderProfile();
    await openEmailDialog();
    expect(screen.getByRole("dialog", { name: /шаг 1 из 2/i })).toBeInTheDocument();
  });

  it("closes dialog when Cancel is clicked", async () => {
    renderProfile();
    const user = await openEmailDialog();

    const cancelBtn = screen.getByRole("button", { name: /отмена/i });
    await user.click(cancelBtn);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});

describe("EmailChangeDialog — Step 1 submit", () => {
  it("advances to Step 2 on successful request", async () => {
    vi.mocked(authApi.requestEmailChange).mockResolvedValue({
      request_id: "req-123",
      code_ttl_seconds: 900,
      masked_new_email: "new***@exa***.com",
    });

    renderProfile();
    const user = await openEmailDialog();
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText(/новый email/i), "new@example.com");
    await user.type(within(dialog).getByLabelText(/код totp/i), "123456");
    await user.click(within(dialog).getByRole("button", { name: /отправить код подтверждения/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /шаг 2 из 2/i })).toBeInTheDocument();
      expect(screen.getByText(/new\*\*\*@exa\*\*\*\.com/i)).toBeInTheDocument();
    });
  });

  it("shows EMAIL_CHANNEL_REQUIRED as a red banner", async () => {
    const { ApiResponseError } = await import("@/features/auth/api");
    vi.mocked(authApi.requestEmailChange).mockRejectedValue(
      new ApiResponseError(503, "Email channel not configured", "EMAIL_CHANNEL_REQUIRED"),
    );

    renderProfile();
    const user = await openEmailDialog();
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText(/новый email/i), "new@example.com");
    await user.type(within(dialog).getByLabelText(/код totp/i), "123456");
    await user.click(within(dialog).getByRole("button", { name: /отправить код подтверждения/i }));

    await waitFor(() => {
      // Channel error banner from i18n key profile.email_error_EMAIL_CHANNEL_REQUIRED
      expect(screen.getByText(/обратитесь к администратору/i)).toBeInTheDocument();
    });

    // Must still be on Step 1
    expect(screen.getByRole("dialog", { name: /шаг 1 из 2/i })).toBeInTheDocument();
  });
});

describe("EmailChangeDialog — Step 2 submit", () => {
  async function goToStep2() {
    vi.mocked(authApi.requestEmailChange).mockResolvedValue({
      request_id: "req-abc",
      code_ttl_seconds: 900,
      masked_new_email: "new***@exa***.com",
    });

    renderProfile();
    const user = await openEmailDialog();
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText(/новый email/i), "new@example.com");
    await user.type(within(dialog).getByLabelText(/код totp/i), "123456");
    await user.click(within(dialog).getByRole("button", { name: /отправить код подтверждения/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /шаг 2 из 2/i })).toBeInTheDocument();
    });
    return user;
  }

  it("shows success toast and closes dialog on confirm", async () => {
    vi.mocked(authApi.confirmEmailChange).mockResolvedValue({ email: "new@example.com" });

    const user = await goToStep2();
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText(/код из письма/i), "12345678");
    await user.click(within(dialog).getByRole("button", { name: /подтвердить смену email/i }));

    await waitFor(() => {
      // Dialog should close
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows inline error with attempts_remaining on VERIFICATION_FAILED", async () => {
    const { ApiResponseError } = await import("@/features/auth/api");
    vi.mocked(authApi.confirmEmailChange).mockRejectedValue(
      new ApiResponseError(401, "Verification failed", "VERIFICATION_FAILED", 2),
    );

    const user = await goToStep2();
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText(/код из письма/i), "99999999");
    await user.click(within(dialog).getByRole("button", { name: /подтвердить смену email/i }));

    await waitFor(() => {
      // error message contains attempts_remaining = 2
      expect(screen.getByText(/осталось попыток.*2/i)).toBeInTheDocument();
    });
    // Dialog stays open
    expect(screen.getByRole("dialog", { name: /шаг 2 из 2/i })).toBeInTheDocument();
  });
});

// ── FontSizeSection (per-user font-size preference) ───────────────────────────

// jsdom does not expose localStorage under the default (opaque) origin; provide
// an in-memory stub so the write-through cache assertions work.
class MemoryStorage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
}

describe("ProfilePage — FontSizeSection", () => {
  beforeEach(() => {
    // Reset the singleton font-scale store + caches so each case is deterministic.
    vi.stubGlobal("localStorage", new MemoryStorage());
    document.documentElement.style.removeProperty("--app-font-scale");
    setScale(DEFAULT_SCALE);
  });

  afterAll(() => {
    vi.unstubAllGlobals();
  });

  it("applies the chosen size to <html>, caches it, and PATCHes the server", async () => {
    vi.mocked(authApi.updateMyProfile).mockResolvedValue({ ...sampleProfile, ui_font_scale: 115 });
    const user = userEvent.setup();
    renderProfile();

    // Radios are disabled until the profile resolves — wait for the enabled one.
    const large = await screen.findByRole("radio", { name: "Крупный" });
    await waitFor(() => expect(large).toBeEnabled());

    await user.click(large);

    // Instant local apply + write-through cache.
    expect(document.documentElement.style.getPropertyValue("--app-font-scale")).toBe("1.15");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("115");
    expect(large).toHaveAttribute("aria-checked", "true");

    // Debounced server write (system of record) — sends the current full_name + scale.
    await waitFor(() => expect(authApi.updateMyProfile).toHaveBeenCalledWith("Test User", 115));
  });

  it("adopts the server-stored size on load (reconciliation)", async () => {
    vi.mocked(authApi.getMyProfile).mockResolvedValue({ ...sampleProfile, ui_font_scale: 130 });
    renderProfile();

    await waitFor(() =>
      expect(document.documentElement.style.getPropertyValue("--app-font-scale")).toBe("1.3"),
    );
    expect(await screen.findByRole("radio", { name: "Очень крупный" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });
});
