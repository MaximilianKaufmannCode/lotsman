// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Admin custom fields API — typed wrappers for:
 *   GET  /api/v1/admin/document-types/{type_code}/custom-fields
 *   PUT  /api/v1/admin/document-types/{type_code}/custom-fields  (re-MFA)
 *   POST /api/v1/admin/import/preview
 *   POST /api/v1/admin/import/confirm                            (re-MFA)
 *
 * Phase 3 of the flexible-document-fields feature (US-6, US-7, US-8).
 *
 * IMPORTANT: registerDocTypeFieldsTokenAccessor MUST be called in
 * web/src/app/providers.tsx inside InterceptorWiring.  Omitting it causes
 * every PUT to 401 (recurring bug from prior sprints).
 */

// ── Domain types ──────────────────────────────────────────────────────────────

export type FieldType = "text" | "number" | "date" | "enum";

export interface CustomField {
  key: string; // [a-z][a-z0-9_]{0,63}
  display_name: string; // 1..100
  type: FieldType;
  required: boolean;
  options: string[] | null; // required+non-empty for enum, null for others
}

// ── Import preview / confirm ───────────────────────────────────────────────────

export interface KnownColumn {
  header: string;
  mapped_to: string; // internal field name
}

export interface UnknownColumn {
  header: string;
  sample_values: string[];
  suggested_type: FieldType;
}

export interface ImportPreviewResponse {
  import_session_id: string;
  rows_total: number;
  known_columns: KnownColumn[];
  unknown_columns: UnknownColumn[];
}

export type ImportDecision =
  | {
      header: string;
      action: "create_new";
      new_key: string;
      target_type: string;
      field_type: FieldType;
      display_name?: string;
      options?: string[];
      apply_to_all_types?: boolean;
    }
  | {
      header: string;
      action: "rename";
      new_key: string;
      target_type: string;
      field_type: FieldType;
      display_name?: string;
      options?: string[];
      apply_to_all_types?: boolean;
    }
  | {
      header: string;
      action: "map_to_existing";
      mapped_to_field: string;
      target_type: string;
    }
  | {
      header: string;
      action: "skip";
    };

export interface ImportRowError {
  row_index: number;
  error: string;
}

export interface ImportConfirmResponse {
  rows_imported: number;
  rows_failed: number;
  fields_added: number;
  errors?: ImportRowError[];
}

// ── Error types ───────────────────────────────────────────────────────────────

export type CustomFieldErrorCode =
  | "REMFA_REQUIRED"
  | "REMFA_REPLAY"
  | "CUSTOM_FIELD_VALIDATION"
  | "SESSION_EXPIRED";

export interface CustomFieldApiError {
  detail: string;
  code?: CustomFieldErrorCode;
}

export class CustomFieldApiResponseError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code?: CustomFieldErrorCode,
    /** Parsed validation details for per-field 422 errors */
    public readonly validationErrors?: Array<{ loc: (string | number)[]; msg: string }>,
  ) {
    super(detail);
    this.name = "CustomFieldApiResponseError";
  }
}

// ── Token accessor (same pattern as channels/api.ts) ──────────────────────────

let _getToken: (() => string | null) | null = null;

export function registerDocTypeFieldsTokenAccessor(fn: () => string | null): void {
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

function authHeadersNoContentType(): Record<string, string> {
  const token = _getToken?.() ?? null;
  const h: Record<string, string> = {
    "X-Request-Id": makeRequestId(),
  };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

interface ValidationError {
  loc: (string | number)[];
  msg: string;
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    ...init,
    headers: {
      ...authHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
  });

  if (!res.ok) {
    let detail = "Неизвестная ошибка";
    let code: CustomFieldErrorCode | undefined;
    let validationErrors: ValidationError[] | undefined;

    try {
      const body = (await res.json()) as CustomFieldApiError | { detail: ValidationError[] };

      if (typeof body.detail === "string") {
        detail = body.detail;
        code = (body as CustomFieldApiError).code;
      } else if (Array.isArray(body.detail)) {
        // 422 FastAPI validation error
        validationErrors = body.detail as ValidationError[];
        detail = "Ошибка валидации полей";
        code = "CUSTOM_FIELD_VALIDATION";
      }
    } catch {
      // non-JSON body — keep defaults
    }

    throw new CustomFieldApiResponseError(res.status, detail, code, validationErrors);
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

// ── Custom field schema ────────────────────────────────────────────────────────

/** GET /api/v1/admin/document-types/{type_code}/custom-fields */
export async function getCustomFieldSchema(typeCode: string): Promise<CustomField[]> {
  const data = await apiFetch<{ schema: CustomField[] }>(
    `/v1/admin/document-types/${encodeURIComponent(typeCode)}/custom-fields`,
  );
  return data.schema;
}

/** PUT /api/v1/admin/document-types/{type_code}/custom-fields */
export async function updateCustomFieldSchema(
  typeCode: string,
  schema: CustomField[],
  totpCode: string,
): Promise<CustomField[]> {
  const data = await apiFetch<{ schema: CustomField[] }>(
    `/v1/admin/document-types/${encodeURIComponent(typeCode)}/custom-fields`,
    {
      method: "PUT",
      body: JSON.stringify({ schema, totp_code: totpCode }),
    },
  );
  return data.schema;
}

// ── Import ────────────────────────────────────────────────────────────────────

/** POST /api/v1/admin/import/preview — multipart form, field name: "file" */
export async function importPreview(file: File): Promise<ImportPreviewResponse> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch("/api/v1/admin/import/preview", {
    method: "POST",
    credentials: "include",
    headers: authHeadersNoContentType(),
    body: form,
  });

  if (!res.ok) {
    let detail = "Неизвестная ошибка";
    let code: CustomFieldErrorCode | undefined;
    try {
      const body = (await res.json()) as CustomFieldApiError;
      if (body.detail) detail = body.detail;
      if (body.code) code = body.code;
    } catch {
      // non-JSON
    }
    throw new CustomFieldApiResponseError(res.status, detail, code);
  }

  return res.json() as Promise<ImportPreviewResponse>;
}

/** POST /api/v1/admin/import/confirm */
export async function importConfirm(
  importSessionId: string,
  decisions: ImportDecision[],
  totpCode: string,
): Promise<ImportConfirmResponse> {
  return apiFetch<ImportConfirmResponse>("/v1/admin/import/confirm", {
    method: "POST",
    body: JSON.stringify({
      import_session_id: importSessionId,
      decisions,
      totp_code: totpCode,
    }),
  });
}
