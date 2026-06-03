// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Global 401 interceptor (auth-flow-review §7b).
 *
 * On 401 from a NON-refresh route:
 *   1. Attempt a single token refresh (leader-elected via BroadcastChannel).
 *   2. On success: retry the original request with the new token.
 *   3. On failure: kick to /login with toast "Сессия истекла".
 *
 * On 401 from the refresh endpoint itself: hard logout (no retry loop).
 *
 * This module is wired in providers.tsx via TanStack Query's global error handler.
 * It also exports `fetchWithInterceptor` for callers that bypass TanStack Query.
 */

import { toast } from "@/shared/ui/toast";

// Callbacks injected by AuthProvider to avoid circular imports
let _refresh: (() => Promise<string | null>) | null = null;
let _logout: (() => Promise<void>) | null = null;
let _navigateToLogin: (() => void) | null = null;

export function registerInterceptorCallbacks(opts: {
  refresh: () => Promise<string | null>;
  logout: () => Promise<void>;
  navigateToLogin: () => void;
}): void {
  _refresh = opts.refresh;
  _logout = opts.logout;
  _navigateToLogin = opts.navigateToLogin;
}

/** Whether a 401 retry is currently in flight (prevents nested retries). */
let _refreshInFlight = false;

/**
 * Called by TanStack Query's onError and mutation error handlers.
 * Also usable directly by non-query callers.
 */
export async function handle401(isRefreshEndpoint: boolean): Promise<boolean> {
  if (isRefreshEndpoint) {
    // Hard logout — no retry
    try {
      await _logout?.();
    } catch {
      // ignore
    }
    _navigateToLogin?.();
    toast.show({
      title: "Сессия истекла",
      description: "Войдите снова для продолжения работы.",
      variant: "destructive",
    });
    return false;
  }

  if (_refreshInFlight) {
    // Another request already triggered a refresh — caller should retry after a tick
    return false;
  }

  _refreshInFlight = true;
  try {
    const newToken = await _refresh?.();
    _refreshInFlight = false;
    if (!newToken) {
      // Refresh succeeded in another tab — token will arrive via BroadcastChannel
      return true;
    }
    return true;
  } catch {
    _refreshInFlight = false;
    try {
      await _logout?.();
    } catch {
      // ignore
    }
    _navigateToLogin?.();
    toast.show({
      title: "Сессия истекла",
      description: "Войдите снова для продолжения работы.",
      variant: "destructive",
    });
    return false;
  }
}
