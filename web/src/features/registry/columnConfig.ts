// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Column definitions for the registry TanStack Table.
 * Each column has: id, accessor path, display flags, and metadata for the
 * visibility panel (US-24). The "Контрагент" column is always pinned left
 * and cannot be hidden.
 *
 * v1.24.0: Added filterType + fieldKey for per-column header filter UI.
 */

/** v1.24.0 — per-column header filter type enum */
export type ColumnFilterType =
  | "text"
  | "date" // legacy: cf_date equality (single date) — kept for back-compat
  | "date-system" // system date: range + perpetual (expiry_date / updated_at / created_at)
  | "date-custom-range" // v1.24.17: cf_date range (from + to + is_null)
  | "fk-asset"
  | "fk-responsible"
  | "enum"
  | "doctype"
  | null;

export interface ColumnMeta {
  /** Human-readable label (i18n key sourced here, resolved at render time) */
  labelKey: string;
  /** data-analytics-key for future analytics tagging — English */
  analyticsKey: string;
  sortable: boolean;
  filterable: boolean;
  /** Hidden at 768–1279px tablet breakpoint */
  hideAtTablet: boolean;
  /** Cannot be hidden via the columns panel */
  alwaysVisible: boolean;
  /** Pin to the left — only "Контрагент" */
  pinLeft: boolean;
  /** Default visibility in the column panel */
  defaultVisible: boolean;

  // ── v1.24.0 per-column filter metadata ────────────────────────────────────
  /** Which filter popover type to render; null = no funnel icon */
  filterType?: ColumnFilterType;
  /**
   * The URL param key this column filter writes to.
   * - System fields: the bare param name, e.g. 'expiry_from'/'expiry_to', 'responsible', etc.
   * - Custom fields: 'cfFilters.<key>' where key is the cf_ key without the prefix.
   * When undefined, filterType must be null.
   */
  fieldKey?: string;
  /** Static enum options for filterType='enum' or 'doctype'. Doctype resolves at runtime. */
  enumOptions?: { value: string; label: string }[];
  /** Whether expiry_null (perpetual) toggle is available — only for expiry_date */
  supportsNull?: boolean;
}

export const COLUMN_META: Record<string, ColumnMeta> = {
  select: {
    labelKey: "",
    analyticsKey: "select",
    sortable: false,
    filterable: false,
    hideAtTablet: false,
    alwaysVisible: true,
    pinLeft: false,
    defaultVisible: true,
    filterType: null,
  },
  asset_name: {
    labelKey: "registry.col_counterparty",
    analyticsKey: "asset_name",
    sortable: true,
    filterable: true,
    hideAtTablet: false,
    // alwaysVisible/pinLeft are NOT hardcoded any more — the panel's
    // dynamic effectivePinnedId controls both. Default fallback (when
    // tenant_preferences has no pin set) is still asset_name.
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    filterType: "fk-asset",
    fieldKey: "asset_ids",
  },
  type_display_name: {
    labelKey: "registry.col_type",
    analyticsKey: "type_display_name",
    sortable: true,
    filterable: true,
    hideAtTablet: false,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    filterType: "doctype",
    fieldKey: "type_codes",
  },
  number: {
    labelKey: "registry.col_number",
    analyticsKey: "number",
    sortable: true,
    filterable: true,
    hideAtTablet: false,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    filterType: "text",
    fieldKey: "number",
  },
  expiry_date: {
    labelKey: "registry.col_valid_until",
    analyticsKey: "expiry_date",
    sortable: true,
    filterable: true,
    hideAtTablet: false,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    // v1.24.17 — возврат к date-system (range from/to + «Только бессрочные»)
    // после фикса bubble-bug в v1.24.16. Единый UX со всеми остальными
    // date-колонками (issue_date / updated_at / created_at / custom date-поля).
    filterType: "date-system",
    fieldKey: "expiry_date",
    supportsNull: true,
  },
  responsible_user_name: {
    labelKey: "registry.col_responsible",
    analyticsKey: "responsible_user_name",
    sortable: true,
    filterable: true,
    hideAtTablet: true,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    filterType: "fk-responsible",
    fieldKey: "responsible",
  },
  status: {
    labelKey: "registry.col_status",
    analyticsKey: "status",
    sortable: false,
    filterable: true,
    hideAtTablet: false,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: true,
    // v1.24.5 — колонка визуально показывает urgency (compute_status →
    // ok / soon / overdue / archived), не DB doc_status. Воронка фильтрует
    // по тому же urgency-полю (backend query-param `status`), чтобы
    // совпадало то что пользователь видит и что выбирает.
    filterType: "enum",
    fieldKey: "status",
    enumOptions: [
      { value: "ok", label: "ОК" },
      { value: "soon", label: "Скоро" },
      { value: "overdue", label: "Просрочено" },
      { value: "archived", label: "Архив" },
    ],
  },
  notes: {
    labelKey: "registry.col_notes",
    analyticsKey: "notes",
    sortable: false,
    filterable: false,
    hideAtTablet: true,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: false,
    filterType: null, // notes filter out-of-scope V1 — no backend full-text index
  },
  created_at: {
    labelKey: "registry.col_created_at",
    analyticsKey: "created_at",
    sortable: true,
    filterable: true,
    hideAtTablet: true,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: false,
    filterType: "date-system",
    fieldKey: "created_at",
  },
  updated_at: {
    labelKey: "registry.col_updated_at",
    analyticsKey: "updated_at",
    sortable: true,
    filterable: true,
    hideAtTablet: true,
    alwaysVisible: false,
    pinLeft: false,
    defaultVisible: false,
    filterType: "date-system",
    fieldKey: "updated_at",
  },
};

/** Ordered column ids for the table (controls left-to-right order). */
export const COLUMN_ORDER: string[] = [
  "select",
  "asset_name",
  "type_display_name",
  "number",
  "expiry_date",
  "responsible_user_name",
  "status",
  "notes",
  "created_at",
];

/** Default visibility per column id (for localStorage persistence). */
export function getDefaultColumnVisibility(): Record<string, boolean> {
  return Object.fromEntries(
    Object.entries(COLUMN_META).map(([id, meta]) => [id, meta.defaultVisible]),
  );
}
