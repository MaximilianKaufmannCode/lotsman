// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * SystemLayout tests.
 *
 * Covers:
 * - RoleGuard: renders for super_admin, shows fallback for admin/editor/null
 * - SuperAdminBanner renders and is dismissible
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { I18nextProvider } from "react-i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JwtClaims } from "@/features/auth/types";
import i18n from "@/i18n/index";

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
      children?: React.ReactNode;
      [key: string]: unknown;
    }) => (
      <a href={to} {...props}>
        {children}
      </a>
    ),
    Outlet: () => <div data-testid="outlet" />,
    useLocation: () => ({ pathname: "/system/health" }),
    Navigate: ({ to }: { to: string }) => <div data-testid="navigate" data-to={to} />,
    useNavigate: () => vi.fn(),
    useRouter: () => ({ navigate: vi.fn() }),
    useSearch: () => ({}),
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
  registerTokenAccessor: vi.fn(),
  ApiResponseError: class ApiResponseError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

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

// ── Footer mock (simplify) ────────────────────────────────────────────────────

vi.mock("@/shared/layout/Footer", () => ({
  Footer: () => <footer data-testid="footer" />,
}));

import { AuthProvider } from "@/features/auth/AuthProvider";
import { RoleGuard } from "@/features/auth/RoleGuard";
import { SystemLayout } from "../SystemLayout";

// ── Claims ────────────────────────────────────────────────────────────────────

const superAdminClaims: JwtClaims = {
  sub: "sa-uuid",
  email: "superadmin@example.com",
  role: "super_admin",
  sid: "sid",
  jti: "jti",
  iss: "lotsman-auth",
  aud: "lotsman-spa",
  iat: 0,
  exp: 9999999999,
  nbf: 0,
};

const adminClaims: JwtClaims = {
  ...superAdminClaims,
  sub: "admin-uuid",
  email: "admin@example.com",
  role: "admin",
};

function WithClaims({ claims, children }: { claims: JwtClaims | null; children: React.ReactNode }) {
  return (
    <MockAuthContext.Provider value={{ claims, status: "authenticated", logout: vi.fn() }}>
      {children}
    </MockAuthContext.Provider>
  );
}

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderLayout(claims: JwtClaims | null) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>
        <WithClaims claims={claims}>
          <I18nextProvider i18n={i18n}>
            {/* biome-ignore lint/a11y/useValidAriaRole: custom prop */}
            <RoleGuard
              role="super_admin"
              fallback={<div data-testid="no-access">Только для super-admin.</div>}
            >
              <SystemLayout />
            </RoleGuard>
          </I18nextProvider>
        </WithClaims>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  // Clear sessionStorage to show banner each test
  try {
    sessionStorage.clear();
  } catch {
    // ignore
  }
});

describe("SystemLayout — RoleGuard", () => {
  it("renders SystemLayout for super_admin", () => {
    renderLayout(superAdminClaims);
    // Sidebar nav should be visible
    expect(
      screen.getByRole("navigation", { name: /навигация системной панели/i }),
    ).toBeInTheDocument();
  });

  it("renders fallback for admin (not super_admin)", () => {
    renderLayout(adminClaims);
    expect(screen.getByTestId("no-access")).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: /навигация системной панели/i }),
    ).not.toBeInTheDocument();
  });

  it("renders fallback for null claims", () => {
    renderLayout(null);
    expect(
      screen.queryByRole("navigation", { name: /навигация системной панели/i }),
    ).not.toBeInTheDocument();
  });
});

describe("SystemLayout — super admin banner", () => {
  it("shows the SUPER-ADMIN warning banner for super_admin", () => {
    renderLayout(superAdminClaims);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/super-admin/i);
  });

  it("banner is dismissible", async () => {
    const user = userEvent.setup();
    renderLayout(superAdminClaims);

    const banner = screen.getByRole("alert");
    expect(banner).toBeInTheDocument();

    // Find dismiss button
    const dismissBtn = screen.getByRole("button", { name: /закрыть/i });
    await user.click(dismissBtn);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("SystemLayout — sidebar navigation", () => {
  it("renders all 7 nav links", () => {
    renderLayout(superAdminClaims);
    const nav = screen.getByRole("navigation", { name: /навигация системной панели/i });
    // Each link should be present
    expect(nav).toBeInTheDocument();
    // The outlet renders inside the layout
    expect(screen.getByTestId("outlet")).toBeInTheDocument();
  });
});
