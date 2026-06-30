// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Registry feature — shared TypeScript types.
 * Derived from the requirements and the design spec.
 * Backend is producing the OpenAPI spec in parallel; types here are hand-rolled
 * and must be kept in sync once the spec is code-generated.
 */

// ── Domain enums ──────────────────────────────────────────────────────────────

/** Urgency status, computed from expiry_date + deleted_at at read time. Never stored. */
export type ComputedStatus = "ok" | "soon" | "overdue" | "archived";

/** Export job lifecycle (pending → running → done | failed). */
export type ExportJobStatus = "pending" | "running" | "done" | "failed" | "expired";

// ── Core domain entities ───────────────────────────────────────────────────────

export interface Asset {
  id: string;
  name: string;
  /** Russian taxpayer ID — 10 digits (legal entity) or 12 digits (individual) */
  inn: string | null;
  notes: string | null;
  /** ISO-8601 timestamp */
  created_at: string;
  updated_at: string;
  /** Soft-delete timestamp; null when active */
  deleted_at: string | null;
  /** Count of associated active documents — returned by list endpoint */
  document_count?: number;
}

export interface DocumentType {
  code: string;
  display_name: string;
  /** Days before expiry to trigger each pre-notice reminder */
  pre_notice_days: number[];
  notify_in_day: boolean;
  /** Repeat overdue notification every N days */
  overdue_every_days: number;
  created_at: string;
  updated_at: string;
  /**
   * Custom field schema for this document type.
   * Phase 3: present when backend returns it (may be absent on older endpoints).
   */
  custom_field_schema?: import("@/features/admin/document-types/custom-fields-api").CustomField[];
}

export interface Document {
  id: string;
  asset_id: string;
  /** Populated by BFF join */
  asset_name: string;
  type_code: string;
  /** Populated by BFF join */
  type_display_name: string;
  number: string;
  /** ISO-8601 date string (YYYY-MM-DD) */
  issue_date: string | null;
  /** ISO-8601 date string; null means open-ended */
  expiry_date: string | null;
  responsible_user_id: string | null;
  responsible_user_name: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  /** Soft-delete timestamp */
  deleted_at: string | null;
  /**
   * Pre-computed status returned by BFF (mirrors computeStatus pure function).
   * Frontend falls back to computeStatus() if the field is absent.
   */
  status?: ComputedStatus;
  /**
   * Custom field values: key → raw value (string | number | null).
   * Phase 3: populated by BFF when the document type has a custom_field_schema.
   */
  custom_field_values?: Record<string, string | number | null>;
}

export interface Attachment {
  id: string;
  document_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  created_by: string;
  created_at: string;
}

export interface ExportJob {
  id: string;
  status: ExportJobStatus;
  requested_by: string;
  /** ISO-8601 timestamp when the job was submitted */
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  /** ISO-8601 timestamp; 24h after completed_at */
  expires_at: string | null;
  error: string | null;
  /** Filter + sort snapshot submitted with the job */
  filter_params: FilterState;
  visible_columns: string[];
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_id: string;
  /** ФИО актора, заполняется web-bff'ом из auth-service. null → плейсхолдер «Удалённый пользователь». */
  actor_name: string | null;
  /** Например `registry.document.updated.v1`. */
  event_type: string;
  /** `document` / `asset` / ... */
  entity_type: string;
  entity_id: string;
  /** `expiry_date` / `responsible_user_id` / ... — только для `*.updated.v1`. */
  field: string | null;
  /** Сырое значение из payload (строка, число, объект — зависит от поля). */
  before: unknown;
  after: unknown;
  /** Человекочитаемое значение для полей с FK (asset_id/type_code/responsible_user_id/attachments). */
  before_display: string | null;
  after_display: string | null;
}

// ── URL / filter state ─────────────────────────────────────────────────────────

export interface FilterState {
  /** Global search query; minimum 2 chars for pg_trgm */
  q: string | undefined;
  type_code: string | undefined;
  /** v1.25.5 — multi-select urgency status (was single ComputedStatus | undefined) */
  status: ComputedStatus[] | undefined;
  asset_id: string | undefined;
  /** true = show archived (soft-deleted) documents */
  show_archived: boolean | undefined;
}

export interface SortState {
  sort: string | undefined;
  dir: "asc" | "desc" | undefined;
}

export type ColumnVisibilityMap = Record<string, boolean>;

// ── API list response shapes ────────────────────────────────────────────────────

export interface PaginatedDocuments {
  items: Document[];
  total: number;
  page: number;
  page_size: number;
}

export interface PaginatedAssets {
  items: Asset[];
  total: number;
}

export interface BulkArchiveResult {
  archived: number;
  skipped: number;
}

// ── Mutation payloads ──────────────────────────────────────────────────────────

export interface CreateDocumentPayload {
  asset_id: string;
  type_code: string;
  /** № документа is optional (issue #18) — backend column is nullable. */
  number: string | null;
  issue_date: string | null;
  expiry_date: string | null;
  responsible_user_id: string | null;
  notes: string | null;
}

export interface PatchDocumentPayload {
  asset_id?: string;
  type_code?: string;
  number?: string;
  issue_date?: string | null;
  expiry_date?: string | null;
  responsible_user_id?: string | null;
  notes?: string | null;
  /**
   * v1.25.0 — Full-dict replacement of custom_field_values.
   * Caller sends the new dictionary in its entirety (NOT a delta).
   * When `type_code` is changed in the same PATCH, the backend prunes
   * orphan keys against the new type's schema before this assignment.
   */
  custom_field_values?: Record<string, string | number | null>;
}

export interface CreateAssetPayload {
  name: string;
  inn: string | null;
  notes: string | null;
}

export interface PatchAssetPayload {
  name?: string;
  inn?: string | null;
  notes?: string | null;
}

export interface CreateDocumentTypePayload {
  code: string;
  display_name: string;
  pre_notice_days: number[];
  notify_in_day: boolean;
  overdue_every_days: number;
}

export interface PatchDocumentTypePayload {
  display_name?: string;
  pre_notice_days?: number[];
  notify_in_day?: boolean;
  overdue_every_days?: number;
}

export interface ExportRequestPayload {
  filter: FilterState;
  sort: SortState;
  visible_columns: string[];
}
