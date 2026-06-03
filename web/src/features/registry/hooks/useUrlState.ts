// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useUrlState — bookmarkable filter + sort + pagination state via TanStack Router.
 *
 * URL is the source of truth for filters/sort/page (Iron Rule §3).
 * Zod schema validates and normalises search params.
 *
 * v1.23.0: Extended with multi-criteria filter params.
 * Backward compat: show_archived=true → doc_status=active,archived (migration fn below).
 *
 * Example URL: /registry?q=Газпром&type_codes=contract,license&responsible=me&expiry_to=2026-12-31
 */

import { useLocation } from "@tanstack/react-router";
import * as React from "react";
import { z } from "zod";

// ── CSV helpers ───────────────────────────────────────────────────────────────

/** Parse "a,b,c" → ["a","b","c"]; empty string → [] */
const csvArray = z
  .string()
  .optional()
  .transform((v) => (v ? v.split(",").filter(Boolean) : undefined));

/** Coerce "true"/"false"/boolean to boolean */
const boolParam = z.union([z.boolean(), z.string().transform((v) => v === "true")]).optional();

// ── Zod schema (v1.23.0) ──────────────────────────────────────────────────────

export const registrySearchSchema = z.object({
  // ── Search & pagination ───────────────────────────────────────────────────
  q: z.string().optional(),
  sort: z.string().optional(),
  dir: z.enum(["asc", "desc"]).optional(),
  page: z.coerce.number().int().min(1).optional().default(1),

  // ── Legacy single-value params (kept for backward compat) ────────────────
  /** @deprecated use type_codes instead */
  type_code: z.string().optional(),
  /** @deprecated use asset_ids instead */
  asset_id: z.string().optional(),
  /** @deprecated use doc_status instead */
  show_archived: boolParam,
  /**
   * Urgency status filter — multi-select since v1.25.5.
   * CSV: "soon,overdue" → ["soon","overdue"]. Single value "soon"
   * (legacy URL bookmark) still parses to ["soon"] via csvArray.
   */
  status: z
    .string()
    .optional()
    .transform((v): ("ok" | "soon" | "overdue" | "archived")[] | undefined => {
      if (!v) return undefined;
      const allowed = new Set(["ok", "soon", "overdue", "archived"]);
      const items = v.split(",").filter((s) => allowed.has(s)) as (
        | "ok"
        | "soon"
        | "overdue"
        | "archived"
      )[];
      return items.length > 0 ? items : undefined;
    }),

  // ── Document filters ──────────────────────────────────────────────────────
  /** CSV: "contract,license" → ["contract","license"] */
  type_codes: csvArray,
  /** Physical document status: active/archived */
  doc_status: csvArray,

  // ── Asset (counterparty) filters ──────────────────────────────────────────
  /** CSV of asset UUIDs */
  asset_ids: csvArray,
  /** CSV: "RU,KZ" */
  jurisdiction: csvArray,
  /** INN substring filter */
  inn: z.string().optional(),
  /** Document number substring filter */
  number: z.string().optional(),
  /** v1.25.6 — «— Не задано» voronka tick for № документа (null or empty) */
  number_is_null: boolParam,

  // ── Expiry date filters ───────────────────────────────────────────────────
  expiry_from: z.string().optional(),
  expiry_to: z.string().optional(),
  /** true = only documents with NULL expiry_date */
  expiry_perpetual: boolParam,
  /** v1.24.9 — multi-select из воронки колонки «Действ. до».
   *  CSV ISO-дат и/или сентинел __NULL__. */
  expiry_dates: csvArray,

  // ── Responsible user filter ───────────────────────────────────────────────
  /**
   * 'me' | 'unassigned' | UUID
   * 'me'         → server resolves to caller's user_id
   * 'unassigned' → responsible_is_null=true
   * UUID         → filter by specific user
   */
  responsible: z.string().optional(),

  // ── Metadata filters ──────────────────────────────────────────────────────
  updated_from: z.string().optional(),
  updated_to: z.string().optional(),

  // ── Custom-field filters (v1.24.0) ────────────────────────────────────────
  /**
   * Arbitrary cf_<key> params: each key maps to its value.
   * In URL: cf_yurisdikciya=Гонконг → stored as cfFilters["yurisdikciya"] = "Гонконг"
   *
   * These are passed through raw to the backend as cf_<key>=<value> query params.
   * Multiple values for one key are stored as comma-separated string in the URL.
   * Serialization: cfFilters is NOT a flat Zod field — it lives in the raw URL
   * as cf_<key> params and is parsed from `location.search` manually below.
   */
  cfFilters: z.record(z.string(), z.string()).optional(),

  // ── v1.24.17 — schema-driven date-range filters for custom fields ─────────
  /**
   * For any custom field with type === "date" the popover writes a range here.
   * URL representation: cf_<key>_from, cf_<key>_to, cf_<key>_is_null (parsed
   * out of location.search manually below — same dynamic pattern as cfFilters).
   * Shape: { yurisdikciya: { from?: "2026-09-01", to?: "2026-09-30", isNull?: true } }
   */
  cfDateFilters: z
    .record(
      z.string(),
      z.object({
        from: z.string().optional(),
        to: z.string().optional(),
        isNull: z.boolean().optional(),
      }),
    )
    .optional(),
});

export type RegistrySearch = z.infer<typeof registrySearchSchema>;

// ── Filter param keys (used by FilterChips to enumerate active conditions) ───

export type FilterKey = keyof Omit<RegistrySearch, "q" | "sort" | "dir" | "page">;

/** Regex for valid cf_ key names (mirrors backend _CF_KEY_RE) */
const CF_KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

/**
 * Parse cf_* params from a raw URLSearchParams / location.search string.
 * Returns a record of {key: value} for all cf_* params found.
 */
export function parseCfFiltersFromSearch(search: string | Record<string, string>): Record<string, string> {
  const result: Record<string, string> = {};
  const entries: Array<[string, string]> =
    typeof search === "string"
      ? Array.from(new URLSearchParams(search).entries())
      : Object.entries(search);
  // v1.24.17: skip range-suffix keys (cf_<key>_from / _to / _is_null) — they live
  // in cfDateFilters, not in cfFilters.
  for (const [k, v] of entries) {
    if (!k.startsWith("cf_")) continue;
    const key = k.slice(3);
    if (/^[a-z][a-z0-9_]{0,63}_(from|to|is_null)$/.test(key)) continue;
    if (CF_KEY_RE.test(key) && v) {
      result[key] = v;
    }
  }
  return result;
}

/**
 * v1.24.17 — parse cf_<key>_(from|to|is_null) URL params into a structured map.
 * Used for date-range filters on custom date fields.
 */
export function parseCfDateFiltersFromSearch(
  search: string | Record<string, string>,
): Record<string, { from?: string; to?: string; isNull?: boolean }> {
  const result: Record<string, { from?: string; to?: string; isNull?: boolean }> = {};
  const entries: Array<[string, string]> =
    typeof search === "string"
      ? Array.from(new URLSearchParams(search).entries())
      : Object.entries(search);
  const RANGE_RE = /^([a-z][a-z0-9_]{0,63})_(from|to|is_null)$/;
  for (const [k, v] of entries) {
    if (!k.startsWith("cf_") || !v) continue;
    const m = RANGE_RE.exec(k.slice(3));
    if (!m) continue;
    const key = m[1]!;
    const suffix = m[2]!;
    const bucket = result[key] ?? {};
    if (suffix === "from") bucket.from = v;
    else if (suffix === "to") bucket.to = v;
    else if (suffix === "is_null") bucket.isNull = v.toLowerCase() === "true";
    result[key] = bucket;
  }
  return result;
}

/**
 * Serialize cfDateFilters back to flat URL params: cf_<key>_(from|to|is_null) = value.
 */
export function serializeCfDateFilters(
  cf: Record<
    string,
    { from?: string | undefined; to?: string | undefined; isNull?: boolean | undefined }
  >,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(cf)) {
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(k)) continue;
    if (v.from) out[`cf_${k}_from`] = v.from;
    if (v.to) out[`cf_${k}_to`] = v.to;
    if (v.isNull) out[`cf_${k}_is_null`] = "true";
  }
  return out;
}

/**
 * Serialize cfFilters record back to URL params.
 * Each entry becomes cf_<key>=<value>.
 */
export function serializeCfFilters(cf: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(cf)) {
    if (CF_KEY_RE.test(k) && v) {
      out[`cf_${k}`] = v;
    }
  }
  return out;
}

export const FILTER_KEYS: FilterKey[] = [
  "type_code",
  "type_codes",
  "asset_id",
  "asset_ids",
  "show_archived",
  "status",
  "doc_status",
  "jurisdiction",
  "inn",
  "number",
  "number_is_null",
  "expiry_from",
  "expiry_to",
  "expiry_perpetual",
  "expiry_dates",
  "responsible",
  "updated_from",
  "updated_to",
  "cfFilters",
  "cfDateFilters",
];

/**
 * Count how many filter conditions are active (excluding q/sort/dir/page).
 * Used for the badge counter on the Filters button.
 */
export function countActiveFilters(search: RegistrySearch): number {
  let n = 0;
  for (const key of FILTER_KEYS) {
    if (key === "cfFilters") {
      const cf = search.cfFilters;
      if (cf) {
        for (const v of Object.values(cf)) {
          if (v) n++;
        }
      }
      continue;
    }
    if (key === "cfDateFilters") {
      const cf = search.cfDateFilters;
      if (cf) {
        for (const v of Object.values(cf)) {
          if (v && (v.from || v.to || v.isNull)) n++;
        }
      }
      continue;
    }
    const val = search[key as Exclude<FilterKey, "cfFilters" | "cfDateFilters">];
    if (val === undefined || val === null || val === false || val === "") continue;
    if (Array.isArray(val) && val.length === 0) continue;
    n++;
  }
  return n;
}

// ── Backward-compat URL migration ─────────────────────────────────────────────

/**
 * Migrate legacy URL params to v1.23.0 equivalents.
 * Called once on page load when legacy keys are detected.
 *
 * - show_archived=true → doc_status includes both "active" and "archived"
 * - asset_id=X        → asset_ids=[X]  (if asset_ids not already set)
 * - type_code=X       → type_codes=[X] (if type_codes not already set)
 */
export function migrateLegacySearch(search: RegistrySearch): RegistrySearch {
  const next = { ...search };

  // show_archived=true → doc_status=active,archived
  if (next.show_archived === true && !next.doc_status?.length) {
    next.doc_status = ["active", "archived"];
    next.show_archived = undefined;
  }

  // Legacy single asset_id → asset_ids
  if (next.asset_id && !next.asset_ids?.length) {
    next.asset_ids = [next.asset_id];
    next.asset_id = undefined;
  }

  // Legacy single type_code → type_codes
  if (next.type_code && !next.type_codes?.length) {
    next.type_codes = [next.type_code];
    next.type_code = undefined;
  }

  return next;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export interface UrlStateReturn {
  search: RegistrySearch;
  setSearch: (partial: Partial<RegistrySearch>, resetPage?: boolean) => void;
  resetFilters: () => void;
  activeFilterCount: number;
  /** Remove a single filter key from URL */
  removeFilter: (key: FilterKey) => void;
}

/**
 * Reads URL search params and returns typed state + setters.
 *
 * Array values are serialized as CSV: ["a","b"] → "a,b"
 * Boolean values are serialized as "true" (omitted when false/undefined)
 */
export function useUrlState(): UrlStateReturn {
  const location = useLocation();

  const search: RegistrySearch = React.useMemo(() => {
    const raw: unknown = location.search;
    const parsed = registrySearchSchema.safeParse(raw);
    const base = parsed.success ? parsed.data : (registrySearchSchema.parse({}) as RegistrySearch);
    // Parse cf_* params from either a raw search string OR TanStack Router's
    // already-parsed object. v1.24.3: tanstack-router stores location.search as
    // a Record, never as a string — the earlier "string" branch was dead code,
    // so cfFilters never populated → per-column filters never applied.
    const cfSource: string | Record<string, string> =
      typeof location.search === "string"
        ? location.search
        : (location.search as Record<string, string>);
    const cfFilters = parseCfFiltersFromSearch(cfSource);
    const cfDateFilters = parseCfDateFiltersFromSearch(cfSource);
    const withCf: RegistrySearch = {
      ...base,
      ...(Object.keys(cfFilters).length > 0 ? { cfFilters } : {}),
      ...(Object.keys(cfDateFilters).length > 0 ? { cfDateFilters } : {}),
    };
    return migrateLegacySearch(withCf);
  }, [location.search]);

  const activeFilterCount = React.useMemo(() => countActiveFilters(search), [search]);

  // v1.24.12 — ВСЕ значения превращаем в строки ЗДЕСЬ, до передачи в
  // navigate({search}). Это РАДИКАЛЬНАЯ защита: arrays никогда не достигают
  // router-level validateSearch/stringifySearch — поэтому никакие type-checks
  // (typeof v in {string,number,boolean}) внутри TanStack Router не смогут их
  // тихо отбросить, как было до v1.24.11. Arrays сериализуются в CSV прямо
  // здесь, single-values через String(). Cf-filters flatten как раньше.
  const buildSearchObject = React.useCallback(
    (next: RegistrySearch): Record<string, string> => {
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(next)) {
        if (k === "cfFilters" || k === "cfDateFilters") continue;
        if (v === undefined || v === null || v === "" || v === false) continue;
        if (Array.isArray(v)) {
          if (v.length > 0) out[k] = v.join(",");
        } else {
          out[k] = String(v);
        }
      }
      if (next.cfFilters) {
        for (const [k, v] of Object.entries(serializeCfFilters(next.cfFilters))) {
          if (v) out[k] = v;
        }
      }
      if (next.cfDateFilters) {
        for (const [k, v] of Object.entries(serializeCfDateFilters(next.cfDateFilters))) {
          if (v) out[k] = v;
        }
      }
      return out;
    },
    [],
  );

  const navigateToSearch = React.useCallback(
    (next: RegistrySearch) => {
      const searchObj = buildSearchObject(next);
      const params = new URLSearchParams(searchObj);
      const qs = params.toString();
      const url = qs ? `${location.pathname}?${qs}` : location.pathname;
      // v1.24.15 — пишем URL ТОЛЬКО через History API + popstate.
      // КРИТИЧНО: НЕ вызываем navigate({to: url}) — TanStack Router'овский
      // navigate({to: string}) НЕ парсит query из строки и сбрасывает search
      // к defaults (= пустой объект + page=1 от schema). Это перетирало URL
      // обратно в `?page=1` сразу после pushState. (См. v1.24.14 регресс.)
      window.history.pushState(null, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    },
    [location.pathname, buildSearchObject],
  );

  const setSearch = React.useCallback(
    (partial: Partial<RegistrySearch>, resetPage = true) => {
      const next: RegistrySearch = {
        ...search,
        ...partial,
        page: resetPage && !("page" in partial) ? 1 : (partial.page ?? search.page),
      };
      navigateToSearch(next);
    },
    [search, navigateToSearch],
  );

  const resetFilters = React.useCallback(() => {
    navigateToSearch({
      sort: search.sort,
      dir: search.dir,
      page: 1,
    } as RegistrySearch);
  }, [navigateToSearch, search.sort, search.dir]);

  const removeFilter = React.useCallback(
    (key: FilterKey) => {
      const next = { ...search, [key]: undefined, page: 1 };
      navigateToSearch(next);
    },
    [search, navigateToSearch],
  );

  return { search, setSearch, resetFilters, activeFilterCount, removeFilter };
}
