// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Calendar subscriptions API — typed wrappers around BFF endpoints.
 * Phase 3 of the exchange-calendar feature (US-3).
 *
 * POST and DELETE require a fresh TOTP code in body (re-MFA pattern).
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export type CalendarSubscriptionShareStatus =
  | "not_attempted"
  | "pending"
  | "granted"
  | "failed"
  | "revoked";

export interface CalendarSubscription {
  user_id: string;
  enabled: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  share_status: CalendarSubscriptionShareStatus;
  share_granted_at: string | null;
  share_error: string | null;
  /** Per-user URL-safe token. Combined with current origin yields the full
   * ICS feed URL — paste into Outlook «Open calendar from URL». */
  ics_feed_token: string | null;
}

export type CalendarSubscriptionErrorCode =
  | "USER_ALREADY_SUBSCRIBED"
  | "SUBSCRIPTION_NOT_FOUND"
  | "REMFA_REQUIRED"
  | "REMFA_REPLAY";

export interface CalendarSubscriptionApiError {
  detail: string;
  code?: CalendarSubscriptionErrorCode;
}

export class CalendarSubscriptionApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code?: CalendarSubscriptionErrorCode,
  ) {
    super(detail);
    this.name = "CalendarSubscriptionApiResponseError";
  }
}

// ── Token accessor (matches pattern in channels/api.ts) ───────────────────────

let _getToken: (() => string | null) | null = null;

export function registerCalendarSubscriptionTokenAccessor(fn: () => string | null): void {
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

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    ...init,
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  });

  if (!res.ok) {
    let detail = "Неизвестная ошибка";
    let code: CalendarSubscriptionErrorCode | undefined;
    try {
      const body = (await res.json()) as CalendarSubscriptionApiError;
      if (body.detail) detail = body.detail;
      if (body.code) code = body.code;
    } catch {
      // non-JSON body
    }
    throw new CalendarSubscriptionApiResponseError(res.status, detail, code);
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

// ── Calendar Subscriptions API ────────────────────────────────────────────────

/** GET /api/v1/admin/calendar-subscriptions */
export async function listCalendarSubscriptions(): Promise<CalendarSubscription[]> {
  return apiFetch<CalendarSubscription[]>("/v1/admin/calendar-subscriptions");
}

/** POST /api/v1/admin/calendar-subscriptions */
export async function addCalendarSubscription(
  user_id: string,
  totp_code: string,
): Promise<CalendarSubscription> {
  return apiFetch<CalendarSubscription>("/v1/admin/calendar-subscriptions", {
    method: "POST",
    body: JSON.stringify({ user_id, totp_code }),
  });
}

/** DELETE /api/v1/admin/calendar-subscriptions/{user_id} */
export async function removeCalendarSubscription(
  user_id: string,
  totp_code: string,
): Promise<void> {
  return apiFetch<void>(`/v1/admin/calendar-subscriptions/${user_id}`, {
    method: "DELETE",
    body: JSON.stringify({ totp_code }),
  });
}

/** POST /api/v1/admin/calendar-subscriptions/{user_id}/retry-share
 *
 * Re-attempt the EWS Reviewer-grant for a subscriber whose share_status
 * is `failed` / `not_attempted`. Useful after IT fixed mailbox folder
 * permissions on the Exchange side. No re-MFA — read-only-ish operation. */
export async function retryShareCalendarSubscription(
  user_id: string,
): Promise<CalendarSubscription> {
  return apiFetch<CalendarSubscription>(
    `/v1/admin/calendar-subscriptions/${user_id}/retry-share`,
    { method: "POST" },
  );
}

/** POST /api/v1/admin/calendar-subscriptions/{user_id}/mark-granted
 *
 * Flip share_status to «granted» without calling EWS. Use case:
 * corp Exchange refuses EWS PermissionSet writes and IT granted
 * Reviewer manually via `Add-MailboxFolderPermission`. This is purely
 * an audit-trail flip — admin asserts the grant happened. */
export async function markCalendarShareGranted(
  user_id: string,
): Promise<CalendarSubscription> {
  return apiFetch<CalendarSubscription>(
    `/v1/admin/calendar-subscriptions/${user_id}/mark-granted`,
    { method: "POST" },
  );
}
