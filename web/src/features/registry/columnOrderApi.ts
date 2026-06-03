// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tenant-wide column-order preferences (US-N).
 *
 * Read endpoint (GET) is callable by any authenticated user; admins use
 * the PUT endpoint to update the order — UI gates the action by role.
 */

let _getToken: (() => string | null) | null = null;

export function registerColumnOrderTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

export interface ColumnOrderResponse {
  order: string[];
  /** Column id that should be pinned to the left (sticky) in the table.
   * `null` means «use the default» (asset_name). */
  pinned_column_id: string | null;
  updated_at: string | null;
}

export class ColumnOrderApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
    this.name = "ColumnOrderApiError";
  }
}

function authHeaders(): Record<string, string> {
  const token = _getToken?.() ?? null;
  const h: Record<string, string> = {
    "Content-Type": "application/json",
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
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new ColumnOrderApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export async function getColumnOrder(): Promise<ColumnOrderResponse> {
  return apiFetch<ColumnOrderResponse>("/v1/preferences/column-order");
}

export async function updateColumnOrder(
  order: string[],
  pinned_column_id: string | null = null,
): Promise<ColumnOrderResponse> {
  return apiFetch<ColumnOrderResponse>("/v1/admin/preferences/column-order", {
    method: "PUT",
    body: JSON.stringify({ order, pinned_column_id }),
  });
}
