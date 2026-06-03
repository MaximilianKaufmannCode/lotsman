// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Registry feature — typed API wrappers around BFF endpoints.
 *
 * The OpenAPI schema (schema.gen.ts) is currently a stub; backend is
 * producing it in parallel. We use the raw `api` client with typed payloads
 * until the codegen is wired. Once web-bff.yaml lands, replace these wrappers
 * with the generated client calls.
 *
 * Base URL: /api (from VITE_API_BASE_URL). All paths are /v1/...
 */

import { api } from "@/shared/api/client";
import type {
  Asset,
  Attachment,
  AuditEvent,
  BulkArchiveResult,
  CreateAssetPayload,
  CreateDocumentPayload,
  CreateDocumentTypePayload,
  Document,
  DocumentType,
  ExportJob,
  ExportRequestPayload,
  PaginatedAssets,
  PaginatedDocuments,
  PatchAssetPayload,
  PatchDocumentPayload,
  PatchDocumentTypePayload,
} from "./types";

// ── Documents ─────────────────────────────────────────────────────────────────

export interface ListDocumentsParams {
  q?: string;
  // Legacy single-value params (kept for backward compat with old URLs)
  type_code?: string;
  /**
   * Urgency status filter — multi-select since v1.25.5. Repeated query
   * param: `?status=soon&status=overdue`. Single value still accepted.
   */
  status?: string[];
  asset_id?: string;
  show_archived?: boolean;
  sort?: string;
  dir?: "asc" | "desc";
  page?: number;
  page_size?: number;
  // New multi-value filter params (v1.23.0)
  /** Multiple asset UUIDs — backend param name: asset_ids (repeated) */
  asset_ids?: string[];
  /** Multiple type codes — backend param name: type_codes (repeated) */
  type_codes?: string[];
  /**
   * Responsible user filter:
   * - 'me'          → resolved server-side to the caller's user_id
   * - 'unassigned'  → responsible_is_null=true
   * - UUID string   → responsible_user_ids=[uuid]
   */
  responsible?: "me" | "unassigned" | string;
  expiry_from?: string; // ISO date YYYY-MM-DD
  expiry_to?: string;
  expiry_null?: boolean; // true = expiry_date IS NULL
  updated_from?: string; // ISO datetime
  updated_to?: string;
  /** Physical document status: 'active' | 'archived' (may be multiple) */
  doc_status?: string[];
  /** Sidebar "Юрисдикция" → maps to cf_yurisdikciya on the backend. */
  jurisdiction?: string[];
  /** Sidebar substring "ИНН содержит". Not yet routed to backend (V1 ignored). */
  inn?: string;
  /** Sidebar "№ содержит" → maps to q (pg_trgm search). */
  number?: string;
  /**
   * v1.25.6 — «— Не задано» tick from the № документа column funnel.
   * Backend filters `WHERE number IS NULL OR number = ''`. Distinct from
   * `number=` (substring search via q=) so the two can combine.
   */
  number_is_null?: boolean;
  /** Sidebar "Только бессрочные" → backend expiry_null=true. */
  expiry_perpetual?: boolean;
  /** v1.24.9 — multi-select из воронки «Действ. до». ISO-даты + сентинел __NULL__. */
  expiry_dates?: string[];
  /** Dynamic custom-field filters: cf_jurisdiction=RU etc. */
  custom_fields?: Record<string, string>;
  /** v1.24.17 — date-range filters for any cf-date field.
   *  Each entry → cf_<key>_from, cf_<key>_to, cf_<key>_is_null params. */
  custom_field_ranges?: Record<
    string,
    { from?: string | undefined; to?: string | undefined; isNull?: boolean | undefined }
  >;
}

/**
 * Build query string for listDocuments.
 * Array params use repeated keys: ?asset_ids=a&asset_ids=b
 * Custom fields use cf_ prefix: ?cf_jurisdiction=RU
 */
function buildDocumentsQuery(params: ListDocumentsParams): string {
  const sp = new URLSearchParams();

  // Scalar params
  const scalar: Record<string, string | boolean | number | undefined> = {
    q: params.q,
    type_code: params.type_code,
    asset_id: params.asset_id,
    show_archived: params.show_archived,
    sort: params.sort,
    dir: params.dir,
    expiry_from: params.expiry_from,
    expiry_to: params.expiry_to,
    expiry_null: params.expiry_null,
    updated_from: params.updated_from,
    updated_to: params.updated_to,
  };
  for (const [k, v] of Object.entries(scalar)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) {
      sp.append(k, String(v));
    }
  }

  // Pagination
  if (params.page !== undefined)
    sp.set("offset", String((params.page - 1) * (params.page_size ?? 100)));
  if (params.page_size !== undefined) sp.set("limit", String(params.page_size));

  // Repeated array params
  for (const id of params.asset_ids ?? []) sp.append("asset_ids", id);
  for (const code of params.type_codes ?? []) sp.append("type_codes", code);
  for (const s of params.doc_status ?? []) sp.append("doc_status", s);
  // v1.25.5 — urgency status as repeated `status=`
  for (const s of params.status ?? []) sp.append("status", s);

  // v1.24.9 — multi-select из воронки «Действ. до» → repeated expiry_dates.
  for (const d of params.expiry_dates ?? []) sp.append("expiry_dates", d);

  // v1.24.4 — asset lifecycle sidebar removed; column-header funnel on
  // «Активность» (cf_aktivnost) is the single point of entry. Backend
  // asset_status query-param still accepted for future external callers.

  // v1.24.2 — perpetual (no expiry) toggle. URL state uses expiry_perpetual;
  // backend reads it as expiry_null (FastAPI alias on expiry_is_null).
  if (params.expiry_perpetual) sp.set("expiry_null", "true");

  // v1.24.2 — sidebar "Юрисдикция" maps to the production custom-field
  // key cf_yurisdikciya. Note: sidebar's hardcoded RU/KZ/... codes may not
  // match real text values stored on documents — this only wires the
  // pipeline; data alignment is a follow-up (use column-header filter on
  // "Юрисдикция" for live distinct values).
  for (const j of params.jurisdiction ?? []) sp.append("cf_yurisdikciya", j);

  // v1.24.2 — sidebar "№ содержит" uses the existing global q search
  // (pg_trgm on documents.number). If global search is already in use,
  // sidebar number takes precedence.
  if (params.number && params.number.length >= 2) {
    sp.set("q", params.number);
  }
  // v1.25.6 — «— Не задано» tick from the № документа column funnel
  // maps to dedicated `number_is_null=true` backend param.
  if (params.number_is_null) {
    sp.set("number_is_null", "true");
  }

  // Responsible filter — translate UI value to backend params
  if (params.responsible === "unassigned") {
    sp.set("responsible_is_null", "true");
  } else if (params.responsible === "me") {
    sp.set("responsible_user_ids", "me");
  } else if (params.responsible) {
    sp.append("responsible_user_ids", params.responsible);
  }

  // Custom fields (equality / containment)
  for (const [key, val] of Object.entries(params.custom_fields ?? {})) {
    if (val) sp.set(`cf_${key}`, val);
  }

  // v1.24.17 — Custom-field date ranges → cf_<key>_from / _to / _is_null.
  for (const [key, range] of Object.entries(params.custom_field_ranges ?? {})) {
    if (!range) continue;
    if (range.from) sp.set(`cf_${key}_from`, range.from);
    if (range.to) sp.set(`cf_${key}_to`, range.to);
    if (range.isNull) sp.set(`cf_${key}_is_null`, "true");
  }

  const str = sp.toString();
  return str ? `?${str}` : "";
}

export async function listDocuments(params: ListDocumentsParams): Promise<PaginatedDocuments> {
  const query = buildDocumentsQuery(params);
  const res = await fetch(`/api/v1/documents${query}`, {
    headers: authHeaders(),
  });
  await assertOk(res);
  const data = (await res.json()) as Document[] | PaginatedDocuments;
  if (Array.isArray(data)) {
    return {
      items: data,
      total: data.length,
      page: params.page ?? 1,
      page_size: params.page_size ?? 100,
    } as PaginatedDocuments;
  }
  return data;
}

/**
 * PATCH /api/v1/assets/{id}/status
 * Change asset activity status. Admin/editor only.
 */
export async function patchAssetStatus(
  assetId: string,
  status: "active" | "liquidating" | "archived",
): Promise<Asset> {
  const res = await fetch(`/api/v1/assets/${assetId}/status`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  await assertOk(res);
  return res.json() as Promise<Asset>;
}

export async function getDocument(id: string): Promise<Document> {
  const res = await fetch(`/api/v1/documents/${id}`, { headers: authHeaders() });
  await assertOk(res);
  return res.json() as Promise<Document>;
}

export async function createDocument(payload: CreateDocumentPayload): Promise<Document> {
  const res = await fetch("/api/v1/documents", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<Document>;
}

export async function patchDocument(id: string, payload: PatchDocumentPayload): Promise<Document> {
  const res = await fetch(`/api/v1/documents/${id}`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<Document>;
}

export async function archiveDocument(id: string): Promise<void> {
  const res = await fetch(`/api/v1/documents/${id}/archive`, {
    method: "PATCH",
    headers: authHeaders(),
  });
  await assertOk(res);
}

export async function restoreDocument(id: string): Promise<Document> {
  const res = await fetch(`/api/v1/documents/${id}/restore`, {
    method: "PATCH",
    headers: authHeaders(),
  });
  await assertOk(res);
  return res.json() as Promise<Document>;
}

export async function bulkArchiveDocuments(ids: string[]): Promise<BulkArchiveResult> {
  const res = await fetch("/api/v1/documents/bulk-archive", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  await assertOk(res);
  return res.json() as Promise<BulkArchiveResult>;
}

// ── Attachments ───────────────────────────────────────────────────────────────

export async function listAttachments(documentId: string): Promise<Attachment[]> {
  const res = await fetch(`/api/v1/documents/${documentId}/attachments`, {
    headers: authHeaders(),
  });
  await assertOk(res);
  return res.json() as Promise<Attachment[]>;
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

/**
 * Upload an attachment using XHR for progress events.
 * TanStack Query's fetch() wrapper doesn't expose upload progress.
 */
export function uploadAttachment(
  documentId: string,
  file: File,
  onProgress?: (p: UploadProgress) => void,
): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const token = getToken();

    xhr.open("POST", `/api/v1/documents/${documentId}/attachments`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("X-Request-Id", makeRequestId());

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          onProgress({ loaded: e.loaded, total: e.total });
        }
      });
    }

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Attachment);
        } catch {
          reject(new ApiError("Invalid JSON in upload response", xhr.status));
        }
      } else {
        const detail = tryParseDetail(xhr.responseText);
        reject(new ApiError(detail ?? `Upload failed: ${xhr.status}`, xhr.status));
      }
    });

    xhr.addEventListener("error", () => {
      reject(new ApiError("Network error during upload", 0));
    });

    xhr.addEventListener("abort", () => {
      reject(new ApiError("Upload aborted", 0));
    });

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export async function downloadAttachment(attachmentId: string): Promise<string> {
  // Returns the signed redirect URL (BFF returns 302; we follow and return the final URL).
  const res = await fetch(`/api/v1/attachments/${attachmentId}/download`, {
    headers: authHeaders(),
    redirect: "follow",
  });
  await assertOk(res);
  // The BFF redirects to the signed URL; after following, the URL is the download URL.
  return res.url;
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const res = await fetch(`/api/v1/attachments/${attachmentId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await assertOk(res);
}

// ── Assets ────────────────────────────────────────────────────────────────────

export interface ListAssetsParams {
  q?: string;
  show_archived?: boolean;
  page?: number;
  page_size?: number;
}

export async function listAssets(params?: ListAssetsParams): Promise<PaginatedAssets> {
  const query = buildQuery((params ?? {}) as Record<string, unknown>);
  const res = await fetch(`/api/v1/assets${query}`, { headers: authHeaders() });
  await assertOk(res);
  const data = (await res.json()) as Asset[] | PaginatedAssets;
  if (Array.isArray(data)) {
    return { items: data, total: data.length } as PaginatedAssets;
  }
  return data;
}

export async function getAsset(id: string): Promise<Asset> {
  const res = await fetch(`/api/v1/assets/${id}`, { headers: authHeaders() });
  await assertOk(res);
  return res.json() as Promise<Asset>;
}

export async function createAsset(payload: CreateAssetPayload): Promise<Asset> {
  const res = await fetch("/api/v1/assets", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<Asset>;
}

export async function patchAsset(id: string, payload: PatchAssetPayload): Promise<Asset> {
  const res = await fetch(`/api/v1/assets/${id}`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<Asset>;
}

export async function archiveAsset(id: string): Promise<void> {
  const res = await fetch(`/api/v1/assets/${id}/archive`, {
    method: "PATCH",
    headers: authHeaders(),
  });
  await assertOk(res);
}

// ── Document types ────────────────────────────────────────────────────────────

export async function listDocumentTypes(): Promise<DocumentType[]> {
  const res = await fetch("/api/v1/document-types", { headers: authHeaders() });
  await assertOk(res);
  return res.json() as Promise<DocumentType[]>;
}

export async function createDocumentType(
  payload: CreateDocumentTypePayload,
): Promise<DocumentType> {
  const res = await fetch("/api/v1/document-types", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<DocumentType>;
}

export async function patchDocumentType(
  code: string,
  payload: PatchDocumentTypePayload,
): Promise<DocumentType> {
  const res = await fetch(`/api/v1/document-types/${code}`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<DocumentType>;
}

// ── Exports ───────────────────────────────────────────────────────────────────

export async function requestExportJob(payload: ExportRequestPayload): Promise<ExportJob> {
  const res = await fetch("/api/v1/exports/xlsx", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await assertOk(res);
  return res.json() as Promise<ExportJob>;
}

export async function getExportJob(jobId: string): Promise<ExportJob> {
  const res = await fetch(`/api/v1/exports/${jobId}`, { headers: authHeaders() });
  await assertOk(res);
  return res.json() as Promise<ExportJob>;
}

export async function downloadExportJob(jobId: string): Promise<string> {
  const res = await fetch(`/api/v1/exports/${jobId}/download`, {
    headers: authHeaders(),
    redirect: "follow",
  });
  await assertOk(res);
  return res.url;
}

export interface ImportXlsxResponse {
  ok: boolean;
  filename?: string;
  summary?: {
    total_rows: number;
    assets_created: number;
    assets_reused: number;
    types_created: number;
    documents_created: number;
    documents_updated: number;
    skipped: number;
    errors_count: number;
  };
  errors?: { row: number; company: string; document: string; error: string }[];
  error?: string;
}

export async function importXlsx(file: File): Promise<ImportXlsxResponse> {
  const fd = new FormData();
  fd.append("file", file);
  // FormData sets its own multipart boundary — do NOT set Content-Type manually
  const headers = authHeaders();
  delete headers["Content-Type"];
  const res = await fetch("/api/v1/imports/xlsx", {
    method: "POST",
    headers,
    credentials: "include",
    body: fd,
  });
  const data = (await res.json().catch(() => ({}))) as ImportXlsxResponse;
  if (!res.ok && data.ok !== false) {
    return { ok: false, error: `HTTP ${res.status}` };
  }
  return data;
}

export async function listExportJobs(): Promise<ExportJob[]> {
  const res = await fetch("/api/v1/exports", { headers: authHeaders() });
  // Backend currently has no GET /exports list endpoint (only POST + GET by id).
  // Return empty list gracefully instead of breaking the registry page.
  if (res.status === 405 || res.status === 404) {
    return [];
  }
  await assertOk(res);
  return res.json() as Promise<ExportJob[]>;
}

// ── Distinct values (v1.24.0) ─────────────────────────────────────────────────

export interface DistinctValueItem {
  value: string;
  count: number;
}

export interface DistinctValuesResponse {
  field: string;
  values: DistinctValueItem[];
  total_distinct: number;
  truncated: boolean;
  /** v1.24.6 — count of docs where cf_<key> is missing or empty.
   *  FE renders extra «Не задано (N)» checkbox when > 0. */
  null_count: number;
}

export interface ListDistinctValuesParams {
  field: string;
  q?: string;
  limit?: number;
}

export async function listDistinctValues(
  params: ListDistinctValuesParams,
): Promise<DistinctValuesResponse> {
  const sp = new URLSearchParams({ field: params.field });
  if (params.q) sp.set("q", params.q);
  if (params.limit !== undefined) sp.set("limit", String(params.limit));
  const res = await fetch(`/api/v1/documents/distinct-values?${sp.toString()}`, {
    headers: authHeaders(),
  });
  await assertOk(res);
  // Backend returns {values, total_distinct, null_count?}; field/truncated may be
  // absent on older versions.
  const data = (await res.json()) as Omit<
    DistinctValuesResponse,
    "field" | "truncated" | "null_count"
  > &
    Partial<Pick<DistinctValuesResponse, "field" | "truncated" | "null_count">>;
  return {
    field: data.field ?? params.field,
    values: data.values,
    total_distinct: data.total_distinct,
    truncated: data.truncated ?? data.values.length < data.total_distinct,
    null_count: data.null_count ?? 0,
  };
}

// ── Audit history ─────────────────────────────────────────────────────────────

export async function getDocumentHistory(documentId: string, limit = 50): Promise<AuditEvent[]> {
  // Specialised endpoint: registry-svc enriches assets / doc-types,
  // web-bff resolves actor_id + responsible_user_id UUIDs to ФИО.
  const res = await fetch(`/api/v1/documents/${documentId}/history?limit=${limit}`, {
    headers: authHeaders(),
  });
  await assertOk(res);
  return res.json() as Promise<AuditEvent[]>;
}

export async function getAssetHistory(assetId: string, limit = 50): Promise<AuditEvent[]> {
  const res = await fetch(`/api/v1/assets/${assetId}/history?limit=${limit}`, {
    headers: authHeaders(),
  });
  await assertOk(res);
  return res.json() as Promise<AuditEvent[]>;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Token accessor — set by AuthProvider via registerClientTokenAccessor
let _getToken: (() => string | null) | null = null;

export function registerRegistryTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

function getToken(): string | null {
  return _getToken?.() ?? null;
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  const headers: Record<string, string> = {
    "X-Request-Id": makeRequestId(),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
}

function buildQuery(params: Record<string, unknown>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  }
  const str = searchParams.toString();
  return str ? `?${str}` : "";
}

async function assertOk(res: Response): Promise<void> {
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = (await res.clone().json()) as { detail?: string };
      detail = body.detail;
    } catch {
      // non-JSON body
    }
    throw new ApiError(detail ?? `HTTP ${res.status}`, res.status);
  }
}

function tryParseDetail(text: string): string | null {
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    return parsed.detail ?? null;
  } catch {
    return null;
  }
}

// Re-export api client for any callers that need the typed openapi-fetch instance
export { api };
