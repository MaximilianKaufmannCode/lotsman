// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Background refresh scheduler.
 *
 * Calls `refresh()` ~14 minutes after the last token was issued
 * (access JWTs expire in 15 min per ADR-0003 §7).
 *
 * Uses the BroadcastChannel leader-election (inside `refresh()`) so only
 * one tab actually POSTs to /api/v1/auth/refresh.
 *
 * Usage: call `startRefreshScheduler` once from AuthProvider.
 */

import { REFRESH_BEFORE_EXPIRY_MS } from "./constants";

export interface RefreshSchedulerOptions {
  /** Returns the current access token expiry in ms (from JWT exp claim * 1000). */
  getExpiresAt: () => number | null;
  /** Performs the actual refresh + leader election. */
  doRefresh: () => Promise<string | null>;
}

export class RefreshScheduler {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly getExpiresAt: () => number | null;
  private readonly doRefresh: () => Promise<string | null>;

  constructor(opts: RefreshSchedulerOptions) {
    this.getExpiresAt = opts.getExpiresAt;
    this.doRefresh = opts.doRefresh;
  }

  /** Schedule the next refresh based on current token expiry. */
  schedule(): void {
    this.cancel();

    const expiresAt = this.getExpiresAt();
    if (!expiresAt) return;

    const now = Date.now();
    const delay = Math.max(0, expiresAt - now - REFRESH_BEFORE_EXPIRY_MS);

    this.timer = setTimeout(() => {
      this.doRefresh()
        .then(() => {
          // Re-schedule after successful refresh
          this.schedule();
        })
        .catch(() => {
          // Refresh failed — AuthProvider will handle state transition
        });
    }, delay);
  }

  cancel(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
