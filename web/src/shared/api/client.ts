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
import { recoverFrom401 } from "./interceptor";
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

function enrich(req: Request, token: string | null): Request {
  return new Request(req, {
    headers: {
      ...Object.fromEntries(req.headers.entries()),
      "x-request-id": makeRequestId(),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
}

export const api = createClient<paths>({
  baseUrl,
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
  },
  fetch: async (req) => {
    const token = _getToken?.() ?? null;
    let res = await fetch(enrich(req, token));

    // Transparent recovery from an expired access token (ADR-0003 §7): refresh
    // once and retry. Only idempotent methods are auto-retried — a GET/HEAD has
    // no body to re-stream, so building a second Request from the original is
    // safe. This typed client never serves auth routes, so there is no
    // refresh-loop risk.
    if (res.status === 401) {
      const method = req.method.toUpperCase();
      if (method === "GET" || method === "HEAD") {
        const newToken = await recoverFrom401(token);
        if (newToken) {
          res = await fetch(enrich(req, newToken));
        }
      } else {
        // Non-idempotent request: refresh the token so the user's next attempt
        // succeeds, but never replay a consumed request body.
        await recoverFrom401(token);
      }
    }
    return res;
  },
});
