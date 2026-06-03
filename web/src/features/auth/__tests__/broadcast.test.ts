// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Unit tests for BroadcastCoordinator.
 *
 * BroadcastChannel is not available in jsdom — we mock it.
 *
 * Each coordinator in these tests receives a unique `tabId` override so that
 * the self-filtering logic (`msg.tabId !== this.tabId`) works correctly even
 * though all instances run in the same JS context (shared module-level TAB_ID).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthBroadcastMessage } from "../broadcast";
import { BroadcastCoordinator, CHANNEL_NAME, TAB_ID } from "../broadcast";

// ── BroadcastChannel mock ─────────────────────────────────────────────────────

class MockBroadcastChannel {
  name: string;
  onmessage: ((event: MessageEvent<AuthBroadcastMessage>) => void) | null = null;
  private listeners: Map<string, Set<(event: MessageEvent<AuthBroadcastMessage>) => void>> =
    new Map();
  private static instances: MockBroadcastChannel[] = [];

  constructor(name: string) {
    this.name = name;
    MockBroadcastChannel.instances.push(this);
  }

  postMessage(data: AuthBroadcastMessage): void {
    // Deliver to all OTHER instances with the same channel name
    for (const inst of MockBroadcastChannel.instances) {
      if (inst !== this && inst.name === this.name) {
        const event = new MessageEvent("message", { data });
        inst.onmessage?.(event);
        const listeners = inst.listeners.get("message");
        if (listeners) {
          for (const listener of listeners) {
            listener(event);
          }
        }
      }
    }
  }

  addEventListener(
    type: string,
    listener: (event: MessageEvent<AuthBroadcastMessage>) => void,
  ): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    // biome-ignore lint/style/noNonNullAssertion: set above
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(
    type: string,
    listener: (event: MessageEvent<AuthBroadcastMessage>) => void,
  ): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    const idx = MockBroadcastChannel.instances.indexOf(this);
    if (idx !== -1) MockBroadcastChannel.instances.splice(idx, 1);
  }

  static reset() {
    MockBroadcastChannel.instances = [];
  }

  static getInstances(): MockBroadcastChannel[] {
    return MockBroadcastChannel.instances;
  }
}

beforeEach(() => {
  MockBroadcastChannel.reset();
  // @ts-expect-error -- mock assignment
  globalThis.BroadcastChannel = MockBroadcastChannel;
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("BroadcastCoordinator constants", () => {
  it("uses channel name lotsman-auth", () => {
    expect(CHANNEL_NAME).toBe("lotsman-auth");
  });

  it("TAB_ID is a non-empty string", () => {
    expect(typeof TAB_ID).toBe("string");
    expect(TAB_ID.length).toBeGreaterThan(0);
  });
});

describe("BroadcastCoordinator.broadcastTokenRefreshed", () => {
  it("onTokenRefreshed is called in receiving tab with correct values", () => {
    const receiverCallback = vi.fn();
    const sender = new BroadcastCoordinator({
      tabId: "tab-sender",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });
    const receiver = new BroadcastCoordinator({
      tabId: "tab-receiver",
      onTokenRefreshed: receiverCallback,
      onLoggedOut: vi.fn(),
    });

    sender.open();
    receiver.open();

    const fakeToken = "eyJ.eyJ.sig";
    const fakeExpiry = Date.now() + 900_000;
    sender.broadcastTokenRefreshed(fakeToken, fakeExpiry);

    expect(receiverCallback).toHaveBeenCalledWith(fakeToken, fakeExpiry);
    expect(receiverCallback).toHaveBeenCalledTimes(1);

    sender.close();
    receiver.close();
  });

  it("sender's own onTokenRefreshed is NOT called by its own broadcast", () => {
    const senderCallback = vi.fn();
    const sender = new BroadcastCoordinator({
      tabId: "tab-only-sender",
      onTokenRefreshed: senderCallback,
      onLoggedOut: vi.fn(),
    });
    sender.open();
    sender.broadcastTokenRefreshed("tok", Date.now());
    expect(senderCallback).not.toHaveBeenCalled();
    sender.close();
  });
});

describe("BroadcastCoordinator.broadcastLoggedOut", () => {
  it("onLoggedOut is called in receiving tab", () => {
    const receiverLogout = vi.fn();
    const sender = new BroadcastCoordinator({
      tabId: "tab-logout-sender",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });
    const receiver = new BroadcastCoordinator({
      tabId: "tab-logout-receiver",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: receiverLogout,
    });

    sender.open();
    receiver.open();
    sender.broadcastLoggedOut();

    expect(receiverLogout).toHaveBeenCalledTimes(1);

    sender.close();
    receiver.close();
  });

  it("sender's own onLoggedOut is NOT triggered by its own broadcast", () => {
    const senderLogout = vi.fn();
    const sender = new BroadcastCoordinator({
      tabId: "tab-logout-only",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: senderLogout,
    });
    sender.open();
    sender.broadcastLoggedOut();
    expect(senderLogout).not.toHaveBeenCalled();
    sender.close();
  });
});

describe("BroadcastCoordinator.electLeader", () => {
  it("returns true when no other tab responds", async () => {
    const coord = new BroadcastCoordinator({
      tabId: "tab-leader-solo",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });
    coord.open();
    const isLeader = await coord.electLeader();
    expect(isLeader).toBe(true);
    coord.close();
  });

  it("returns false when a tab with earlier ts responds with pong", async () => {
    // Create a second coordinator that will auto-pong with an earlier ts
    const early = new BroadcastCoordinator({
      tabId: "tab-early",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });
    early.open();

    // Manually simulate early tab sending a pong on election messages
    // by polling its internal channel
    const earlyChannel = MockBroadcastChannel.getInstances().find(
      (inst) => inst.name === CHANNEL_NAME,
    );

    // Monkeypatch: when early receives an election, it sends a pong with ts-1000
    if (earlyChannel) {
      const orig = earlyChannel.onmessage;
      earlyChannel.onmessage = (event: MessageEvent<AuthBroadcastMessage>) => {
        orig?.(event);
        if (event.data.type === "leader-election") {
          // Simulate early tab ponging with an earlier timestamp
          const earlyTs = event.data.ts - 1000;
          for (const inst of MockBroadcastChannel.getInstances()) {
            if (inst !== earlyChannel) {
              const pongData: AuthBroadcastMessage = {
                type: "leader-pong" as const,
                tabId: "tab-early",
                ts: earlyTs,
              };
              const pong = new MessageEvent<AuthBroadcastMessage>("message", {
                data: pongData,
              });
              inst.onmessage?.(pong);
              // Also fire on event listeners
              const listeners = (
                inst as unknown as {
                  listeners: Map<string, Set<(e: MessageEvent<AuthBroadcastMessage>) => void>>;
                }
              ).listeners;
              for (const l of listeners.get("message") ?? []) {
                l(pong);
              }
            }
          }
        }
      };
    }

    const late = new BroadcastCoordinator({
      tabId: "tab-late",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });
    late.open();

    const isLeader = await late.electLeader();
    // Another tab pong'd with an earlier ts — late tab should defer
    expect(isLeader).toBe(false);

    early.close();
    late.close();
  });
});

describe("close() prevents further message handling", () => {
  it("after close(), callbacks are not called", () => {
    const callback = vi.fn();
    const coord = new BroadcastCoordinator({
      tabId: "tab-closed",
      onTokenRefreshed: callback,
      onLoggedOut: vi.fn(),
    });
    const sender = new BroadcastCoordinator({
      tabId: "tab-active-sender",
      onTokenRefreshed: vi.fn(),
      onLoggedOut: vi.fn(),
    });

    coord.open();
    sender.open();
    coord.close(); // close before broadcast

    sender.broadcastTokenRefreshed("tok", Date.now());
    expect(callback).not.toHaveBeenCalled();

    sender.close();
  });
});
