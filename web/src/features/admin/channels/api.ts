// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Admin channels — typed wrappers around BFF channel endpoints.
 * Backend spec: admin-channels.md US-2..6, Phase 2.
 *
 * IMPORTANT: GET /api/v1/admin/channels does NOT return config secrets.
 *            PUT sends them; never read them back.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export type ChannelName = "email" | "telegram" | "dion" | "exchange_calendar" | "ics_feed";
export type ChannelStatus = "ok" | "not_configured" | "decrypt_error";

export interface ChannelInfo {
  channel: ChannelName;
  enabled: boolean;
  configured: boolean;
  status: ChannelStatus;
  updated_at: string | null;
  /** Only present on ics_feed channel after save — the generated feed token */
  config?: Record<string, unknown> | undefined;
}

export interface EmailConfig {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  from_address: string;
  from_name: string;
}

export interface TelegramConfig {
  bot_token: string;
  default_parse_mode: "HTML" | "MarkdownV2";
}

export interface DionConfig {
  api_base: string;
  api_token: string;
  workspace_id?: string | undefined;
}

export interface ExchangeCalendarConfig {
  ews_url: string;
  service_account_login: string;
  service_account_password: string;
  target_mailbox: string;
  auth_type: "NTLM" | "Basic";
  verify_ssl: boolean;
  default_notice_days: number;
}

export interface IcsFeedConfig {
  token: string;
  cache_ttl_seconds: number;
}

export type ChannelConfig =
  | EmailConfig
  | TelegramConfig
  | DionConfig
  | ExchangeCalendarConfig
  | IcsFeedConfig;

export interface SetChannelBody {
  enabled: boolean;
  config: ChannelConfig;
  totp_code: string;
}

export interface PatchChannelBody {
  enabled?: boolean;
  totp_code: string;
}

export interface TestChannelResponse {
  // Email/Telegram/Dion test response
  queued?: boolean;
  destination?: string;
  test_id?: string;
  /** For email channel: which transport actually delivered ("smtp" | "ews"). */
  transport?: "smtp" | "ews" | null;
  // Exchange Calendar test response (CalendarTestResult)
  success?: boolean;
  detail?: string;
  latency_ms?: number | null;
}

/** Machine-readable error codes from backend — never display detail verbatim */
export type ChannelErrorCode =
  | "MIN_ADMINS"
  | "NO_CHANNEL"
  | "PENDING_INVITES"
  | "REMFA_REQUIRED"
  | "REMFA_REPLAY"
  | "USER_ACTIVATED"
  | "NOT_IMPLEMENTED"
  | "SECRET_REQUIRED";

export interface ChannelApiError {
  detail: string;
  code?: ChannelErrorCode;
}

/** Invite user body for POST /api/v1/admin/users (channels-aware version) */
export interface InviteUserBody {
  email: string;
  full_name: string;
  role: "admin" | "editor" | "viewer";
  delivery: "auto" | "show-otp";
  totp_code: string;
}

/** Response when delivery="auto" */
export interface InviteUserAutoResponse {
  user_id: string;
  channel_used: string;
  invitation_id: string;
}

/** Response when delivery="show-otp" */
export interface InviteUserOtpResponse {
  user_id: string;
  otp: string;
  otp_ttl_minutes: number;
}

export type InviteUserResponse = InviteUserAutoResponse | InviteUserOtpResponse;

export interface ReInviteUserBody {
  delivery: "auto" | "show-otp";
  totp_code: string;
}

// ── Token accessor (mirrors auth/api.ts pattern) ──────────────────────────────

let _getToken: (() => string | null) | null = null;

export function registerChannelTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeRequestId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

function authHeaders(): Record<string, string> {
  const token = _getToken?.() ?? null;
  const h: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Request-Id": makeRequestId(),
  };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

export class ChannelApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code?: ChannelErrorCode,
  ) {
    super(detail);
    this.name = "ChannelApiResponseError";
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    ...init,
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  });

  if (!res.ok) {
    let detail = "Неизвестная ошибка";
    let code: ChannelErrorCode | undefined;
    try {
      const body = (await res.json()) as ChannelApiError;
      if (body.detail) detail = body.detail;
      if (body.code) code = body.code;
    } catch {
      // non-JSON body
    }
    throw new ChannelApiResponseError(res.status, detail, code);
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

// ── Channels API ──────────────────────────────────────────────────────────────

export interface ChannelConfigResponse {
  channel: ChannelName;
  /** Decrypted config with secret fields replaced by "********" */
  config: Record<string, unknown>;
}

/** GET /api/v1/admin/channels */
export async function listChannels(): Promise<ChannelInfo[]> {
  return apiFetch<ChannelInfo[]>("/v1/admin/channels");
}

/** PUT /api/v1/admin/channels/{channel} */
export async function setChannel(channel: ChannelName, body: SetChannelBody): Promise<ChannelInfo> {
  return apiFetch<ChannelInfo>(`/v1/admin/channels/${channel}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** PATCH /api/v1/admin/channels/{channel} */
export async function patchChannel(
  channel: ChannelName,
  body: PatchChannelBody,
): Promise<ChannelInfo> {
  return apiFetch<ChannelInfo>(`/v1/admin/channels/${channel}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** GET /api/v1/admin/channels/{channel}/config — returns current config (secrets as "********") */
export async function getChannelConfig(channel: ChannelName): Promise<ChannelConfigResponse> {
  return apiFetch<ChannelConfigResponse>(`/v1/admin/channels/${channel}/config`);
}

/** POST /api/v1/admin/channels/{channel}/test */
export async function testChannel(
  channel: ChannelName,
  totp_code: string,
): Promise<TestChannelResponse> {
  return apiFetch<TestChannelResponse>(`/v1/admin/channels/${channel}/test`, {
    method: "POST",
    body: JSON.stringify({ totp_code }),
  });
}

// ── Users (channels-aware) ────────────────────────────────────────────────────

/** POST /api/v1/admin/users — channels-aware invite */
export async function inviteUser(body: InviteUserBody): Promise<InviteUserResponse> {
  return apiFetch<InviteUserResponse>("/v1/admin/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** POST /api/v1/admin/users/{id}/invite — re-invite */
export async function reInviteUser(
  userId: string,
  body: ReInviteUserBody,
): Promise<InviteUserResponse> {
  return apiFetch<InviteUserResponse>(`/v1/admin/users/${userId}/invite`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
