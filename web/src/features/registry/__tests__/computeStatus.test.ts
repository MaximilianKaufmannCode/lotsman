// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { describe, expect, it } from "vitest";
import { computeStatus } from "../computeStatus";

// Fixed anchor date: 2026-05-07 UTC
const TODAY = new Date("2026-05-07T00:00:00.000Z");

describe("computeStatus", () => {
  // ── Priority: archived ────────────────────────────────────────────────────────

  it("returns 'archived' when deleted_at is set, regardless of expiry_date", () => {
    expect(computeStatus("2026-06-01", "2026-05-01T10:00:00Z", TODAY)).toBe("archived");
    expect(computeStatus(null, "2026-04-30T00:00:00Z", TODAY)).toBe("archived");
    // Even overdue + archived → archived
    expect(computeStatus("2025-01-01", "2026-05-06T00:00:00Z", TODAY)).toBe("archived");
  });

  // ── No expiry → ok ────────────────────────────────────────────────────────────

  it("returns 'ok' when expiry_date is null and not archived", () => {
    expect(computeStatus(null, null, TODAY)).toBe("ok");
  });

  // ── ok: >30 days out ─────────────────────────────────────────────────────────

  it("returns 'ok' when expiry_date is 31 days from today", () => {
    // TODAY + 31 = 2026-06-07
    expect(computeStatus("2026-06-07", null, TODAY)).toBe("ok");
  });

  it("returns 'ok' when expiry_date is far in the future", () => {
    expect(computeStatus("2030-01-01", null, TODAY)).toBe("ok");
  });

  // ── soon: 0–30 days (inclusive boundary) ─────────────────────────────────────

  it("returns 'soon' when expiry_date is exactly 30 days from today", () => {
    // TODAY + 30 = 2026-06-06
    expect(computeStatus("2026-06-06", null, TODAY)).toBe("soon");
  });

  it("returns 'soon' when expiry_date is 15 days from today", () => {
    // TODAY + 15 = 2026-05-22
    expect(computeStatus("2026-05-22", null, TODAY)).toBe("soon");
  });

  it("returns 'soon' when expiry_date is exactly today (in-day boundary)", () => {
    expect(computeStatus("2026-05-07", null, TODAY)).toBe("soon");
  });

  it("returns 'soon' when expiry_date is 1 day from today", () => {
    // TODAY + 1 = 2026-05-08
    expect(computeStatus("2026-05-08", null, TODAY)).toBe("soon");
  });

  // ── overdue: expiry_date < today ──────────────────────────────────────────────

  it("returns 'overdue' when expiry_date was yesterday", () => {
    // TODAY - 1 = 2026-05-06
    expect(computeStatus("2026-05-06", null, TODAY)).toBe("overdue");
  });

  it("returns 'overdue' when expiry_date is far in the past", () => {
    expect(computeStatus("2020-01-01", null, TODAY)).toBe("overdue");
  });

  // ── 30-day boundary precision ─────────────────────────────────────────────────

  it("transitions from ok→soon exactly at the 30-day boundary", () => {
    const d31 = "2026-06-07"; // 31 days → ok
    const d30 = "2026-06-06"; // 30 days → soon
    expect(computeStatus(d31, null, TODAY)).toBe("ok");
    expect(computeStatus(d30, null, TODAY)).toBe("soon");
  });

  it("transitions from soon→overdue at the today boundary", () => {
    const inDay = "2026-05-07"; // today → soon
    const yesterday = "2026-05-06"; // yesterday → overdue
    expect(computeStatus(inDay, null, TODAY)).toBe("soon");
    expect(computeStatus(yesterday, null, TODAY)).toBe("overdue");
  });

  // ── Defaults to real today ────────────────────────────────────────────────────

  it("uses real Date() when no today argument is provided (smoke test)", () => {
    const result = computeStatus("2099-01-01", null);
    expect(result).toBe("ok");
  });
});
