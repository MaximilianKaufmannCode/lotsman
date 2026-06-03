// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * openapi-fetch typed client for non-auth endpoints (registry, notifications, audit).
 *
 * Authorization: Bearer is injected via the token accessor registered by AuthProvider.
 * X-Request-Id is a UUIDv4 per request for distributed tracing (ADR-0002 §C.4).
 *
 * Auth endpoints use the direct fetch wrappers in features/auth/api.ts
 * (they pre-date the OpenAPI schema which backend is producing in parallel).
 */

import createClient from "openapi-fetch";
import type { paths } from "./schema.gen";

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "/api") as string;

/** Injected by AuthProvider — avoids stale closures. */
let _getToken: (() => string | null) | null = null;

export function registerClientTokenAccessor(fn: () => string | null): void {
  _getToken = fn;
}

function makeRequestId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

export const api = createClient<paths>({
  baseUrl,
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
  },
  fetch: async (req) => {
    const requestId = makeRequestId();

    const token = _getToken?.();

    const enriched =
      req instanceof Request
        ? new Request(req, {
            headers: {
              ...Object.fromEntries(req.headers.entries()),
              "x-request-id": requestId,
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
          })
        : req;

    return fetch(enriched);
  },
});
