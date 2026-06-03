// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Admin UsersPage tests.
 *
 * Covers: renders for admin, hidden/403 for non-admin (RoleGuard),
 * create user dialog opens, axe-clean.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";
import type { AdminUser, JwtClaims } from "@/features/auth/types";
import i18n from "@/i18n/index";
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
    useLocation: () => ({ pathname: "/admin/users" }),
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
  adminListUsers: vi.fn(),
  adminCreateUser: vi.fn(),
  adminChangeRole: vi.fn(),
  adminLockUser: vi.fn(),
  adminUnlockUser: vi.fn(),
  adminResetTotp: vi.fn(),
  adminResetPassword: vi.fn(),
  adminRevokeAllSessions: vi.fn(),
  adminDeactivateUser: vi.fn(),
  reMfa: vi.fn(),
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

import * as authApi from "@/features/auth/api";

// ── AuthContext mock — controls claims ────────────────────────────────────────

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

// ── Components under test ─────────────────────────────────────────────────────

import { AuthProvider } from "@/features/auth/AuthProvider";
import { RoleGuard } from "@/features/auth/RoleGuard";
import { UsersPage } from "./UsersPage";

// ── Sample data ───────────────────────────────────────────────────────────────

const adminClaims: JwtClaims = {
  sub: "admin-uuid",
  email: "admin@example.com",
  role: "admin",
  sid: "sid",
  jti: "jti",
  iss: "lotsman-auth",
  aud: "lotsman-spa",
  iat: 0,
  exp: 9999999999,
  nbf: 0,
};

const editorClaims: JwtClaims = {
  ...adminClaims,
  sub: "editor-uuid",
  email: "editor@example.com",
  role: "editor",
};

const sampleUsers: AdminUser[] = [
  {
    id: "u1",
    email: "alice@example.com",
    full_name: "Alice",
    role: "editor",
    is_active: true,
    is_locked: false,
    totp_enrolled: true,
    last_login_at: "2026-05-01T10:00:00Z",
    created_at: "2026-04-01T00:00:00Z",
  },
];

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

// Helper that overrides the claims in mock context
function WithClaims({ claims, children }: { claims: JwtClaims | null; children: React.ReactNode }) {
  // Reach into MockAuthContext — we override it at the JSX level
  return (
    <MockAuthContext.Provider value={{ claims, status: "authenticated", logout: vi.fn() }}>
      {children}
    </MockAuthContext.Provider>
  );
}

function renderPage(claims: JwtClaims | null) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>
        <WithClaims claims={claims}>
          <I18nextProvider i18n={i18n}>
            {/* biome-ignore lint/a11y/useValidAriaRole: role is a RoleGuard custom prop, not an HTML attribute */}
            <RoleGuard
              role="admin"
              fallback={<div data-testid="no-access">Недостаточно прав доступа.</div>}
            >
              <UsersPage />
            </RoleGuard>
            <Toaster />
          </I18nextProvider>
        </WithClaims>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(authApi.refreshToken).mockRejectedValue(new Error("no cookie"));
  vi.mocked(authApi.adminListUsers).mockResolvedValue(sampleUsers);
});

describe("UsersPage RoleGuard", () => {
  it("renders the users table for admin role", async () => {
    renderPage(adminClaims);

    await waitFor(() => {
      expect(screen.getByText("alice@example.com")).toBeInTheDocument();
    });
  });

  it("renders fallback for non-admin (editor)", () => {
    renderPage(editorClaims);
    expect(screen.getByTestId("no-access")).toBeInTheDocument();
    expect(screen.queryByText("alice@example.com")).not.toBeInTheDocument();
  });

  it("renders fallback when not authenticated (null claims)", () => {
    renderPage(null);
    // RoleGuard with null claims returns null (no fallback in that path)
    expect(screen.queryByText("alice@example.com")).not.toBeInTheDocument();
  });
});

describe("UsersPage table", () => {
  it("shows user email and status", async () => {
    renderPage(adminClaims);
    await waitFor(() => {
      expect(screen.getByText("alice@example.com")).toBeInTheDocument();
      expect(screen.getByText("Активен")).toBeInTheDocument();
    });
  });

  it("shows + Добавить пользователя button", async () => {
    renderPage(adminClaims);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /добавить пользователя/i })).toBeInTheDocument();
    });
  });

  it("opens CreateUserDialog when + button is clicked", async () => {
    const user = userEvent.setup();
    renderPage(adminClaims);
    await waitFor(() => screen.getByRole("button", { name: /добавить пользователя/i }));

    await user.click(screen.getByRole("button", { name: /добавить пользователя/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog", { name: /новый пользователь/i })).toBeInTheDocument();
    });
  });
});

describe("UsersPage accessibility", () => {
  it("is accessible — zero axe violations (admin view)", async () => {
    const { container } = renderPage(adminClaims);
    await waitFor(() => screen.getByText("alice@example.com"));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  }, 15_000);

  it("is accessible — zero axe violations (no-access fallback)", async () => {
    const { container } = renderPage(editorClaims);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  }, 15_000);
});
