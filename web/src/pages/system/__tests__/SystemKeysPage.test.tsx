// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * SystemKeysPage tests.
 *
 * Covers:
 * - Red row highlight when days_since >= 90
 * - Yellow row highlight when days_since 75-89
 * - No highlight when days_since < 75
 * - "Record rotation" button opens dialog
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
      [k: string]: unknown;
    }) => (
      <a href={to} {...props}>
        {children}
      </a>
    ),
    useLocation: () => ({ pathname: "/system/keys" }),
    useNavigate: () => vi.fn(),
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

// ── Auth mock ─────────────────────────────────────────────────────────────────

vi.mock("@/features/auth/api", () => ({
  login: vi.fn(),
  verifyTotp: vi.fn(),
  verifyBackupCode: vi.fn(),
  refreshToken: vi.fn().mockRejectedValue(new Error("no cookie")),
  logout: vi.fn(),
  registerTokenAccessor: vi.fn(),
  ApiResponseError: class extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

const MockAuthContext = React.createContext<{
  claims: JwtClaims | null;
  status: string;
  logout: () => void;
}>({
  claims: null,
  status: "authenticated",
  logout: vi.fn(),
});

vi.mock("@/features/auth/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => (
    <MockAuthContext.Provider value={{ claims: null, status: "authenticated", logout: vi.fn() }}>
      {children}
    </MockAuthContext.Provider>
  ),
  useAuth: () => React.useContext(MockAuthContext),
  TAB_ID: "test-tab",
}));

// ── System API mock ───────────────────────────────────────────────────────────

vi.mock("@/features/system/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/system/api")>();
  return {
    ...original,
    fetchSystemKeys: vi.fn(),
    recordKeyRotation: vi.fn(),
  };
});

import * as systemApi from "@/features/system/api";
import { SystemKeysPage } from "../SystemKeysPage";

// ── Sample data ───────────────────────────────────────────────────────────────

const sampleKeys = [
  {
    key_id: "jwt-signing-key",
    rotated_at: "2025-01-01T00:00:00Z",
    rotated_by_email: "admin@example.com",
    days_since: 92, // red
  },
  {
    key_id: "encryption-key",
    rotated_at: "2025-02-01T00:00:00Z",
    rotated_by_email: "admin@example.com",
    days_since: 78, // yellow
  },
  {
    key_id: "session-key",
    rotated_at: "2025-04-01T00:00:00Z",
    rotated_by_email: "admin@example.com",
    days_since: 30, // ok
  },
];

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <I18nextProvider i18n={i18n}>
        <SystemKeysPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(systemApi.fetchSystemKeys).mockResolvedValue(sampleKeys);
});

describe("SystemKeysPage — row highlighting", () => {
  it("shows red badge for days_since >= 90", async () => {
    renderPage();
    await waitFor(() => screen.getByTestId("key-row-jwt-signing-key"));

    const redBadge = screen.getByTestId("days-badge-red");
    expect(redBadge).toBeInTheDocument();
    expect(redBadge).toHaveTextContent("92");
  });

  it("shows yellow badge for days_since 75-89", async () => {
    renderPage();
    await waitFor(() => screen.getByTestId("key-row-encryption-key"));

    const yellowBadge = screen.getByTestId("days-badge-yellow");
    expect(yellowBadge).toBeInTheDocument();
    expect(yellowBadge).toHaveTextContent("78");
  });

  it("no red badge within the session-key row (days_since=30 < 75)", async () => {
    renderPage();
    const sessionKeyRow = await screen.findByTestId("key-row-session-key");

    // The session-key row itself should not contain a red badge
    expect(sessionKeyRow.querySelector("[data-testid='days-badge-red']")).toBeNull();
    expect(sessionKeyRow.querySelector("[data-testid='days-badge-yellow']")).toBeNull();
  });
});

describe("SystemKeysPage — record rotation button", () => {
  it("opens RecordRotationDialog when button is clicked", async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => screen.getByTestId("key-row-jwt-signing-key"));

    const buttons = screen.getAllByRole("button", {
      name: /зафиксировать ротацию|record rotation/i,
    });
    expect(buttons.length).toBeGreaterThan(0);

    await user.click(buttons[0] as HTMLButtonElement);

    // Dialog should open
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});
