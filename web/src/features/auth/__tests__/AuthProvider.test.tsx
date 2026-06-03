// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AuthProvider state-machine tests.
 *
 * Mocks the auth api module and BroadcastChannel.
 * Tests the transitions: login → totp-required → authenticated.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, useAuth } from "../AuthProvider";

// ── Mocks ─────────────────────────────────────────────────────────────────────

// Mock BroadcastChannel
class MockBC {
  onmessage = null;
  postMessage = vi.fn();
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  close = vi.fn();
}
// @ts-expect-error -- mock
globalThis.BroadcastChannel = MockBC;

// A minimal valid RS256-style JWT (header.payload.sig — not verified client-side)
function makeJwt(claims: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const payload = btoa(
    JSON.stringify({
      sub: "user-uuid",
      email: "dan@example.com",
      role: "editor",
      sid: "session-uuid",
      jti: "jti-uuid",
      iss: "lotsman-auth",
      aud: "lotsman-spa",
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 900,
      nbf: Math.floor(Date.now() / 1000),
      ...claims,
    }),
  );
  return `${header}.${payload}.sig`;
}

// Mock the api module
vi.mock("../api", () => ({
  login: vi.fn(),
  verifyTotp: vi.fn(),
  verifyBackupCode: vi.fn(),
  refreshToken: vi.fn(),
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

import * as api from "../api";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

function StatusDisplay() {
  const { status, claims } = useAuth();
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="email">{claims?.email ?? ""}</span>
      <span data-testid="role">{claims?.role ?? ""}</span>
    </div>
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  // Default: silent refresh fails → unauthenticated
  vi.mocked(api.refreshToken).mockRejectedValue(new Error("no cookie"));
});

describe("AuthProvider initial state", () => {
  it("transitions from unknown/loading to unauthenticated when refresh fails", async () => {
    render(
      <Wrapper>
        <StatusDisplay />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    });
  });

  it("transitions to authenticated when refresh succeeds", async () => {
    const token = makeJwt({});
    vi.mocked(api.refreshToken).mockResolvedValue({
      access_token: token,
      token_type: "Bearer",
    });

    render(
      <Wrapper>
        <StatusDisplay />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("authenticated");
      expect(screen.getByTestId("email").textContent).toBe("dan@example.com");
    });
  });
});

describe("AuthProvider login flow", () => {
  it("login → totp-required status on next_step=verify_totp", async () => {
    vi.mocked(api.refreshToken).mockRejectedValue(new Error("no cookie"));
    vi.mocked(api.login).mockResolvedValue({
      next_step: "verify_totp",
      totp_session_token: "session-tok",
    });

    let authCtx!: ReturnType<typeof useAuth>;

    function Capture() {
      authCtx = useAuth();
      return <StatusDisplay />;
    }

    render(
      <Wrapper>
        <Capture />
      </Wrapper>,
    );

    // Wait for initial silent refresh to settle
    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    });

    await act(async () => {
      await authCtx.login("dan@example.com", "SecurePass123");
    });

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("totp-required");
    });
  });

  it("completeTotp → authenticated status on success", async () => {
    vi.mocked(api.refreshToken).mockRejectedValue(new Error("no cookie"));
    vi.mocked(api.login).mockResolvedValue({
      next_step: "verify_totp",
      totp_session_token: "session-tok",
    });
    const token = makeJwt({});
    vi.mocked(api.verifyTotp).mockResolvedValue({
      access_token: token,
      token_type: "Bearer",
    });

    let authCtx!: ReturnType<typeof useAuth>;

    function Capture() {
      authCtx = useAuth();
      return <StatusDisplay />;
    }

    render(
      <Wrapper>
        <Capture />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    });

    await act(async () => {
      await authCtx.login("dan@example.com", "SecurePass123");
    });

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("totp-required");
    });

    await act(async () => {
      await authCtx.completeTotp("session-tok", "123456");
    });

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("authenticated");
      expect(screen.getByTestId("email").textContent).toBe("dan@example.com");
      expect(screen.getByTestId("role").textContent).toBe("editor");
    });
  });

  it("login success with next_step=none → directly authenticated", async () => {
    vi.mocked(api.refreshToken).mockRejectedValue(new Error("no cookie"));
    const token = makeJwt({});
    vi.mocked(api.login).mockResolvedValue({
      next_step: "none",
      access_token: token,
      token_type: "Bearer",
    });

    let authCtx!: ReturnType<typeof useAuth>;

    function Capture() {
      authCtx = useAuth();
      return <StatusDisplay />;
    }

    render(
      <Wrapper>
        <Capture />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    await act(async () => {
      await authCtx.login("dan@example.com", "SecurePass123");
    });

    await waitFor(() => {
      expect(screen.getByTestId("status").textContent).toBe("authenticated");
    });
  });

  it("login failure → back to unauthenticated", async () => {
    vi.mocked(api.refreshToken).mockRejectedValue(new Error("no cookie"));
    vi.mocked(api.login).mockRejectedValue(new api.ApiResponseError(401, "Invalid credentials"));

    let authCtx!: ReturnType<typeof useAuth>;

    function Capture() {
      authCtx = useAuth();
      return <StatusDisplay />;
    }

    render(
      <Wrapper>
        <Capture />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    await act(async () => {
      try {
        await authCtx.login("bad@example.com", "wrong");
      } catch {
        // expected
      }
    });

    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
  });
});

describe("AuthProvider logout", () => {
  it("logout clears token and transitions to unauthenticated", async () => {
    const token = makeJwt({});
    vi.mocked(api.refreshToken).mockResolvedValue({ access_token: token, token_type: "Bearer" });
    vi.mocked(api.logout).mockResolvedValue(undefined);

    let authCtx!: ReturnType<typeof useAuth>;

    function Capture() {
      authCtx = useAuth();
      return <StatusDisplay />;
    }

    render(
      <Wrapper>
        <Capture />
      </Wrapper>,
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));

    await act(async () => {
      await authCtx.logout();
    });

    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    expect(screen.getByTestId("email").textContent).toBe("");
  });
});

describe("useAuth outside provider", () => {
  it("throws an error when used outside AuthProvider", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    function BadComponent() {
      useAuth();
      return null;
    }

    expect(() => render(<BadComponent />)).toThrow("useAuth must be used within <AuthProvider>");

    consoleError.mockRestore();
  });
});
