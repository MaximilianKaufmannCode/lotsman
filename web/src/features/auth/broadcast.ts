// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * BroadcastChannel coordinator for multi-tab token refresh.
 *
 * Per ADR-0003 §11: one leader-elected tab performs the refresh;
 * followers receive the new access token via the channel.
 * This guarantees exactly one refresh in flight — which is why §8
 * reuse-detection stays strict (no grace window).
 *
 * Channel name: 'lotsman-auth'
 * Messages are discriminated unions keyed on `type`.
 */

export const CHANNEL_NAME = "lotsman-auth" as const;

// ── Message types ─────────────────────────────────────────────────────────────

export interface TokenRefreshedMessage {
  type: "token-refreshed";
  accessToken: string;
  /** Unix ms */
  expiresAt: number;
  /** Identifies which tab sent the message */
  tabId: string;
}

export interface LoggedOutMessage {
  type: "logged-out";
  tabId: string;
}

export interface LeaderElectionMessage {
  type: "leader-election";
  tabId: string;
  /** Unix ms — used to pick the "earliest" tab as leader */
  ts: number;
}

export interface LeaderPongMessage {
  type: "leader-pong";
  /** The tab that is asserting leadership */
  tabId: string;
  /** Original ts from the election message */
  ts: number;
}

export type AuthBroadcastMessage =
  | TokenRefreshedMessage
  | LoggedOutMessage
  | LeaderElectionMessage
  | LeaderPongMessage;

// ── Coordinator ───────────────────────────────────────────────────────────────

export type OnTokenRefreshed = (accessToken: string, expiresAt: number) => void;
export type OnLoggedOut = () => void;

/** Stable tab identity for the lifetime of this JS context. */
export const TAB_ID: string =
  typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

interface BroadcastCoordinatorOptions {
  onTokenRefreshed: OnTokenRefreshed;
  onLoggedOut: OnLoggedOut;
  /** Override the tab identity — used in unit tests where all instances share a JS context. */
  tabId?: string | undefined;
}

export class BroadcastCoordinator {
  private channel: BroadcastChannel | null = null;
  private readonly onTokenRefreshed: OnTokenRefreshed;
  private readonly onLoggedOut: OnLoggedOut;
  private readonly tabId: string;

  constructor(opts: BroadcastCoordinatorOptions) {
    this.onTokenRefreshed = opts.onTokenRefreshed;
    this.onLoggedOut = opts.onLoggedOut;
    this.tabId = opts.tabId ?? TAB_ID;
  }

  open(): void {
    if (typeof BroadcastChannel === "undefined") return;
    this.channel = new BroadcastChannel(CHANNEL_NAME);
    this.channel.onmessage = this.handleMessage.bind(this);
  }

  close(): void {
    this.channel?.close();
    this.channel = null;
  }

  private post(msg: AuthBroadcastMessage): void {
    this.channel?.postMessage(msg);
  }

  private handleMessage(event: MessageEvent<AuthBroadcastMessage>): void {
    const msg = event.data;
    switch (msg.type) {
      case "token-refreshed":
        if (msg.tabId !== this.tabId) {
          this.onTokenRefreshed(msg.accessToken, msg.expiresAt);
        }
        break;
      case "logged-out":
        if (msg.tabId !== this.tabId) {
          this.onLoggedOut();
        }
        break;
      case "leader-election":
      case "leader-pong":
        // Handled in electLeader() via event listeners — not handled here.
        break;
    }
  }

  /** Broadcast that a token refresh succeeded. */
  broadcastTokenRefreshed(accessToken: string, expiresAt: number): void {
    this.post({ type: "token-refreshed", accessToken, expiresAt, tabId: this.tabId });
  }

  /** Broadcast that this tab logged out — all other tabs should clear state. */
  broadcastLoggedOut(): void {
    this.post({ type: "logged-out", tabId: this.tabId });
  }

  /**
   * Leader election — resolves true if this tab should perform the refresh,
   * false if another tab is already the leader and will broadcast the result.
   *
   * Protocol:
   * 1. This tab broadcasts leader-election{tabId, ts=now}
   * 2. Wait ELECTION_TIMEOUT_MS for any pong
   * 3. If a pong arrives with an earlier ts → another tab is leader → return false
   * 4. If no pong or pong has later ts → this tab is leader → broadcast pong + return true
   *
   * Other tabs receiving leader-election respond with leader-pong if their own
   * ts is earlier (meaning they were created first and claim priority).
   */
  async electLeader(): Promise<boolean> {
    if (!this.channel) return true; // No channel — always be leader

    const ELECTION_TIMEOUT_MS = 150;
    const myTs = Date.now();

    return new Promise((resolve) => {
      let resolved = false;

      const electionMsg: LeaderElectionMessage = {
        type: "leader-election",
        tabId: this.tabId,
        ts: myTs,
      };

      const onMessage = (event: MessageEvent<AuthBroadcastMessage>) => {
        if (resolved) return;
        const msg = event.data;

        // Another tab responded saying it has an earlier ts — defer to it.
        if (msg.type === "leader-pong" && msg.ts < myTs) {
          resolved = true;
          cleanup();
          resolve(false);
        }

        // If we receive an election from another tab with a later ts,
        // we reply with our own pong to assert our earlier birth.
        if (msg.type === "leader-election" && msg.tabId !== this.tabId && msg.ts > myTs) {
          this.post({ type: "leader-pong", tabId: this.tabId, ts: myTs });
        }
      };

      const timer = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          cleanup();
          // Won the election — broadcast pong so latecomers defer to us.
          this.post({ type: "leader-pong", tabId: this.tabId, ts: myTs });
          resolve(true);
        }
      }, ELECTION_TIMEOUT_MS);

      const cleanup = () => {
        clearTimeout(timer);
        if (this.channel) {
          this.channel.removeEventListener("message", onMessage);
        }
      };

      // channel is guaranteed defined — checked at top of electLeader
      // biome-ignore lint/style/noNonNullAssertion: channel checked above
      this.channel!.addEventListener("message", onMessage);
      this.post(electionMsg);
    });
  }
}
