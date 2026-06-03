// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * TypeScript types matching backend /api/v1/system/* response shapes.
 * ADR-0006 §8 — super_admin system panel.
 */

// ── Health ────────────────────────────────────────────────────────────────────

export type ServiceStatus = "ok" | "degraded" | "down";

export interface ServiceHealth {
  name: string;
  status: ServiceStatus;
  last_seen: string | null;
  uptime: string | null;
}

// ── Queues ────────────────────────────────────────────────────────────────────

export interface QueueEntry {
  service: string;
  outbox_undispatched: number;
  stream_lag: number;
  dlq_size: number;
  note?: string | undefined;
}

// ── Migrations ────────────────────────────────────────────────────────────────

export interface MigrationEntry {
  service: string;
  current: string;
  latest_in_code: string;
  pending: boolean;
}

// ── Keys ──────────────────────────────────────────────────────────────────────

export interface KeyEntry {
  key_id: string;
  rotated_at: string;
  rotated_by_email: string;
  days_since: number;
}

export interface KeyRotatePayload {
  totp_code: string;
  rotated_at: string;
}

// ── Logs ──────────────────────────────────────────────────────────────────────

export interface LogsResponse {
  lines: string[];
  truncated: boolean;
}

// ── Maintenance ───────────────────────────────────────────────────────────────

export interface BackupNowPayload {
  totp_code: string;
  confirmation: "BACKUP NOW";
}

export interface BackupNowResponse {
  exit_code: number;
  duration_ms: number;
  stdout_tail: string;
}

export interface RestartServicePayload {
  service: string;
  totp_code: string;
  confirmation: string;
}

export interface RestartServiceResponse {
  status: string;
}

export interface MigrateServicePayload {
  service: string;
  totp_code: string;
  confirmation: string;
}

export interface MigrateServiceResponse {
  status: string;
  applied: number;
}

// ── Audit ─────────────────────────────────────────────────────────────────────

export interface SystemAuditEntry {
  id: string;
  event_type: string;
  actor_email: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface SystemAuditListResponse {
  items: SystemAuditEntry[];
  total: number;
  has_more: boolean;
}

// ── Error codes ───────────────────────────────────────────────────────────────

export type SystemErrorCode =
  | "REMFA_REQUIRED"
  | "REMFA_REPLAY"
  | "CONFIRMATION_MISMATCH"
  | "FORBIDDEN"
  | "NOT_FOUND";
