// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for useUrlState hook — filter schema, migration, and counter.
 *
 * Run:
 *   pnpm vitest run src/features/registry/__tests__/useUrlState.test.tsx
 */

import { describe, expect, it } from "vitest";
import type { RegistrySearch } from "../hooks/useUrlState";
import {
  countActiveFilters,
  migrateLegacySearch,
  registrySearchSchema,
} from "../hooks/useUrlState";

// ---------------------------------------------------------------------------
// Schema tests (no router dependency)
// ---------------------------------------------------------------------------

describe("registrySearchSchema", () => {
  it("accepts valid legacy filter params", () => {
    const result = registrySearchSchema.safeParse({
      q: "Газпром",
      type_code: "contract",
      status: "soon",
      sort: "expiry_date",
      dir: "asc",
      page: 1,
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.q).toBe("Газпром");
      expect(result.data.type_code).toBe("contract");
      // v1.25.5 — single status string still works (backward compat) and
      // resolves to a one-element array.
      expect(result.data.status).toEqual(["soon"]);
    }
  });

  it("filters out unknown status values (v1.25.5 — array, silent drop)", () => {
    // v1.25.5 — status is now a CSV array; unknown enum members are dropped
    // silently rather than rejecting the whole parse (URL bookmarks with a
    // mix of valid + invalid statuses still resolve to the valid subset).
    const result = registrySearchSchema.safeParse({ status: "unknown_status" });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.status).toBeUndefined();
    }
  });

  it("parses status CSV into array (v1.25.5 multi-select)", () => {
    const result = registrySearchSchema.safeParse({ status: "soon,overdue" });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.status).toEqual(["soon", "overdue"]);
    }
  });

  it("rejects invalid dir value", () => {
    const result = registrySearchSchema.safeParse({ dir: "sideways" });
    expect(result.success).toBe(false);
  });

  it("coerces page to number", () => {
    const result = registrySearchSchema.safeParse({ page: "3" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.page).toBe(3);
  });

  it("rejects page below minimum (must be >= 1)", () => {
    const result = registrySearchSchema.safeParse({ page: 0 });
    expect(result.success).toBe(false);
  });

  it("defaults page to 1 when not provided", () => {
    const result = registrySearchSchema.safeParse({});
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.page).toBe(1);
  });

  it("accepts SQL-special chars in q without transformation", () => {
    const result = registrySearchSchema.safeParse({ q: "O'Brien; DROP TABLE --" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.q).toBe("O'Brien; DROP TABLE --");
  });

  it("preserves sort and dir when q is also set", () => {
    const result = registrySearchSchema.safeParse({
      q: "Газпром",
      sort: "expiry_date",
      dir: "asc",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.sort).toBe("expiry_date");
      expect(result.data.dir).toBe("asc");
    }
  });

  it("coerces show_archived string 'true' to boolean", () => {
    const result = registrySearchSchema.safeParse({ show_archived: "true" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.show_archived).toBe(true);
  });

  it("coerces show_archived string 'false' to false", () => {
    const result = registrySearchSchema.safeParse({ show_archived: "false" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.show_archived).toBe(false);
  });

  // v1.23.0 new fields
  it("parses CSV type_codes", () => {
    const result = registrySearchSchema.safeParse({ type_codes: "contract,license" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.type_codes).toEqual(["contract", "license"]);
  });

  it("parses CSV asset_ids", () => {
    const id1 = "550e8400-e29b-41d4-a716-446655440000";
    const id2 = "550e8400-e29b-41d4-a716-446655440001";
    const result = registrySearchSchema.safeParse({ asset_ids: `${id1},${id2}` });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.asset_ids).toEqual([id1, id2]);
  });

  it("parses CSV jurisdiction", () => {
    const result = registrySearchSchema.safeParse({ jurisdiction: "RU,KZ" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.jurisdiction).toEqual(["RU", "KZ"]);
  });

  it("parses boolean expiry_perpetual from string 'true'", () => {
    const result = registrySearchSchema.safeParse({ expiry_perpetual: "true" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.expiry_perpetual).toBe(true);
  });

  it("parses date strings for expiry_from / expiry_to", () => {
    const result = registrySearchSchema.safeParse({
      expiry_from: "2026-01-01",
      expiry_to: "2026-12-31",
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.expiry_from).toBe("2026-01-01");
      expect(result.data.expiry_to).toBe("2026-12-31");
    }
  });

  it("accepts responsible=me", () => {
    const result = registrySearchSchema.safeParse({ responsible: "me" });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.responsible).toBe("me");
  });

  it("round-trips full v1.23.0 filter state", () => {
    const input = {
      q: "Газпром",
      type_codes: "contract,license",
      responsible: "me",
      expiry_from: "2026-01-01",
      expiry_to: "2026-12-31",
      jurisdiction: "RU,KZ",
      doc_status: "active,archived",
      page: 2,
    };
    const result = registrySearchSchema.safeParse(input);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.type_codes).toEqual(["contract", "license"]);
      expect(result.data.jurisdiction).toEqual(["RU", "KZ"]);
      expect(result.data.doc_status).toEqual(["active", "archived"]);
    }
  });
});

// ---------------------------------------------------------------------------
// Migration tests
// ---------------------------------------------------------------------------

describe("migrateLegacySearch", () => {
  it("migrates show_archived=true to doc_status=active,archived", () => {
    const input = registrySearchSchema.parse({ show_archived: "true" });
    const result = migrateLegacySearch(input);
    expect(result.doc_status).toEqual(["active", "archived"]);
    expect(result.show_archived).toBeUndefined();
  });

  it("does not override existing doc_status when show_archived=true", () => {
    const input = registrySearchSchema.parse({
      show_archived: "true",
      doc_status: "archived",
    });
    const result = migrateLegacySearch(input);
    // doc_status was already set — preserve it
    expect(result.doc_status).toEqual(["archived"]);
  });

  it("migrates single asset_id to asset_ids array", () => {
    const id = "550e8400-e29b-41d4-a716-446655440000";
    const input = registrySearchSchema.parse({ asset_id: id });
    const result = migrateLegacySearch(input);
    expect(result.asset_ids).toEqual([id]);
    expect(result.asset_id).toBeUndefined();
  });

  it("does not migrate asset_id when asset_ids already set", () => {
    const id1 = "550e8400-e29b-41d4-a716-446655440000";
    const id2 = "550e8400-e29b-41d4-a716-446655440001";
    const input = registrySearchSchema.parse({ asset_id: id1, asset_ids: id2 });
    const result = migrateLegacySearch(input);
    expect(result.asset_ids).toEqual([id2]); // pre-existing wins
  });

  it("migrates single type_code to type_codes array", () => {
    const input = registrySearchSchema.parse({ type_code: "contract" });
    const result = migrateLegacySearch(input);
    expect(result.type_codes).toEqual(["contract"]);
    expect(result.type_code).toBeUndefined();
  });

  it("is idempotent on clean v1.23.0 search", () => {
    const input: RegistrySearch = registrySearchSchema.parse({
      type_codes: "contract,license",
      responsible: "me",
    });
    const once = migrateLegacySearch(input);
    const twice = migrateLegacySearch(once);
    expect(twice).toEqual(once);
  });
});

// ---------------------------------------------------------------------------
// countActiveFilters tests
// ---------------------------------------------------------------------------

describe("countActiveFilters", () => {
  const base: RegistrySearch = registrySearchSchema.parse({});

  it("returns 0 for empty search", () => {
    expect(countActiveFilters(base)).toBe(0);
  });

  it("counts type_codes as 1", () => {
    expect(countActiveFilters({ ...base, type_codes: ["contract"] })).toBe(1);
  });

  it("counts multiple independent filters correctly", () => {
    const s: RegistrySearch = {
      ...base,
      type_codes: ["contract"],
      responsible: "me",
      expiry_to: "2026-12-31",
      jurisdiction: ["RU", "KZ"],
    };
    expect(countActiveFilters(s)).toBe(4);
  });

  it("counts show_archived=true as 1", () => {
    expect(countActiveFilters({ ...base, show_archived: true })).toBe(1);
  });

  it("does NOT count q, sort, dir, or page", () => {
    const s: RegistrySearch = {
      ...base,
      q: "some query",
      sort: "expiry_date",
      dir: "asc",
      page: 5,
    };
    expect(countActiveFilters(s)).toBe(0);
  });

  it("does NOT count empty arrays", () => {
    const s = { ...base, type_codes: [], asset_ids: [] };
    expect(countActiveFilters(s)).toBe(0);
  });

  it("counts expiry_perpetual=true", () => {
    expect(countActiveFilters({ ...base, expiry_perpetual: true })).toBe(1);
  });

  it("does not count expiry_perpetual=false", () => {
    expect(countActiveFilters({ ...base, expiry_perpetual: false })).toBe(0);
  });
});
