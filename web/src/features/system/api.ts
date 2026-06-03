// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Typed wrappers for /api/v1/system/* endpoints.
 * All endpoints require role=super_admin on the backend.
 * Read-only endpoints: no TOTP.
 * Mutating endpoints: TOTP + optional typed-confirmation.
 */

import type {
  BackupNowPayload,
  BackupNowResponse,
  KeyEntry,
  KeyRotatePayload,
  LogsResponse,
  MigrateServicePayload,
  MigrateServiceResponse,
  MigrationEntry,
  QueueEntry,
  RestartServicePayload,
  RestartServiceResponse,
  ServiceHealth,
  SystemAuditEntry,
  SystemErrorCode,
} from "./types";

// ── Error class ───────────────────────────────────────────────────────────────

export class SystemApiResponseError extends Error {
  status: number;
  code: SystemErrorCode | string | null;
  detail: string;

  constructor(status: number, detail: string, code?: string | null) {
    super(detail);
    this.name = "SystemApiResponseError";
    this.status = status;
    this.detail = detail;
    this.code = code ?? null;
  }
}

// ── Token accessor wiring (registered from app/providers.tsx) ────────────────

let _getToken: (() => string | null) | null = null;

export function registerSystemTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

// ── Base fetch helper ─────────────────────────────────────────────────────────

async function systemFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = _getToken?.() ?? null;
  const res = await fetch(`/api/v1/system${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
    credentials: "include",
    ...init,
  });

  if (!res.ok) {
    let code: string | null = null;
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string; code?: string };
      detail = body.detail ?? detail;
      code = body.code ?? null;
    } catch {
      // ignore parse failure
    }
    throw new SystemApiResponseError(res.status, detail, code);
  }

  if (res.status === 204) return undefined as unknown as T;

  return res.json() as Promise<T>;
}

// ── Health ────────────────────────────────────────────────────────────────────

export async function fetchSystemHealth(): Promise<ServiceHealth[]> {
  return systemFetch<ServiceHealth[]>("/health");
}

// ── Queues ────────────────────────────────────────────────────────────────────

export async function fetchSystemQueues(): Promise<QueueEntry[]> {
  return systemFetch<QueueEntry[]>("/queues");
}

// ── Migrations ────────────────────────────────────────────────────────────────

export async function fetchSystemMigrations(): Promise<MigrationEntry[]> {
  return systemFetch<MigrationEntry[]>("/migrations");
}

// ── Keys ──────────────────────────────────────────────────────────────────────

export async function fetchSystemKeys(): Promise<KeyEntry[]> {
  return systemFetch<KeyEntry[]>("/keys");
}

export async function recordKeyRotation(keyId: string, payload: KeyRotatePayload): Promise<void> {
  await systemFetch<void>(`/keys/${encodeURIComponent(keyId)}/rotated`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── Logs ──────────────────────────────────────────────────────────────────────

export interface LogsParams {
  service: string;
  tail?: number;
}

export async function fetchSystemLogs(params: LogsParams): Promise<LogsResponse> {
  const qs = new URLSearchParams({ service: params.service });
  if (params.tail !== undefined) qs.set("tail", String(params.tail));
  return systemFetch<LogsResponse>(`/logs?${qs.toString()}`);
}

// ── Maintenance ───────────────────────────────────────────────────────────────

export async function triggerBackupNow(payload: BackupNowPayload): Promise<BackupNowResponse> {
  return systemFetch<BackupNowResponse>("/backup-now", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function restartService(
  payload: RestartServicePayload,
): Promise<RestartServiceResponse> {
  return systemFetch<RestartServiceResponse>("/restart-service", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function migrateService(
  payload: MigrateServicePayload,
): Promise<MigrateServiceResponse> {
  return systemFetch<MigrateServiceResponse>("/migrate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── System audit ──────────────────────────────────────────────────────────────

export interface SystemAuditParams {
  from?: string;
  to?: string;
  actor?: string;
  type?: string;
  limit?: number;
  cursor?: string;
}

export interface SystemAuditPage {
  items: SystemAuditEntry[];
  has_more: boolean;
  next_cursor: string | null;
}

export async function fetchSystemAudit(params: SystemAuditParams): Promise<SystemAuditPage> {
  const qs = new URLSearchParams();
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.actor) qs.set("actor", params.actor);
  if (params.type) qs.set("type", params.type);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.cursor) qs.set("cursor", params.cursor);

  const token = _getToken?.() ?? null;
  const raw = await fetch(`/api/v1/audit/system?${qs.toString()}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });

  if (!raw.ok) {
    let code: string | null = null;
    let detail = raw.statusText;
    try {
      const body = (await raw.json()) as { detail?: string; code?: string };
      detail = body.detail ?? detail;
      code = body.code ?? null;
    } catch {
      // ignore
    }
    throw new SystemApiResponseError(raw.status, detail, code);
  }

  return raw.json() as Promise<SystemAuditPage>;
}
