// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Auth feature — typed wrappers around BFF auth endpoints.
 *
 * All requests include `credentials: 'include'` so the HttpOnly refresh cookie
 * flows automatically (ADR-0003 §8, §9).
 *
 * The access token is injected via the `getAccessToken` callback to avoid stale
 * closures — callers set this callback once AuthProvider mounts.
 *
 * Endpoint shapes match ADR-0003 + requirements/auth.md.
 * The schema.gen.ts is not yet populated (backend in parallel),
 * so we use a thin fetch-based adapter here instead of openapi-fetch.
 */

import type {
  AdminUser,
  AdminUserDetail,
  AdminUserSession,
  BackupCodesRegenerateResponse,
  BackupCodeVerifyResponse,
  CreateUserResponse,
  LoginResponse,
  RefreshResponse,
  ReMfaResponse,
  SessionItem,
  TotpEnrollResponse,
  TotpVerifyResponse,
  UserProfile,
  UserRole,
} from "./types";
import { recoverFrom401 } from "@/shared/api/interceptor";

const BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api") as string;

// ── Token accessor ────────────────────────────────────────────────────────────

/** Set by AuthProvider — avoids stale-closure issues in module-level interceptors. */
let _getToken: (() => string | null) | null = null;

export function registerTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

// ── Low-level fetch helpers ───────────────────────────────────────────────────

function makeRequestId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

interface FetchOptions {
  method?: string;
  body?: unknown;
  /** Include Authorization: Bearer header */
  auth?: boolean;
  /** Extra headers */
  headers?: Record<string, string>;
}

export async function apiFetch<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const { method = "GET", body, auth = false, headers: extraHeaders = {} } = opts;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Request-Id": makeRequestId(),
    ...extraHeaders,
  };

  if (auth && _getToken) {
    const token = _getToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  const fetchInit: RequestInit = {
    method,
    credentials: "include",
    headers,
  };
  if (body !== undefined) {
    fetchInit.body = JSON.stringify(body);
  }

  let res = await fetch(`${BASE}${path}`, fetchInit);

  // Transparent recovery from an expired access token (ADR-0003 §7): on a 401
  // for an authenticated call, refresh once and retry. The body is a serialized
  // string, so re-sending `fetchInit` is safe for any method — and a 401 is
  // rejected pre-handler, so there is no double-execution risk. Anonymous routes
  // (login / TOTP / refresh) carry no bearer and must never trigger a loop.
  if (res.status === 401 && auth && path !== "/v1/auth/refresh") {
    const sentToken = _getToken?.() ?? null;
    const newToken = await recoverFrom401(sentToken);
    if (newToken) {
      headers.Authorization = `Bearer ${newToken}`;
      res = await fetch(`${BASE}${path}`, fetchInit);
    }
  }

  if (!res.ok) {
    let detail = "Неизвестная ошибка";
    let code: string | undefined;
    let attemptsRemaining: number | undefined;
    let retryAfterSeconds: number | undefined;
    try {
      const err = (await res.json()) as {
        detail?:
          | string
          | {
              detail?: string;
              code?: string;
              attempts_remaining?: number;
              retry_after_seconds?: number;
            };
      };
      if (err.detail) {
        if (typeof err.detail === "string") {
          detail = err.detail;
        } else {
          detail = err.detail.detail ?? detail;
          code = err.detail.code;
          attemptsRemaining = err.detail.attempts_remaining;
          retryAfterSeconds = err.detail.retry_after_seconds;
        }
      }
    } catch {
      // ignore parse error
    }
    if (retryAfterSeconds === undefined) {
      const ra = res.headers.get("retry-after");
      if (ra) {
        const n = Number.parseInt(ra, 10);
        if (Number.isFinite(n) && n > 0) retryAfterSeconds = n;
      }
    }
    throw new ApiResponseError(res.status, detail, code, attemptsRemaining, retryAfterSeconds);
  }

  // 204 No Content
  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json() as Promise<T>;
}

export class ApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code?: string,
    public readonly attemptsRemaining?: number,
    public readonly retryAfterSeconds?: number,
  ) {
    super(detail);
    this.name = "ApiResponseError";
  }
}

// ── Auth endpoints ────────────────────────────────────────────────────────────

/**
 * POST /api/v1/auth/login
 * Step 1 of login flow — email + password.
 * Returns one of three shapes based on account state (ADR-0003 §3, §5).
 */
export async function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/v1/auth/login", {
    method: "POST",
    body: { email, password },
  });
}

/**
 * POST /api/v1/auth/totp/verify
 * Completes TOTP step after a successful password login.
 */
export async function verifyTotp(
  totpSessionToken: string,
  code: string,
): Promise<TotpVerifyResponse> {
  return apiFetch<TotpVerifyResponse>("/v1/auth/totp/verify", {
    method: "POST",
    body: { totp_session_token: totpSessionToken, code },
  });
}

/**
 * POST /api/v1/auth/backup-codes/verify
 * Completes login using a backup code instead of TOTP.
 */
export async function verifyBackupCode(
  totpSessionToken: string,
  code: string,
): Promise<BackupCodeVerifyResponse> {
  return apiFetch<BackupCodeVerifyResponse>("/v1/auth/backup-codes/verify", {
    method: "POST",
    body: { totp_session_token: totpSessionToken, code },
  });
}

/**
 * POST /api/v1/auth/totp/enroll
 * Step 1 of TOTP enrollment — returns secret_b32 + otpauth_url.
 * Requires enrollment-scoped token (first-login) or auth token (profile rotation).
 */
export async function enrollTotp(): Promise<TotpEnrollResponse> {
  return apiFetch<TotpEnrollResponse>("/v1/auth/totp/enroll", {
    method: "POST",
    auth: true,
  });
}

/**
 * POST /api/v1/auth/totp/enroll/confirm
 * Step 2 of enrollment — confirms the code against the pending Redis key.
 */
export async function confirmTotpEnrollment(code: string): Promise<{ backup_codes: string[] }> {
  return apiFetch<{ backup_codes: string[] }>("/v1/auth/totp/enroll/confirm", {
    method: "POST",
    auth: true,
    body: { code },
  });
}

/**
 * POST /api/v1/auth/refresh
 * Uses the HttpOnly refresh cookie. Leader-elected in BroadcastChannel.
 * No Authorization header — cookie-only endpoint.
 */
export async function refreshToken(): Promise<RefreshResponse> {
  return apiFetch<RefreshResponse>("/v1/auth/refresh", {
    method: "POST",
  });
}

/**
 * POST /api/v1/auth/logout
 * Revokes current session's refresh token. Cookie cleared by BFF.
 */
export async function logout(): Promise<void> {
  return apiFetch<void>("/v1/auth/logout", {
    method: "POST",
    auth: true,
  });
}

/**
 * POST /api/v1/auth/backup-codes/regenerate
 * Regenerates all 10 backup codes, invalidating prior ones.
 * Requires auth.
 */
export async function regenerateBackupCodes(): Promise<BackupCodesRegenerateResponse> {
  return apiFetch<BackupCodesRegenerateResponse>("/v1/auth/backup-codes/regenerate", {
    method: "POST",
    auth: true,
  });
}

/**
 * GET /api/v1/auth/sessions/me
 * Lists current user's active sessions.
 */
export async function getMySessions(): Promise<SessionItem[]> {
  return apiFetch<SessionItem[]>("/v1/auth/sessions/me", {
    auth: true,
  });
}

/**
 * DELETE /api/v1/auth/sessions/{id}
 * Revokes a specific session.
 */
export async function revokeSession(sessionId: string): Promise<void> {
  return apiFetch<void>(`/v1/auth/sessions/${sessionId}`, {
    method: "DELETE",
    auth: true,
  });
}

/**
 * POST /api/v1/auth/re-mfa
 * Obtain a short-lived re-MFA token by providing current TOTP code.
 * Required before sensitive operations (password change, admin actions).
 */
export async function reMfa(totpCode: string): Promise<ReMfaResponse> {
  return apiFetch<ReMfaResponse>("/v1/auth/re-mfa", {
    method: "POST",
    auth: true,
    body: { code: totpCode },
  });
}

/**
 * POST /api/v1/auth/password/change
 * Changes password. Requires re-MFA token.
 */
export async function changePassword(opts: {
  currentPassword: string;
  newPassword: string;
  reMfaToken: string;
}): Promise<void> {
  return apiFetch<void>("/v1/auth/password/change", {
    method: "POST",
    auth: true,
    body: {
      current_password: opts.currentPassword,
      new_password: opts.newPassword,
      re_mfa_token: opts.reMfaToken,
    },
  });
}

// ── Profile self-service endpoints ───────────────────────────────────────────

/**
 * GET /api/v1/auth/me
 * Returns the authenticated user's full profile, including totp_enrolled and is_locked.
 */
export async function getMyProfile(): Promise<UserProfile> {
  return apiFetch<UserProfile>("/v1/auth/me", { auth: true });
}

/**
 * PATCH /api/v1/auth/me
 * Updates the authenticated user's full_name. Returns the updated profile.
 */
export async function updateMyProfile(full_name: string): Promise<UserProfile> {
  return apiFetch<UserProfile>("/v1/auth/me", {
    method: "PATCH",
    auth: true,
    body: { full_name },
  });
}

/**
 * POST /api/v1/auth/me/test-email
 * Sends a diagnostic email to the authenticated user's own address. The BFF
 * resolves the recipient server-side (no SPA-supplied value). Rate-limited to
 * 1 request per 60s per user — exceeded calls return 429 with retry_after_seconds.
 */
export async function sendMyTestEmail(): Promise<{ sent: boolean; recipient: string }> {
  return apiFetch<{ sent: boolean; recipient: string }>("/v1/auth/me/test-email", {
    method: "POST",
    auth: true,
    body: {},
  });
}

// ── Notification preferences (ADR-0011) ───────────────────────────────────────

export type NotificationEmailMode = "instant" | "digest" | "off";

export interface NotificationCategoryPref {
  in_app: boolean;
  email: boolean;
}

export interface NotificationPrefs {
  enabled: boolean;
  suppress_own: boolean;
  email_mode: NotificationEmailMode;
  categories: Record<string, NotificationCategoryPref>;
}

export async function getMyNotificationPrefs(): Promise<NotificationPrefs> {
  return apiFetch<NotificationPrefs>("/v1/auth/me/notification-prefs", { auth: true });
}

export async function updateMyNotificationPrefs(
  prefs: NotificationPrefs,
): Promise<NotificationPrefs> {
  return apiFetch<NotificationPrefs>("/v1/auth/me/notification-prefs", {
    method: "PUT",
    auth: true,
    body: prefs,
  });
}

// ── In-app notification feed (ADR-0011 Phase 3) ───────────────────────────────

export interface NotificationItem {
  id: string;
  category: string;
  document_id: string | null;
  title: string;
  body: string;
  is_read: boolean;
  created_at: string;
}

export interface NotificationFeed {
  items: NotificationItem[];
  unread: number;
}

export async function getMyNotifications(limit = 30): Promise<NotificationFeed> {
  return apiFetch<NotificationFeed>(`/v1/auth/me/notifications?limit=${limit}`, {
    auth: true,
  });
}

export async function getMyUnreadCount(): Promise<{ unread: number }> {
  return apiFetch<{ unread: number }>("/v1/auth/me/notifications/unread-count", {
    auth: true,
  });
}

export async function markNotificationRead(id: string): Promise<void> {
  return apiFetch<void>(`/v1/auth/me/notifications/${id}/read`, {
    method: "POST",
    auth: true,
    body: {},
  });
}

export async function markAllNotificationsRead(): Promise<void> {
  return apiFetch<void>("/v1/auth/me/notifications/read-all", {
    method: "POST",
    auth: true,
    body: {},
  });
}

// ── Email change self-service ─────────────────────────────────────────────────

export interface RequestEmailChangeBody {
  new_email: string;
  totp_code: string;
}

export interface RequestEmailChangeResponse {
  request_id: string;
  code_ttl_seconds: number;
  masked_new_email: string;
}

export interface ConfirmEmailChangeBody {
  request_id: string;
  verification_code: string;
}

export interface ConfirmEmailChangeResponse {
  email: string;
}

/**
 * POST /api/v1/auth/me/change-email/request
 * Step 1 of email change — sends verification code to new address.
 * Requires TOTP gate (re-MFA handled by BFF).
 */
export async function requestEmailChange(
  body: RequestEmailChangeBody,
): Promise<RequestEmailChangeResponse> {
  return apiFetch<RequestEmailChangeResponse>("/v1/auth/me/change-email/request", {
    method: "POST",
    auth: true,
    body,
  });
}

/**
 * POST /api/v1/auth/me/change-email/confirm
 * Step 2 — confirms the 8-digit code sent to the new inbox.
 */
export async function confirmEmailChange(
  body: ConfirmEmailChangeBody,
): Promise<ConfirmEmailChangeResponse> {
  return apiFetch<ConfirmEmailChangeResponse>("/v1/auth/me/change-email/confirm", {
    method: "POST",
    auth: true,
    body,
  });
}

// ── Admin user endpoints ──────────────────────────────────────────────────────

/**
 * GET /api/v1/admin/users
 * Lists all users. Admin only.
 */
export async function adminListUsers(): Promise<AdminUser[]> {
  return apiFetch<AdminUser[]>("/v1/admin/users", { auth: true });
}

/**
 * GET /api/v1/admin/users/{id}
 * Full user detail (includes must_change_password, updated_at). Admin only.
 */
export async function adminGetUser(userId: string): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/v1/admin/users/${userId}`, { auth: true });
}

/**
 * PATCH /api/v1/admin/users/{id}  body: {active: true, totp_code}
 * Restores a soft-deleted user (US-104).
 * Requires re-MFA per ADR-0004 §6.
 */
export async function adminReactivateUser(userId: string, reMfaToken: string): Promise<void> {
  return apiFetch<void>(`/v1/admin/users/${userId}`, {
    method: "PATCH",
    auth: true,
    body: { active: true, totp_code: reMfaToken },
  });
}

/**
 * DELETE /api/v1/admin/users/{id}  body: {totp_code}
 * Permanent (soft) delete — hides the user and frees the email. Requires re-MFA.
 */
export async function adminDeleteUser(userId: string, reMfaToken: string): Promise<void> {
  return apiFetch<void>(`/v1/admin/users/${userId}`, {
    method: "DELETE",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * DELETE /api/v1/admin/users/{id}/sessions/{session_id}  body: {totp_code}
 * Revoke ONE specific session of a target user (US-105).
 * Requires re-MFA per ADR-0004 §6.
 */
export async function adminRevokeSession(
  userId: string,
  sessionId: string,
  reMfaToken: string,
): Promise<void> {
  return apiFetch<void>(`/v1/admin/users/${userId}/sessions/${sessionId}`, {
    method: "DELETE",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * PATCH /api/v1/admin/users/{id}  body: {full_name, totp_code}
 * Updates target user's profile (currently only full_name) — US-103.
 * Requires re-MFA per ADR-0004 §6.
 */
export async function adminUpdateUserProfile(
  userId: string,
  fields: { full_name: string },
  reMfaToken: string,
): Promise<AdminUserDetail> {
  return apiFetch<AdminUserDetail>(`/v1/admin/users/${userId}`, {
    method: "PATCH",
    auth: true,
    body: { ...fields, totp_code: reMfaToken },
  });
}

/**
 * POST /api/v1/admin/users
 * Creates a new user and returns the OOB OTP. Admin only.
 */
export async function adminCreateUser(opts: {
  email: string;
  fullName: string;
  role: UserRole;
  reMfaToken: string;
}): Promise<CreateUserResponse> {
  return apiFetch<CreateUserResponse>("/v1/admin/users", {
    method: "POST",
    auth: true,
    body: {
      email: opts.email,
      full_name: opts.fullName,
      role: opts.role,
      totp_code: opts.reMfaToken,
    },
  });
}

/**
 * PATCH /api/v1/admin/users/{id}/role
 * Changes user role. Admin only. Requires re-MFA token.
 */
export async function adminChangeRole(
  userId: string,
  role: UserRole,
  reMfaToken: string,
): Promise<void> {
  // BFF route is PATCH /users/{id} with `role` in body (not /users/{id}/role).
  // Body convention: `totp_code` (not `re_mfa_token`) — see web-bff admin.py::_verify_re_mfa.
  return apiFetch<void>(`/v1/admin/users/${userId}`, {
    method: "PATCH",
    auth: true,
    body: { role, totp_code: reMfaToken },
  });
}

/**
 * POST /api/v1/admin/users/{id}/lock
 * Locks a user (Redis flag — instant effect). Admin only. Requires re-MFA.
 */
export async function adminLockUser(userId: string, reMfaToken: string): Promise<void> {
  // BFF route is POST /users/{id}/lockout (a `/lock` path 404s).
  return apiFetch<void>(`/v1/admin/users/${userId}/lockout`, {
    method: "POST",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * DELETE /api/v1/admin/users/{id}/lockout
 * Unlocks a user. Admin only. Requires re-MFA.
 */
export async function adminUnlockUser(userId: string, reMfaToken: string): Promise<void> {
  // Unlock is DELETE on the same `/lockout` resource (a POST `/unlock` 404s).
  return apiFetch<void>(`/v1/admin/users/${userId}/lockout`, {
    method: "DELETE",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * POST /api/v1/admin/users/{id}/totp/reset
 * Resets TOTP secret to sentinel (forces re-enrollment). Admin only. Requires re-MFA.
 */
export async function adminResetTotp(userId: string, reMfaToken: string): Promise<void> {
  return apiFetch<void>(`/v1/admin/users/${userId}/totp/reset`, {
    method: "POST",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * POST /api/v1/admin/users/{id}/password/reset
 * Admin password reset — generates and returns a new OOB OTP. Requires re-MFA.
 */
export async function adminResetPassword(
  userId: string,
  reMfaToken: string,
): Promise<{ oob_otp: string }> {
  return apiFetch<{ oob_otp: string }>(`/v1/admin/users/${userId}/password/reset`, {
    method: "POST",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * DELETE /api/v1/admin/users/{id}/sessions
 * Revokes ALL sessions of a user. Admin only. Requires re-MFA.
 */
export async function adminRevokeAllSessions(userId: string, reMfaToken: string): Promise<void> {
  // BFF route is DELETE on the `/sessions` collection (a POST
  // `/sessions/revoke-all` 405s).
  return apiFetch<void>(`/v1/admin/users/${userId}/sessions`, {
    method: "DELETE",
    auth: true,
    body: { totp_code: reMfaToken },
  });
}

/**
 * POST /api/v1/admin/users/{id}/deactivate
 * Soft-deactivates a user. Admin only. Requires re-MFA.
 */
export async function adminDeactivateUser(userId: string, reMfaToken: string): Promise<void> {
  // BFF route is PATCH /users/{id} with {active: false} (not /users/{id}/deactivate).
  return apiFetch<void>(`/v1/admin/users/${userId}`, {
    method: "PATCH",
    auth: true,
    body: { active: false, totp_code: reMfaToken },
  });
}

/**
 * GET /api/v1/admin/users/{id}/sessions
 * Lists any user's sessions. Admin only (US-21).
 */
export async function adminGetUserSessions(userId: string): Promise<AdminUserSession[]> {
  return apiFetch<AdminUserSession[]>(`/v1/admin/users/${userId}/sessions`, { auth: true });
}

// ── Saved filter presets (v1.23.0) ───────────────────────────────────────────

/**
 * Saved filter preset as returned by the API.
 * filter_json is typed as unknown here — the frontend parses it through
 * the FilterState zod schema before use (security §7.3 of requirements).
 */
export interface SavedFilter {
  id: string;
  user_id: string;
  name: string;
  filter_json: Record<string, unknown>;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateSavedFilterPayload {
  name: string;
  filter_json: Record<string, unknown>;
  is_default?: boolean;
}

export interface UpdateSavedFilterPayload {
  name?: string;
  filter_json?: Record<string, unknown>;
  is_default?: boolean;
}

/**
 * GET /api/v1/auth/me/saved-filters
 * Returns all saved filter presets for the current user.
 */
export async function listMySavedFilters(): Promise<SavedFilter[]> {
  return apiFetch<SavedFilter[]>("/v1/auth/me/saved-filters", { auth: true });
}

/**
 * POST /api/v1/auth/me/saved-filters
 * Creates a new filter preset. Limit: 20 per user (enforced server-side).
 */
export async function createSavedFilter(body: CreateSavedFilterPayload): Promise<SavedFilter> {
  return apiFetch<SavedFilter>("/v1/auth/me/saved-filters", {
    method: "POST",
    auth: true,
    body,
  });
}

/**
 * PATCH /api/v1/auth/me/saved-filters/{id}
 * Updates name, filter_json, or is_default of an existing preset.
 */
export async function updateSavedFilter(
  id: string,
  body: UpdateSavedFilterPayload,
): Promise<SavedFilter> {
  return apiFetch<SavedFilter>(`/v1/auth/me/saved-filters/${id}`, {
    method: "PATCH",
    auth: true,
    body,
  });
}

/**
 * DELETE /api/v1/auth/me/saved-filters/{id}
 * Deletes a preset by id.
 */
export async function deleteSavedFilter(id: string): Promise<void> {
  return apiFetch<void>(`/v1/auth/me/saved-filters/${id}`, {
    method: "DELETE",
    auth: true,
  });
}
