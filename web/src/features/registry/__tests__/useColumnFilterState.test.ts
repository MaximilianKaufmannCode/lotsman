// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for useColumnFilterState pure helper functions.
 *
 * Tests getFieldValue, getFieldValueCount, buildSearchPatch directly
 * (no React hooks needed for the pure functions).
 */

import { describe, expect, it } from "vitest";
import {
  buildSearchPatch,
  getFieldValue,
  getFieldValueCount,
} from "../hooks/useColumnFilterState";
import type { RegistrySearch } from "../hooks/useUrlState";
import { registrySearchSchema } from "../hooks/useUrlState";

const base: RegistrySearch = registrySearchSchema.parse({});

// ── getFieldValue ─────────────────────────────────────────────────────────────

describe("getFieldValue", () => {
  it("returns undefined for unset system field", () => {
    expect(getFieldValue(base, "number")).toBeUndefined();
    expect(getFieldValue(base, "responsible")).toBeUndefined();
    expect(getFieldValue(base, "asset_ids")).toBeUndefined();
  });

  it("returns string for number field", () => {
    const search = { ...base, number: "ДГ-001" };
    expect(getFieldValue(search, "number")).toBe("ДГ-001");
  });

  it("returns string[] for asset_ids", () => {
    const search = { ...base, asset_ids: ["uuid-1", "uuid-2"] };
    const val = getFieldValue(search, "asset_ids");
    expect(Array.isArray(val)).toBe(true);
    expect((val as string[]).length).toBe(2);
  });

  it("returns undefined for empty asset_ids", () => {
    const search = { ...base, asset_ids: [] };
    expect(getFieldValue(search, "asset_ids")).toBeUndefined();
  });

  it("returns composite string for expiry_date range", () => {
    const search = { ...base, expiry_from: "2026-01-01", expiry_to: "2026-12-31" };
    const val = getFieldValue(search, "expiry_date");
    expect(typeof val).toBe("string");
    expect(val as string).toContain("from:2026-01-01");
    expect(val as string).toContain("to:2026-12-31");
  });

  it("returns composite string with null:true for perpetual", () => {
    const search = { ...base, expiry_perpetual: true };
    const val = getFieldValue(search, "expiry_date");
    expect(val as string).toContain("null:true");
  });

  it("returns undefined for expiry_date when no range set", () => {
    expect(getFieldValue(base, "expiry_date")).toBeUndefined();
  });

  it("returns cfFilters value for custom field key", () => {
    const search = { ...base, cfFilters: { yurisdikciya: "RU" } };
    expect(getFieldValue(search, "cfFilters.yurisdikciya")).toBe("RU");
  });

  it("returns undefined for missing cfFilters key", () => {
    const search = { ...base, cfFilters: { other: "X" } };
    expect(getFieldValue(search, "cfFilters.yurisdikciya")).toBeUndefined();
  });

  it("returns undefined for unknown fieldKey", () => {
    expect(getFieldValue(base, "nonexistent_field")).toBeUndefined();
  });

  it("returns responsible string for responsible field", () => {
    const search = { ...base, responsible: "me" };
    expect(getFieldValue(search, "responsible")).toBe("me");
  });

  it("returns composite string for updated_at range", () => {
    const search = {
      ...base,
      updated_from: "2026-01-01T00:00:00",
      updated_to: "2026-06-01T00:00:00",
    };
    const val = getFieldValue(search, "updated_at");
    expect(val as string).toContain("from:");
    expect(val as string).toContain("to:");
  });
});

// ── getFieldValueCount ────────────────────────────────────────────────────────

describe("getFieldValueCount", () => {
  it("returns 0 for undefined value", () => {
    expect(getFieldValueCount(base, "number")).toBe(0);
  });

  it("returns 1 for string value", () => {
    expect(getFieldValueCount({ ...base, number: "ДГ-001" }, "number")).toBe(1);
  });

  it("returns array length for array value", () => {
    expect(
      getFieldValueCount({ ...base, asset_ids: ["a", "b", "c"] }, "asset_ids"),
    ).toBe(3);
  });

  it("returns segment count for composite date string", () => {
    const search = { ...base, expiry_from: "2026-01-01", expiry_to: "2026-12-31" };
    // Composite: "from:..;to:.." → 2 segments
    expect(getFieldValueCount(search, "expiry_date")).toBe(2);
  });

  it("returns 1 for cfFilters single value", () => {
    const search = { ...base, cfFilters: { yurisdikciya: "RU" } };
    expect(getFieldValueCount(search, "cfFilters.yurisdikciya")).toBe(1);
  });
});

// ── buildSearchPatch ──────────────────────────────────────────────────────────

describe("buildSearchPatch", () => {
  it("returns empty patch for unknown fieldKey with undefined value", () => {
    const patch = buildSearchPatch("nonexistent", undefined);
    expect(Object.keys(patch).length).toBe(0);
  });

  it("clears number when value is undefined", () => {
    const patch = buildSearchPatch("number", undefined);
    expect(patch).toMatchObject({ number: undefined });
  });

  it("sets number string value", () => {
    const patch = buildSearchPatch("number", "ДГ-001");
    expect(patch).toMatchObject({ number: "ДГ-001" });
  });

  it("sets asset_ids array from string[]", () => {
    const patch = buildSearchPatch("asset_ids", ["uuid-1", "uuid-2"]);
    expect(patch).toMatchObject({ asset_ids: ["uuid-1", "uuid-2"] });
  });

  it("wraps single string in array for asset_ids", () => {
    const patch = buildSearchPatch("asset_ids", "uuid-1");
    expect(patch).toMatchObject({ asset_ids: ["uuid-1"] });
  });

  it("clears asset_ids when value is undefined", () => {
    const patch = buildSearchPatch("asset_ids", undefined);
    expect(patch).toMatchObject({ asset_ids: undefined });
  });

  it("clears expiry date range when value is undefined", () => {
    const patch = buildSearchPatch("expiry_date", undefined);
    expect(patch).toMatchObject({
      expiry_from: undefined,
      expiry_to: undefined,
      expiry_perpetual: undefined,
    });
  });

  it("clears updated_at range when value is undefined", () => {
    const patch = buildSearchPatch("updated_at", undefined);
    expect(patch).toMatchObject({ updated_from: undefined, updated_to: undefined });
  });

  it("builds cfFilters patch for custom field", () => {
    const patch = buildSearchPatch("cfFilters.yurisdikciya", "RU");
    expect(patch).toMatchObject({ cfFilters: { yurisdikciya: "RU" } });
  });

  it("builds cfFilters patch as CSV for array value", () => {
    const patch = buildSearchPatch("cfFilters.tags", ["A", "B"]);
    expect(patch).toMatchObject({ cfFilters: { tags: "A,B" } });
  });

  it("returns empty cfFilters when value is undefined for custom field", () => {
    const patch = buildSearchPatch("cfFilters.yurisdikciya", undefined);
    expect(patch).toMatchObject({ cfFilters: {} });
  });

  it("sets doc_status array", () => {
    const patch = buildSearchPatch("doc_status", ["active", "archived"]);
    expect(patch).toMatchObject({ doc_status: ["active", "archived"] });
  });

  it("sets responsible string", () => {
    const patch = buildSearchPatch("responsible", "me");
    expect(patch).toMatchObject({ responsible: "me" });
  });

  it("picks first element from array for responsible", () => {
    const patch = buildSearchPatch("responsible", ["me", "other"]);
    expect(patch).toMatchObject({ responsible: "me" });
  });
});
