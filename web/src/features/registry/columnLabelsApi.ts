// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Per-tenant column-label overrides.
 *
 * GET — any authenticated user (everyone sees the same admin-set names).
 * PUT — admin only (server enforces it; UI gates the action by role).
 */

let _getToken: (() => string | null) | null = null;

export function registerColumnLabelsTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

export interface ColumnLabelsResponse {
  /** Map of column id → admin-set display name. Empty when nothing was set. */
  labels: Record<string, string>;
  updated_at: string | null;
}

export class ColumnLabelsApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
    this.name = "ColumnLabelsApiError";
  }
}

function authHeaders(): Record<string, string> {
  const token = _getToken?.() ?? null;
  const h: Record<string, string> = { "Content-Type": "application/json" };
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
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new ColumnLabelsApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export async function getColumnLabels(): Promise<ColumnLabelsResponse> {
  return apiFetch<ColumnLabelsResponse>("/v1/preferences/column-labels");
}

export async function updateColumnLabels(
  labels: Record<string, string>,
): Promise<ColumnLabelsResponse> {
  return apiFetch<ColumnLabelsResponse>("/v1/admin/preferences/column-labels", {
    method: "PUT",
    body: JSON.stringify({ labels }),
  });
}
