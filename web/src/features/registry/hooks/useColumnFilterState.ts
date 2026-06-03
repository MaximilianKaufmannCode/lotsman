// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useColumnFilterState — per-column filter value + setter, synced to URL state.
 *
 * Design contract (v1.24.0):
 * - System fields write directly to their named URL param (expiry_from, expiry_to, etc.)
 * - Custom fields (cf_*) write into cfFilters[key]
 *
 * The hook returns the current value for a given fieldKey and a setter that
 * writes it to the URL. "Value" is typed as string | string[] | undefined
 * to cover all cases:
 *   - text / date columns: string
 *   - enum / FK columns: string[] (CSV stored, parsed as array)
 *   - unset: undefined
 *
 * Session-storage draft per column key: lotsman.registry.filter_draft.<columnId>
 * TTL: session (closed tab = cleared).
 */

import * as React from "react";
import type { RegistrySearch } from "./useUrlState";
import { useUrlState } from "./useUrlState";

export type ColumnFilterValue = string | string[] | undefined;

const SESSION_DRAFT_PREFIX = "lotsman.registry.filter_draft.";

function loadDraft(columnId: string): ColumnFilterValue {
  try {
    const raw = sessionStorage.getItem(SESSION_DRAFT_PREFIX + columnId);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as { value: ColumnFilterValue; ts: number };
    return parsed.value;
  } catch {
    return undefined;
  }
}

function saveDraft(columnId: string, value: ColumnFilterValue): void {
  try {
    sessionStorage.setItem(
      SESSION_DRAFT_PREFIX + columnId,
      JSON.stringify({ value, ts: Date.now() }),
    );
  } catch {
    // private browsing / storage full — silent
  }
}

function clearDraft(columnId: string): void {
  try {
    sessionStorage.removeItem(SESSION_DRAFT_PREFIX + columnId);
  } catch {
    // ignore
  }
}

/**
 * Get the committed (URL-applied) value for a given fieldKey from the URL state.
 * Handles both system params and cf_* custom fields.
 */
export function getFieldValue(search: RegistrySearch, fieldKey: string): ColumnFilterValue {
  if (fieldKey.startsWith("cfFilters.")) {
    const key = fieldKey.slice("cfFilters.".length);
    return search.cfFilters?.[key] ?? undefined;
  }
  // System fields
  switch (fieldKey) {
    case "asset_ids":
      return search.asset_ids && search.asset_ids.length > 0 ? search.asset_ids : undefined;
    case "type_codes":
      return search.type_codes && search.type_codes.length > 0 ? search.type_codes : undefined;
    case "responsible":
      return search.responsible ?? undefined;
    case "expiry_date": {
      // Return a composite value: from|to|null-flag
      const parts: string[] = [];
      if (search.expiry_from) parts.push(`from:${search.expiry_from}`);
      if (search.expiry_to) parts.push(`to:${search.expiry_to}`);
      if (search.expiry_perpetual) parts.push("null:true");
      return parts.length > 0 ? parts.join(";") : undefined;
    }
    case "created_at": {
      // created_at doesn't have a direct range param in v1.23.0; this is conceptual only
      return undefined;
    }
    case "updated_at": {
      const parts: string[] = [];
      if (search.updated_from) parts.push(`from:${search.updated_from}`);
      if (search.updated_to) parts.push(`to:${search.updated_to}`);
      return parts.length > 0 ? parts.join(";") : undefined;
    }
    case "number": {
      // v1.25.6 — combine textual `number` filter and the «— Не задано»
      // tick into the same column-funnel value. The popover stores
      // __NULL__ alongside text values as an array.
      const text = search.number;
      const wantsNull = !!search.number_is_null;
      if (text && wantsNull) return [text, "__NULL__"];
      if (text) return text;
      if (wantsNull) return ["__NULL__"];
      return undefined;
    }
    case "doc_status":
      return search.doc_status && search.doc_status.length > 0 ? search.doc_status : undefined;
    case "status":
      // v1.25.5 — multi-select array
      return search.status && search.status.length > 0 ? search.status : undefined;
    default:
      return undefined;
  }
}

/**
 * Count how many values are "selected" for a given fieldKey
 * (for the badge counter on the funnel icon).
 */
export function getFieldValueCount(search: RegistrySearch, fieldKey: string): number {
  const val = getFieldValue(search, fieldKey);
  if (val === undefined) return 0;
  if (Array.isArray(val)) return val.length;
  if (typeof val === "string") {
    // Special case for composite values (date range)
    if (val.includes(";")) return val.split(";").length;
    return 1;
  }
  return 0;
}

/**
 * Apply a filter value for the given fieldKey to the URL state.
 * `value = undefined` clears the filter.
 */
export function buildSearchPatch(
  fieldKey: string,
  value: ColumnFilterValue,
): Partial<RegistrySearch> {
  if (fieldKey.startsWith("cfFilters.")) {
    const key = fieldKey.slice("cfFilters.".length);
    return {
      cfFilters: value !== undefined
        ? { [key]: Array.isArray(value) ? value.join(",") : (value ?? "") }
        : {},
    };
  }

  if (value === undefined) {
    // Clear this field
    switch (fieldKey) {
      case "asset_ids":
        return { asset_ids: undefined };
      case "type_codes":
        return { type_codes: undefined };
      case "responsible":
        return { responsible: undefined };
      case "expiry_date":
        return { expiry_from: undefined, expiry_to: undefined, expiry_perpetual: undefined };
      case "updated_at":
        return { updated_from: undefined, updated_to: undefined };
      case "number":
        // v1.25.6 — clear both substring filter and «не задано» flag
        return { number: undefined, number_is_null: undefined };
      case "doc_status":
        return { doc_status: undefined };
      case "status":
        return { status: undefined };
      default:
        return {};
    }
  }

  // Set values
  switch (fieldKey) {
    case "asset_ids":
      return { asset_ids: Array.isArray(value) ? value : [value] };
    case "type_codes":
      return { type_codes: Array.isArray(value) ? value : [value] };
    case "responsible":
      return { responsible: Array.isArray(value) ? value[0] : value };
    case "number": {
      // v1.25.6 — split __NULL__ sentinel out into the dedicated
      // number_is_null URL param; preserve textual filter in `number`.
      const arr = Array.isArray(value) ? value : value ? [String(value)] : [];
      const wantsNull = arr.includes("__NULL__");
      const text = arr.filter((v) => v !== "__NULL__");
      return {
        number: text.length > 0 ? text[0] : undefined,
        number_is_null: wantsNull ? true : undefined,
      };
    }
    case "doc_status":
      return { doc_status: Array.isArray(value) ? value : [value] };
    case "status":
      // v1.25.5 — multi-select. Accept array or single-string; coerce to array.
      return {
        status: (Array.isArray(value) ? value : [value]) as RegistrySearch["status"],
      };
    default:
      return {};
  }
}

export interface ColumnFilterStateReturn {
  /** The committed (URL-applied) value for this column */
  committedValue: ColumnFilterValue;
  /** Number of selected values (for badge counter) */
  count: number;
  /** Whether any filter is active for this column */
  isActive: boolean;
  /** Apply draft value to URL — called from popover Apply button */
  apply: (value: ColumnFilterValue, extraPatch?: Partial<RegistrySearch>) => void;
  /** Clear this column's filter from URL */
  clear: () => void;
  /** Save draft to sessionStorage (popover close without apply) */
  saveDraftToSession: (value: ColumnFilterValue) => void;
  /** Load draft from sessionStorage (popover re-open) */
  loadDraftFromSession: () => ColumnFilterValue;
  /** Clear session draft (after successful apply) */
  clearSessionDraft: () => void;
}

/**
 * Per-column filter state hook.
 *
 * @param fieldKey - The field key for this column (e.g. 'expiry_date', 'cfFilters.yurisdikciya')
 * @param columnId - The TanStack Table column id (for sessionStorage draft key)
 */
export function useColumnFilterState(
  fieldKey: string,
  columnId: string,
): ColumnFilterStateReturn {
  const { search, setSearch } = useUrlState();

  const committedValue = React.useMemo(
    () => getFieldValue(search, fieldKey),
    [search, fieldKey],
  );

  const count = React.useMemo(
    () => getFieldValueCount(search, fieldKey),
    [search, fieldKey],
  );

  const isActive = count > 0;

  const apply = React.useCallback(
    (value: ColumnFilterValue, extraPatch?: Partial<RegistrySearch>) => {
      // For cfFilters, we need to MERGE with existing cfFilters, not replace
      if (fieldKey.startsWith("cfFilters.")) {
        const key = fieldKey.slice("cfFilters.".length);
        const existing = search.cfFilters ?? {};
        if (value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) {
          // Remove this key from cfFilters
          const { [key]: _removed, ...rest } = existing;
          setSearch({
            ...(extraPatch ?? {}),
            cfFilters: Object.keys(rest).length > 0 ? rest : undefined,
          });
        } else {
          setSearch({
            ...(extraPatch ?? {}),
            cfFilters: {
              ...existing,
              [key]: Array.isArray(value) ? value.join(",") : value,
            },
          });
        }
        clearDraft(columnId);
        return;
      }

      const patch = buildSearchPatch(fieldKey, value);
      setSearch({ ...patch, ...(extraPatch ?? {}) });
      clearDraft(columnId);
    },
    [fieldKey, setSearch, search, columnId],
  );

  const clear = React.useCallback(() => {
    apply(undefined);
  }, [apply]);

  const saveDraftToSession = React.useCallback(
    (value: ColumnFilterValue) => saveDraft(columnId, value),
    [columnId],
  );

  const loadDraftFromSession = React.useCallback(
    () => loadDraft(columnId),
    [columnId],
  );

  const clearSessionDraft = React.useCallback(
    () => clearDraft(columnId),
    [columnId],
  );

  return {
    committedValue,
    count,
    isActive,
    apply,
    clear,
    saveDraftToSession,
    loadDraftFromSession,
    clearSessionDraft,
  };
}
