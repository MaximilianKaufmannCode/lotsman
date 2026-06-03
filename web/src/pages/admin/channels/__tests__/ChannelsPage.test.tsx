// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ChannelsPage tests — Phase 3 additions:
 *  1. exchange_calendar zod schema rejects http:// URLs
 *  2. ics_feed schema rejects tokens <32 chars
 *  3. Multi-channel warning banner shows when exactly 1 channel is enabled
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type * as React from "react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi } from "vitest";
import i18n from "@/i18n/index";
import { exchangeCalendarSchema, icsFeedSchema } from "@/pages/admin/channels/ChannelsPage";

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
    useSearch: () => ({}),
    useNavigate: () => vi.fn(),
    useLocation: () => ({ pathname: "/admin/channels" }),
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

// ── Channels API mock ─────────────────────────────────────────────────────────

vi.mock("@/features/admin/channels/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/admin/channels/api")>();
  return {
    ...original,
    listChannels: vi.fn().mockResolvedValue([]),
    setChannel: vi.fn(),
    patchChannel: vi.fn(),
    testChannel: vi.fn(),
    getChannelConfig: vi.fn().mockResolvedValue({ channel: "email", config: {} }),
    registerChannelTokenAccessor: vi.fn(),
    ChannelApiResponseError: class ChannelApiResponseError extends Error {
      status: number;
      detail: string;
      code?: string;
      constructor(status: number, detail: string, code?: string) {
        super(detail);
        this.status = status;
        this.detail = detail;
        if (code !== undefined) this.code = code;
        this.name = "ChannelApiResponseError";
      }
    },
  };
});

// ── Auth api mock ─────────────────────────────────────────────────────────────

vi.mock("@/features/auth/api", () => ({
  refreshToken: vi.fn().mockRejectedValue(new Error("no cookie")),
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

// ── Helper: wrap with providers ───────────────────────────────────────────────

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

// ── Multi-channel warning banner component (isolated) ─────────────────────────

// We import it indirectly through the page. For the banner test we need the
// ChannelsPage rendered with controlled channel data.

import type { ChannelInfo } from "@/features/admin/channels/api";
import { listChannels } from "@/features/admin/channels/api";

const { ChannelsPage } = await import("@/pages/admin/channels/ChannelsPage");

function makeChannelInfo(channel: ChannelInfo["channel"], enabled: boolean): ChannelInfo {
  return {
    channel,
    enabled,
    configured: enabled,
    status: enabled ? "ok" : "not_configured",
    updated_at: null,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("exchangeCalendarSchema", () => {
  it("accepts a valid https:// EWS URL", () => {
    const result = exchangeCalendarSchema.safeParse({
      ews_url: "https://mail.example.com/EWS/Exchange.asmx",
      service_account_login: "CORP\\svc-lotsman",
      service_account_password: "secret123",
      target_mailbox: "lotsman-deadlines@example.com",
      auth_type: "NTLM",
      verify_ssl: true,
      default_notice_days: 14,
    });
    expect(result.success).toBe(true);
  });

  it("rejects http:// EWS URL", () => {
    const result = exchangeCalendarSchema.safeParse({
      ews_url: "http://mail.example.com/EWS/Exchange.asmx",
      service_account_login: "CORP\\svc-lotsman",
      service_account_password: "secret123",
      target_mailbox: "lotsman-deadlines@example.com",
      auth_type: "NTLM",
      verify_ssl: true,
      default_notice_days: 14,
    });
    expect(result.success).toBe(false);
    const issues = result.success ? [] : result.error.issues;
    expect(issues.some((i) => i.path.includes("ews_url"))).toBe(true);
  });

  it("rejects notice days outside 1..90", () => {
    const base = {
      ews_url: "https://mail.example.com/EWS/Exchange.asmx",
      service_account_login: "CORP\\user",
      service_account_password: "pass",
      target_mailbox: "cal@example.com",
      auth_type: "NTLM" as const,
      verify_ssl: true,
    };
    expect(exchangeCalendarSchema.safeParse({ ...base, default_notice_days: 0 }).success).toBe(
      false,
    );
    expect(exchangeCalendarSchema.safeParse({ ...base, default_notice_days: 91 }).success).toBe(
      false,
    );
    expect(exchangeCalendarSchema.safeParse({ ...base, default_notice_days: 14 }).success).toBe(
      true,
    );
  });
});

describe("icsFeedSchema", () => {
  it("accepts empty token (auto-generate)", () => {
    const result = icsFeedSchema.safeParse({ token: "", cache_ttl_seconds: 300 });
    expect(result.success).toBe(true);
  });

  it("accepts token with ≥32 characters", () => {
    const result = icsFeedSchema.safeParse({
      token: "a".repeat(32),
      cache_ttl_seconds: 300,
    });
    expect(result.success).toBe(true);
  });

  it("rejects token with <32 characters (non-empty)", () => {
    const result = icsFeedSchema.safeParse({
      token: "short",
      cache_ttl_seconds: 300,
    });
    expect(result.success).toBe(false);
    const issues = result.success ? [] : result.error.issues;
    expect(issues.some((i) => i.path.includes("token"))).toBe(true);
  });

  it("rejects token with exactly 31 characters", () => {
    const result = icsFeedSchema.safeParse({
      token: "a".repeat(31),
      cache_ttl_seconds: 300,
    });
    expect(result.success).toBe(false);
  });

  it("rejects cache_ttl_seconds below 60", () => {
    const result = icsFeedSchema.safeParse({ token: "", cache_ttl_seconds: 59 });
    expect(result.success).toBe(false);
  });

  it("rejects cache_ttl_seconds above 86400", () => {
    const result = icsFeedSchema.safeParse({ token: "", cache_ttl_seconds: 86401 });
    expect(result.success).toBe(false);
  });
});

describe("MultiChannelWarningBanner via ChannelsPage", () => {
  it("shows warning banner when exactly 1 channel is enabled", async () => {
    vi.mocked(listChannels).mockResolvedValueOnce([
      makeChannelInfo("email", true),
      makeChannelInfo("telegram", false),
      makeChannelInfo("dion", false),
      makeChannelInfo("exchange_calendar", false),
      makeChannelInfo("ics_feed", false),
    ]);

    // Clear sessionStorage to ensure banner is not dismissed
    sessionStorage.removeItem("multi-channel-warning-dismissed");

    const qc = makeQC();
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={qc}>
          <ChannelsPage />
        </QueryClientProvider>
      </I18nextProvider>,
    );

    // Banner appears after data loads
    const banner = await screen.findByTestId("multi-channel-warning");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("Email");
  });

  it("does NOT show warning banner when 2+ channels are enabled", async () => {
    vi.mocked(listChannels).mockResolvedValueOnce([
      makeChannelInfo("email", true),
      makeChannelInfo("telegram", true),
      makeChannelInfo("dion", false),
      makeChannelInfo("exchange_calendar", false),
      makeChannelInfo("ics_feed", false),
    ]);

    sessionStorage.removeItem("multi-channel-warning-dismissed");

    const qc = makeQC();
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={qc}>
          <ChannelsPage />
        </QueryClientProvider>
      </I18nextProvider>,
    );

    // Wait for data and verify banner is NOT shown
    await screen.findByText("Каналы уведомлений"); // page title loaded
    const banner = screen.queryByTestId("multi-channel-warning");
    expect(banner).toBeNull();
  });

  it("does NOT show warning banner when 0 channels enabled", async () => {
    vi.mocked(listChannels).mockResolvedValueOnce([
      makeChannelInfo("email", false),
      makeChannelInfo("telegram", false),
    ]);

    sessionStorage.removeItem("multi-channel-warning-dismissed");

    const qc = makeQC();
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={qc}>
          <ChannelsPage />
        </QueryClientProvider>
      </I18nextProvider>,
    );

    await screen.findByText("Каналы уведомлений");
    const banner = screen.queryByTestId("multi-channel-warning");
    expect(banner).toBeNull();
  });
});
