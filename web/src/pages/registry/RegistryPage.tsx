// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * RegistryPage — the primary Excel-like virtualized document registry.
 *
 * Architecture:
 * - TanStack Table for column definitions, sorting, selection, column visibility
 * - TanStack Virtual for row virtualization (target: 10 000+ rows, DOM ≤500 nodes)
 * - URL is state for filters/sort/page (useUrlState + TanStack Router)
 * - Sticky header + pinned "Контрагент" column (position: sticky)
 * - Inline edit on double-click (cell-level state machine)
 * - Keyboard navigation: arrows, Enter/Esc, Home/End, PageUp/PageDown
 * - ⌘K / Ctrl+K for global search (header search input focus)
 * - Column visibility persisted in localStorage per US-24
 * - Adaptive: ≥1280px full; 768–1279px hide secondary; <768px cards
 *
 * Performance constraints (US-1 NFR):
 * - First paint <1.5s: Suspense skeleton matching final shape
 * - Scroll ≥60fps: virtual rendering, no layout thrash
 * - DOM ≤500 nodes at any scroll position
 */

import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useSearch } from "@tanstack/react-router";
import {
  type ColumnDef,
  type ColumnOrderState,
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  type RowSelectionState,
  type SortingState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import {
  Archive,
  ChevronDown,
  ChevronUp,
  Columns3,
  Download,
  FileX2,
  Filter,
  GripVertical,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { CustomField } from "@/features/admin/document-types/custom-fields-api";
import { useAuth } from "@/features/auth/AuthProvider";
import {
  COLUMN_META,
  type ColumnFilterType,
  COLUMN_ORDER as DEFAULT_COLUMN_ORDER,
  getDefaultColumnVisibility,
} from "@/features/registry/columnConfig";
import { computeStatus } from "@/features/registry/computeStatus";
import {
  ColumnFilterHeaderContent,
  useOpenColumnPopover,
} from "@/features/registry/filters/ColumnFilterButton";
import { ColumnFilterPopover } from "@/features/registry/filters/ColumnFilterPopover";
import { FilterChips } from "@/features/registry/filters/FilterChips";
import { FilterSheet } from "@/features/registry/filters/FilterSheet";
import { getFieldValueCount } from "@/features/registry/hooks/useColumnFilterState";
import { useColumnLabels } from "@/features/registry/hooks/useColumnLabels";
import { useColumnOrder, useUpdateColumnOrder } from "@/features/registry/hooks/useColumnOrder";
import {
  BULK_ARCHIVE_MAX,
  useBulkArchiveDocuments,
  usePatchDocument,
} from "@/features/registry/hooks/useDocumentMutations";
import { useDocuments } from "@/features/registry/hooks/useDocuments";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { useRequestExportJob } from "@/features/registry/hooks/useExportJob";
import { useUrlState } from "@/features/registry/hooks/useUrlState";
import type { Document } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { deriveRowHeight, useFontScale } from "@/shared/ui/font-scale";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { type DocumentStatus, StatusBadge } from "@/shared/ui/status-badge";
import { DocumentCreateDialog } from "./DocumentCreateDialog";
import { DocumentDetailDrawer } from "./DocumentDetailDrawer";
import { EditColumnDialog } from "./EditColumnDialog";
import { ExportJobsModal } from "./ExportJobsModal";
import { ImportXlsxDialog } from "./ImportXlsxDialog";

// ── Constants ─────────────────────────────────────────────────────────────────

const COLUMN_VISIBILITY_STORAGE_KEY_PREFIX = "lotsman_column_visibility_";
// Base row height at font-scale 100 (the historical fixed value). The effective
// height is derived from the user's font scale so rows grow with the text
// instead of clipping/mis-centering it (see deriveRowHeight + useFontScale).
const ROW_HEIGHT_BASE = 48; // px at scale 100
const PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 200;
const MIN_SEARCH_LENGTH = 2;

// ── Column helper ─────────────────────────────────────────────────────────────

const columnHelper = createColumnHelper<Document>();

// ── Inline cell edit state machine ────────────────────────────────────────────

interface EditingCell {
  rowId: string;
  columnId: string;
  initialValue: string;
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function RegistryPage() {
  const { t } = useTranslation();
  const { claims } = useAuth();
  const userId = claims?.sub ?? "anon";
  const canEdit = claims?.role === "editor" || claims?.role === "admin";

  // ── URL state ──────────────────────────────────────────────────────────────
  const {
    search: urlSearch,
    setSearch,
    resetFilters,
    activeFilterCount,
    removeFilter,
  } = useUrlState();

  // ── Global search debounce ─────────────────────────────────────────────────
  const [searchInput, setSearchInput] = React.useState(urlSearch.q ?? "");
  const searchRef = React.useRef<HTMLInputElement>(null);
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const [searchHint, setSearchHint] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      if (searchInput.length === 0) {
        setSearch({ q: undefined, page: 1 });
        setSearchHint(null);
      } else if (searchInput.length < MIN_SEARCH_LENGTH) {
        setSearchHint(`Введите минимум ${MIN_SEARCH_LENGTH} символа`);
      } else {
        setSearchHint(null);
        setSearch({ q: searchInput, page: 1 });
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput, setSearch]);

  // ⌘K / Ctrl+K global shortcut
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  // ── Column visibility (localStorage per US-24) ────────────────────────────
  const storageKey = `${COLUMN_VISIBILITY_STORAGE_KEY_PREFIX}${userId}`;
  const [columnVisibility, setColumnVisibility] = React.useState<VisibilityState>(() => {
    const defaults = getDefaultColumnVisibility();
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const parsed = JSON.parse(stored) as VisibilityState;
        // Merge: stored overrides defaults, but always keep pinned columns visible
        const merged = { ...defaults, ...parsed };
        // Enforce: always-visible columns cannot be hidden
        for (const [id, meta] of Object.entries(COLUMN_META)) {
          if (meta.alwaysVisible) merged[id] = true;
        }
        return merged;
      }
    } catch {
      // localStorage blocked or JSON parse failed — use defaults silently (US-24 edge)
    }
    return defaults;
  });

  const persistColumnVisibility = React.useCallback(
    (next: VisibilityState) => {
      setColumnVisibility(next);
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        // private browsing — no crash (US-24 edge)
      }
    },
    [storageKey],
  );

  // ── Tenant-wide column order (US-N) ────────────────────────────────────────
  const { data: columnOrderResp } = useColumnOrder();
  const updateColumnOrder = useUpdateColumnOrder();
  const { data: columnLabelsResp } = useColumnLabels();
  const labelOverrides = columnLabelsResp?.labels ?? {};

  // Edit-column dialog state.
  const [editColumnId, setEditColumnId] = React.useState<string | null>(null);

  // Helper: resolve display label for a column id.
  // Priority: admin-set override > built-in standard label > cf_* header from
  // column def > raw id as last resort.
  const builtInLabels: Record<string, string> = {
    asset_name: "Контрагент",
    type_display_name: "Тип",
    number: "№ документа",
    expiry_date: "Действ. до",
    responsible_user_name: "Ответственный",
    status: "Статус",
    notes: "Заметки",
    created_at: "Дата создания",
  };
  const effectiveLabel = (colId: string, fallback?: string): string => {
    const override = labelOverrides[colId];
    if (override && override.trim()) return override.trim();
    if (builtInLabels[colId]) return builtInLabels[colId];
    return fallback ?? colId;
  };

  // ── Row selection ──────────────────────────────────────────────────────────
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});

  // ── Drawer ─────────────────────────────────────────────────────────────────
  const [selectedDoc, setSelectedDoc] = React.useState<Document | null>(null);

  // ── Create dialog ──────────────────────────────────────────────────────────
  const [createOpen, setCreateOpen] = React.useState(false);
  const [importOpen, setImportOpen] = React.useState(false);
  const isAdmin = claims?.role === "admin";

  // ── Export jobs modal ──────────────────────────────────────────────────────
  const [exportModalOpen, setExportModalOpen] = React.useState(false);

  // ── Filters sheet ─────────────────────────────────────────────────────────
  const [filterPanelOpen, setFilterPanelOpen] = React.useState(false);
  const filterButtonRef = React.useRef<HTMLButtonElement | null>(null);

  // ── Per-column filter popover state (singleton — only one open at a time) ──
  const columnPopover = useOpenColumnPopover();

  // ── Columns panel ─────────────────────────────────────────────────────────
  const [columnPanelOpen, setColumnPanelOpen] = React.useState(false);
  const columnsButtonRef = React.useRef<HTMLButtonElement | null>(null);

  // ── Inline cell edit ───────────────────────────────────────────────────────
  const [editingCell, setEditingCell] = React.useState<EditingCell | null>(null);

  // ── Sorting (mirrors URL state) ────────────────────────────────────────────
  const sorting: SortingState = urlSearch.sort
    ? [{ id: urlSearch.sort, desc: urlSearch.dir === "desc" }]
    : [];

  // ── Data fetching ──────────────────────────────────────────────────────────
  // Build params excluding undefined values (exactOptionalPropertyTypes)
  const queryParams: import("@/features/registry/hooks/useDocuments").UseDocumentsOptions = {
    page: urlSearch.page ?? 1,
    page_size: PAGE_SIZE,
    ...(urlSearch.q ? { q: urlSearch.q } : {}),
    // Legacy single-value params (backward compat)
    ...(urlSearch.type_code && !urlSearch.type_codes?.length
      ? { type_code: urlSearch.type_code }
      : {}),
    ...(urlSearch.status ? { status: urlSearch.status } : {}),
    ...(urlSearch.asset_id && !urlSearch.asset_ids?.length ? { asset_id: urlSearch.asset_id } : {}),
    ...(urlSearch.show_archived ? { show_archived: urlSearch.show_archived } : {}),
    ...(urlSearch.sort ? { sort: urlSearch.sort } : {}),
    ...(urlSearch.dir ? { dir: urlSearch.dir } : {}),
    // New multi-criteria filter params (v1.23.0)
    ...(urlSearch.type_codes?.length ? { type_codes: urlSearch.type_codes } : {}),
    ...(urlSearch.asset_ids?.length ? { asset_ids: urlSearch.asset_ids } : {}),
    ...(urlSearch.responsible ? { responsible: urlSearch.responsible } : {}),
    ...(urlSearch.expiry_from ? { expiry_from: urlSearch.expiry_from } : {}),
    ...(urlSearch.expiry_to ? { expiry_to: urlSearch.expiry_to } : {}),
    ...(urlSearch.expiry_perpetual ? { expiry_null: true } : {}),
    ...(urlSearch.updated_from ? { updated_from: urlSearch.updated_from } : {}),
    ...(urlSearch.updated_to ? { updated_to: urlSearch.updated_to } : {}),
    ...(urlSearch.doc_status?.length ? { doc_status: urlSearch.doc_status } : {}),
    ...(urlSearch.expiry_dates?.length ? { expiry_dates: urlSearch.expiry_dates } : {}),
    // v1.24.2 + v1.24.4 — routed via dedicated top-level params.
    // asset_activity dropped в v1.24.4: see CHANGELOG (single source of truth =
    // column-header filter на cf_aktivnost).
    ...(urlSearch.jurisdiction?.length ? { jurisdiction: urlSearch.jurisdiction } : {}),
    ...(urlSearch.inn ? { inn: urlSearch.inn } : {}),
    ...(urlSearch.number ? { number: urlSearch.number } : {}),
    // v1.25.6 — «— Не задано» tick from № документа column funnel
    ...(urlSearch.number_is_null ? { number_is_null: true } : {}),
    // v1.24.0 cfFilters — schema-driven custom-field filters from per-column popovers
    ...(() => {
      if (!urlSearch.cfFilters) return {};
      const cf: Record<string, string> = {};
      for (const [k, v] of Object.entries(urlSearch.cfFilters)) {
        if (v) cf[k] = v;
      }
      return Object.keys(cf).length > 0 ? { custom_fields: cf } : {};
    })(),
    // v1.24.17 — cfDateFilters (range from/to/is_null) for any cf-date field
    ...(() => {
      const ranges = urlSearch.cfDateFilters;
      if (!ranges || Object.keys(ranges).length === 0) return {};
      return { custom_field_ranges: ranges };
    })(),
  };

  const { data, isLoading, isError, refetch } = useDocuments(queryParams);

  // Deep-link: open the document drawer when ?document_id=… is present and the
  // row is in the current result set (used by reminder emails + the in-app
  // notification bell). Opens once per id; does not mutate the URL.
  const rawSearch = useSearch({ strict: false }) as Record<string, unknown>;
  const deepLinkedRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    const did = typeof rawSearch?.document_id === "string" ? rawSearch.document_id : null;
    if (!did || deepLinkedRef.current === did) return;
    const found = data?.items?.find((d) => d.id === did);
    if (found) {
      setSelectedDoc(found);
      deepLinkedRef.current = did;
    }
  }, [rawSearch, data]);

  // ── Document types (for custom field schema) ───────────────────────────────
  const { data: docTypes } = useDocumentTypes();

  // ── Dynamic columns from custom field schemas ──────────────────────────────
  const dynamicFields = React.useMemo((): CustomField[] => {
    if (!docTypes || docTypes.length === 0) return [];
    const activeTypeCode = urlSearch.type_code;
    if (activeTypeCode) {
      // Single type filtered — use that type's schema
      const dt = docTypes.find((t) => t.code === activeTypeCode);
      return dt?.custom_field_schema ?? [];
    }
    // No filter — union all fields across all types, deduplicate by key
    const seen = new Set<string>();
    const fields: CustomField[] = [];
    for (const dt of docTypes) {
      for (const f of dt.custom_field_schema ?? []) {
        if (!seen.has(f.key)) {
          seen.add(f.key);
          fields.push(f);
        }
      }
    }
    return fields;
  }, [docTypes, urlSearch.type_code]);

  // ── Mutations ──────────────────────────────────────────────────────────────
  const patchMutation = usePatchDocument();
  const bulkArchiveMutation = useBulkArchiveDocuments();
  const exportMutation = useRequestExportJob();

  // ── Dynamic column defs ────────────────────────────────────────────────────
  const dynamicColumnDefs = React.useMemo((): ColumnDef<Document>[] => {
    return dynamicFields.map((field) => {
      // Map CustomField.type → ColumnFilterType for per-column filter UI
      // v1.24.17 — date custom fields теперь используют date-custom-range
      // popover (from + to + «Не задано»). Schema-driven: любое cf-поле
      // с type:"date" автоматически получает range filter. Single-date
      // "date" filterType остаётся в enum для backward-compat.
      const cfFilterType: ColumnFilterType =
        field.type === "text"
          ? "text"
          : field.type === "date"
            ? "date-custom-range"
            : field.type === "enum"
              ? "enum"
              : null; // number → out of scope V1

      const cfFieldKey = `cfFilters.${field.key}`;
      const cfEnumOptions =
        field.type === "enum" && Array.isArray(field.options)
          ? (field.options as string[]).map((v) => ({ value: v, label: v }))
          : undefined;

      return columnHelper.display({
        id: `cf_${field.key}`,
        header: effectiveLabel(`cf_${field.key}`, field.display_name),
        cell: ({ row }) => <DynamicFieldCell field={field} row={row.original} />,
        enableSorting: false,
        meta: {
          filterType: cfFilterType,
          fieldKey: cfFieldKey,
          isCustomField: true,
          enumOptions: cfEnumOptions,
        },
      });
    });
  }, [dynamicFields, labelOverrides]);

  // ── Column definitions ─────────────────────────────────────────────────────
  const columns = React.useMemo(
    (): ColumnDef<Document>[] =>
      [
        // Selection column (editor/admin only)
        ...(canEdit
          ? [
              columnHelper.display({
                id: "select",
                header: ({ table }) => (
                  <input
                    type="checkbox"
                    aria-label="Выбрать все строки"
                    checked={table.getIsAllPageRowsSelected()}
                    ref={(el) => {
                      if (el) {
                        el.indeterminate = table.getIsSomePageRowsSelected();
                      }
                    }}
                    onChange={table.getToggleAllPageRowsSelectedHandler()}
                    className="rounded border-input focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
                  />
                ),
                cell: ({ row }) => (
                  <input
                    type="checkbox"
                    aria-label={`Выбрать строку ${row.original.number}`}
                    checked={row.getIsSelected()}
                    disabled={!row.getCanSelect()}
                    onChange={row.getToggleSelectedHandler()}
                    className="rounded border-input focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
                  />
                ),
                size: 40,
              }),
            ]
          : []),

        // Контрагент — always visible, pinned left
        columnHelper.accessor("asset_name", {
          id: "asset_name",
          header: effectiveLabel("asset_name", t("registry.col_counterparty")),
          cell: (info) => (
            <span className="font-medium" data-analytics-key="asset_name">
              {info.getValue()}
            </span>
          ),
          enableSorting: true,
        }),

        // Тип документа
        columnHelper.accessor("type_display_name", {
          id: "type_display_name",
          header: effectiveLabel("type_display_name", t("registry.col_type")),
          cell: (info) => <span>{info.getValue()}</span>,
          enableSorting: true,
        }),

        // № документа
        columnHelper.accessor("number", {
          id: "number",
          header: effectiveLabel("number", t("registry.col_number")),
          cell: (info) => (
            <span className="font-mono text-xs" data-analytics-key="number">
              {info.getValue()}
            </span>
          ),
          enableSorting: true,
        }),

        // Действ. до
        columnHelper.accessor("expiry_date", {
          id: "expiry_date",
          header: effectiveLabel("expiry_date", t("registry.col_valid_until")),
          cell: (info) => {
            const val = info.getValue();
            return (
              <span className="font-mono text-xs tabular-nums" data-analytics-key="expiry_date">
                {val ? formatDate(val) : "Бессрочно"}
              </span>
            );
          },
          enableSorting: true,
        }),

        // Ответственный
        columnHelper.accessor("responsible_user_name", {
          id: "responsible_user_name",
          header: effectiveLabel("responsible_user_name", t("registry.col_responsible")),
          cell: (info) => <span>{info.getValue() ?? "—"}</span>,
          enableSorting: true,
        }),

        // Статус
        columnHelper.display({
          id: "status",
          header: effectiveLabel("status", t("registry.col_status")),
          cell: ({ row }) => {
            const doc = row.original;
            // Always derive display status from expiry_date + deleted_at.
            // doc.status from API is the raw DB enum ("active"|"archived"), not a display value.
            // computeStatus correctly handles archived (deleted_at != null) priority.
            const status = computeStatus(doc.expiry_date, doc.deleted_at);
            return <StatusBadge status={status} />;
          },
        }),

        // Заметки
        columnHelper.accessor("notes", {
          id: "notes",
          header: effectiveLabel("notes", t("registry.col_notes")),
          cell: (info) => (
            <span className="truncate max-w-[200px] block text-muted-foreground text-xs">
              {info.getValue() ?? "—"}
            </span>
          ),
          enableSorting: false,
        }),

        // Дата создания
        columnHelper.accessor("created_at", {
          id: "created_at",
          header: effectiveLabel("created_at", t("registry.col_created_at")),
          cell: (info) => (
            <span className="font-mono text-xs tabular-nums" data-analytics-key="created_at">
              {formatDate(info.getValue())}
            </span>
          ),
          enableSorting: true,
        }),

        // Dynamic custom field columns
        ...dynamicColumnDefs,
      ] as ColumnDef<Document>[],
    [t, canEdit, dynamicColumnDefs, labelOverrides],
  );

  // ── Effective pinned column ────────────────────────────────────────────────
  // Admin can pick which column is sticky-left; falls back to asset_name when
  // unset or when the chosen id isn't a known column (e.g. the cf_* it pointed
  // at was deleted via custom-fields admin).
  const effectivePinnedId = React.useMemo<string>(() => {
    const knownIds = new Set<string>(columns.map((c) => c.id ?? "").filter(Boolean));
    const candidate = columnOrderResp?.pinned_column_id;
    if (candidate && knownIds.has(candidate)) return candidate;
    return "asset_name";
  }, [columnOrderResp, columns]);

  // ── Effective column order ─────────────────────────────────────────────────
  // Server-stored order is the source of truth. Append any column ids that
  // exist in the current build (cf_* dynamic + new standard cols) but were
  // not yet stored. Always anchor `select` first and the pinned column
  // second (pin-left invariant — sticky positioning depends on this).
  const effectiveColumnOrder = React.useMemo<ColumnOrderState>(() => {
    const knownColIds = new Set<string>(columns.map((c) => c.id ?? "").filter(Boolean));
    const stored = (columnOrderResp?.order ?? []).filter((id) => knownColIds.has(id));
    const fallback = stored.length > 0 ? stored : DEFAULT_COLUMN_ORDER;
    const seen = new Set<string>(fallback);
    const tail = Array.from(knownColIds).filter((id) => !seen.has(id));
    let merged = [...fallback, ...tail];
    // Re-anchor invariants.
    merged = merged.filter((id) => id !== "select");
    merged.unshift("select");
    if (knownColIds.has(effectivePinnedId)) {
      merged = merged.filter((id) => id !== effectivePinnedId);
      merged.splice(1, 0, effectivePinnedId);
    }
    return merged;
  }, [columnOrderResp, columns, effectivePinnedId]);

  // ── Table instance ─────────────────────────────────────────────────────────
  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    enableRowSelection: canEdit,
    onRowSelectionChange: setRowSelection,
    state: {
      rowSelection,
      columnVisibility,
      sorting,
      columnOrder: effectiveColumnOrder,
    },
    onColumnVisibilityChange: (updater) => {
      const next = typeof updater === "function" ? updater(columnVisibility) : updater;
      // Enforce always-visible (no hardcoded ones now, but kept for future).
      for (const [id, meta] of Object.entries(COLUMN_META)) {
        if (meta.alwaysVisible) next[id] = true;
      }
      // The pinned column MUST stay visible — sticky-left contract.
      next[effectivePinnedId] = true;
      persistColumnVisibility(next);
    },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater;
      const first = next[0];
      setSearch({
        sort: first?.id,
        dir: first ? (first.desc ? "desc" : "asc") : undefined,
        page: 1,
      });
    },
    manualSorting: true,
    manualPagination: true,
    rowCount: data?.total ?? 0,
  });

  const rows = table.getRowModel().rows;

  // ── Virtualizer ────────────────────────────────────────────────────────────
  // We only virtualize for large datasets — for ≤ VIRTUALIZE_THRESHOLD rows,
  // a normal <tr> stream is used. Absolute-positioned <tr> elements break
  // <table> column layout (cells lose alignment with <th>), so virtualization
  // is reserved for cases where it's actually needed.
  const VIRTUALIZE_THRESHOLD = 200;
  const isVirtualized = rows.length > VIRTUALIZE_THRESHOLD;
  const scrollContainerRef = React.useRef<HTMLDivElement>(null);
  // Effective row height tracks the user's font-size preference so the fixed
  // virtualizer geometry stays proportional to the (rem-scaled) cell text.
  const fontScale = useFontScale();
  const rowHeight = deriveRowHeight(ROW_HEIGHT_BASE, fontScale);
  const virtualizer = useVirtualizer({
    count: isVirtualized ? rows.length : 0,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => rowHeight,
    overscan: 10,
  });
  // estimateSize is read once per measurement pass; when the scale (hence
  // rowHeight) changes we must force a re-measure so translateY offsets and the
  // total size recompute — otherwise rows would overlap or leave gaps.
  // rowHeight is a deliberate trigger dependency (not read in the body).
  // biome-ignore lint/correctness/useExhaustiveDependencies: rowHeight is a re-measure trigger, not referenced in the effect body
  React.useEffect(() => {
    virtualizer.measure();
  }, [virtualizer, rowHeight]);
  const virtualItems = virtualizer.getVirtualItems();
  const totalSize = isVirtualized ? virtualizer.getTotalSize() : 0;

  // ── Keyboard navigation ────────────────────────────────────────────────────
  const [focusedCell, setFocusedCell] = React.useState<{ row: number; col: number } | null>(null);
  const cellRefs = React.useRef<Map<string, HTMLTableCellElement>>(new Map());

  const handleCellKeyDown = React.useCallback(
    (
      e: React.KeyboardEvent<HTMLTableCellElement>,
      rowIndex: number,
      colIndex: number,
      row: (typeof rows)[0],
      columnId: string,
    ) => {
      const visibleCols = table.getVisibleLeafColumns();
      const totalRows = rows.length;
      const totalCols = visibleCols.length;

      let nextRow = rowIndex;
      let nextCol = colIndex;

      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          nextRow = Math.min(rowIndex + 1, totalRows - 1);
          break;
        case "ArrowUp":
          e.preventDefault();
          nextRow = Math.max(rowIndex - 1, 0);
          break;
        case "ArrowRight":
          e.preventDefault();
          nextCol = Math.min(colIndex + 1, totalCols - 1);
          break;
        case "ArrowLeft":
          e.preventDefault();
          nextCol = Math.max(colIndex - 1, 0);
          break;
        case "Home":
          e.preventDefault();
          nextCol = 0;
          break;
        case "End":
          e.preventDefault();
          nextCol = totalCols - 1;
          break;
        case "PageDown":
          e.preventDefault();
          nextRow = Math.min(rowIndex + 10, totalRows - 1);
          break;
        case "PageUp":
          e.preventDefault();
          nextRow = Math.max(rowIndex - 10, 0);
          break;
        case "Enter":
          if (canEdit && editingCell === null) {
            const doc = row.original;
            const accessorId = columnId;
            const val = doc[accessorId as keyof Document];
            if (val !== undefined && val !== null && typeof val === "string") {
              setEditingCell({
                rowId: row.id,
                columnId,
                initialValue: val,
              });
            }
          }
          return;
        case "Escape":
          setEditingCell(null);
          return;
        default:
          return;
      }

      setFocusedCell({ row: nextRow, col: nextCol });
      const nextColId = visibleCols[nextCol]?.id;
      const nextRowId = rows[nextRow]?.id;
      if (nextColId && nextRowId) {
        const ref = cellRefs.current.get(`${nextRowId}-${nextColId}`);
        ref?.focus();
      }
    },
    [table, rows, editingCell, canEdit],
  );

  // ── Inline edit handlers ───────────────────────────────────────────────────
  const handleCellDoubleClick = React.useCallback(
    (row: (typeof rows)[0], columnId: string) => {
      if (!canEdit) return;
      const meta = COLUMN_META[columnId];
      if (!meta?.sortable) return; // Only editable columns have sortable=true (heuristic)
      // Exclude non-text columns
      if (columnId === "status" || columnId === "select") return;

      const doc = row.original;
      const val = doc[columnId as keyof Document];
      setEditingCell({
        rowId: row.id,
        columnId,
        initialValue: val !== null && val !== undefined ? String(val) : "",
      });
    },
    [canEdit],
  );

  const handleCellEditCommit = React.useCallback(
    async (rowId: string, columnId: string, newValue: string) => {
      const row = rows.find((r) => r.id === rowId);
      if (!row) return;
      setEditingCell(null);
      const doc = row.original;
      const oldVal = String(doc[columnId as keyof Document] ?? "");
      if (newValue === oldVal) return; // No change
      await patchMutation.mutateAsync({ id: doc.id, payload: { [columnId]: newValue } });
    },
    [rows, patchMutation],
  );

  // ── Staleness polling (US-22 — concurrent edit detection) ─────────────────
  // Re-fetch every 10s when a drawer is open to detect remote changes
  React.useEffect(() => {
    if (!selectedDoc) return;
    const interval = setInterval(() => void refetch(), 10_000);
    return () => clearInterval(interval);
  }, [selectedDoc, refetch]);

  // ── Selection helpers ──────────────────────────────────────────────────────
  const selectedIds = Object.keys(rowSelection).filter((k) => rowSelection[k]);
  const selectedCount = selectedIds.length;

  const getSelectedDocIds = (): string[] =>
    table.getSelectedRowModel().rows.map((r) => r.original.id);

  const handleBulkArchive = async () => {
    const ids = getSelectedDocIds();
    if (ids.length === 0) return;
    if (ids.length > BULK_ARCHIVE_MAX) return;
    const confirmed = window.confirm(`Вы уверены? ${ids.length} документов будут архивированы.`);
    if (!confirmed) return;
    await bulkArchiveMutation.mutateAsync(ids);
    setRowSelection({});
  };

  // ── Export ─────────────────────────────────────────────────────────────────
  const handleExport = () => {
    const visibleColumns = table
      .getVisibleLeafColumns()
      .map((c) => c.id)
      .filter((id) => id !== "select");

    exportMutation.mutate({
      filter: {
        q: urlSearch.q,
        type_code: urlSearch.type_code,
        status: urlSearch.status,
        asset_id: urlSearch.asset_id,
        show_archived: urlSearch.show_archived,
        // v1.23.0 extended filters
        ...(urlSearch.type_codes?.length ? { type_codes: urlSearch.type_codes.join(",") } : {}),
        ...(urlSearch.asset_ids?.length ? { asset_ids: urlSearch.asset_ids.join(",") } : {}),
        ...(urlSearch.responsible ? { responsible: urlSearch.responsible } : {}),
        ...(urlSearch.expiry_from ? { expiry_from: urlSearch.expiry_from } : {}),
        ...(urlSearch.expiry_to ? { expiry_to: urlSearch.expiry_to } : {}),
        ...(urlSearch.expiry_perpetual ? { expiry_null: true } : {}),
        ...(urlSearch.doc_status?.length ? { doc_status: urlSearch.doc_status.join(",") } : {}),
      } as import("@/features/registry/types").FilterState,
      sort: {
        sort: urlSearch.sort,
        dir: urlSearch.dir,
      },
      visible_columns: visibleColumns,
    });
    setExportModalOpen(true);
  };

  // ── Adaptive: hide secondary columns at tablet breakpoint ──────────────────
  // This is handled by column visibility settings (COLUMN_META.hideAtTablet)
  // applied via a ResizeObserver. We let CSS handle it for now via Tailwind.

  // ── Skeleton loading state ─────────────────────────────────────────────────
  if (isLoading && !data) {
    return <RegistrySkeleton />;
  }

  // ── Error state ────────────────────────────────────────────────────────────
  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <p className="text-muted-foreground">Не удалось загрузить реестр</p>
        <Button variant="outline" onClick={() => void refetch()}>
          <RefreshCw className="size-4 mr-2" aria-hidden />
          Повторить
        </Button>
      </div>
    );
  }

  const totalCount = data?.total ?? 0;
  const showingCount = rows.length;

  return (
    <div className="flex flex-col h-full overflow-hidden" aria-live="polite">
      {/* Status change announcement for screen readers */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {isLoading ? "Загрузка документов..." : `Загружено ${totalCount} документов`}
      </div>

      {/* ── Toolbar ─────────────────────────────────────────────────────────── */}
      <div
        className="flex flex-wrap items-center gap-2 px-4 py-3 border-b bg-background shrink-0"
        role="toolbar"
        aria-label="Действия с реестром"
      >
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search
            className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none"
            aria-hidden
          />
          <Input
            ref={searchRef}
            type="search"
            aria-label="Глобальный поиск по реестру (⌘K)"
            placeholder="Поиск... (⌘K)"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-9"
          />
          {searchHint && (
            <p className="absolute top-full mt-1 text-xs text-muted-foreground" role="status">
              {searchHint}
            </p>
          )}
        </div>

        {/* Add document */}
        {canEdit && (
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" aria-hidden />
            {t("registry.add_document")}
          </Button>
        )}

        {/* Bulk archive */}
        {canEdit && selectedCount > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleBulkArchive()}
            disabled={bulkArchiveMutation.isPending}
          >
            <Archive className="size-4" aria-hidden />
            Архивировать ({selectedCount})
          </Button>
        )}

        <div className="flex items-center gap-1 ml-auto">
          {/* Advanced Filters (Расширенные фильтры) — renamed from v1.23.0 "Фильтры" */}
          <Button
            ref={filterButtonRef}
            size="sm"
            variant={activeFilterCount > 0 ? "default" : "outline"}
            onClick={() => setFilterPanelOpen((v) => !v)}
            aria-expanded={filterPanelOpen}
            aria-haspopup="dialog"
            aria-controls="filter-sheet"
            aria-label={
              activeFilterCount > 0
                ? `${t("registry.advanced_filters_button")}, ${activeFilterCount} активных`
                : t("registry.advanced_filters_button")
            }
          >
            <Filter className="size-4" aria-hidden />
            <span className="hidden sm:inline">
              {activeFilterCount > 0
                ? `${t("registry.advanced_filters_button")} (${activeFilterCount})`
                : t("registry.advanced_filters_button")}
            </span>
          </Button>

          {/* Columns */}
          <div className="relative">
            <Button
              ref={columnsButtonRef}
              size="sm"
              variant="outline"
              onClick={() => setColumnPanelOpen((v) => !v)}
              aria-expanded={columnPanelOpen}
              aria-haspopup="dialog"
            >
              <Columns3 className="size-4" aria-hidden />
              <span className="hidden sm:inline">{t("registry.columns")}</span>
            </Button>
            {columnPanelOpen && (
              <ColumnVisibilityPanel
                table={table}
                onClose={() => setColumnPanelOpen(false)}
                onReset={() => {
                  persistColumnVisibility(getDefaultColumnVisibility());
                  if (isAdmin) {
                    updateColumnOrder.mutate({
                      order: [...DEFAULT_COLUMN_ORDER],
                      pinned_column_id: "asset_name",
                    });
                  }
                }}
                onShowAll={() => {
                  // Build a visibility map that turns ON every leaf column
                  // currently in the table — including dynamic cf_* fields
                  // freshly added by an import.
                  const all: Record<string, boolean> = {};
                  for (const col of table.getAllLeafColumns()) {
                    all[col.id] = true;
                  }
                  persistColumnVisibility(all);
                }}
                canReorder={isAdmin}
                pinnedColumnId={effectivePinnedId}
                onReorder={(nextOrder) =>
                  updateColumnOrder.mutate({
                    order: nextOrder,
                    pinned_column_id: effectivePinnedId,
                  })
                }
                onEditColumn={
                  isAdmin
                    ? (colId) => {
                        setEditColumnId(colId);
                        setColumnPanelOpen(false);
                      }
                    : undefined
                }
                onChangePinned={(nextPinnedId) => {
                  // Force-show the new pinned column.
                  if (!columnVisibility[nextPinnedId]) {
                    persistColumnVisibility({
                      ...columnVisibility,
                      [nextPinnedId]: true,
                    });
                  }
                  // Move the pinned column to position 1 (after `select`).
                  const next = effectiveColumnOrder.filter((id) => id !== nextPinnedId);
                  next.splice(1, 0, nextPinnedId);
                  updateColumnOrder.mutate({
                    order: next,
                    pinned_column_id: nextPinnedId,
                  });
                }}
                triggerRef={columnsButtonRef}
              />
            )}
          </div>

          {/* Import (admin only) */}
          {isAdmin && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setImportOpen(true)}
              data-testid="import-xlsx-button"
            >
              <Upload className="size-4" aria-hidden />
              <span className="hidden sm:inline">Импорт</span>
            </Button>
          )}

          {/* Export */}
          <Button
            size="sm"
            variant="outline"
            onClick={handleExport}
            disabled={exportMutation.isPending}
          >
            <Download className="size-4" aria-hidden />
            <span className="hidden sm:inline">{t("registry.export_xlsx")}</span>
          </Button>
        </div>
      </div>

      {/* ── Filter chips bar — shown when any filter active ──────────────────── */}
      {activeFilterCount > 0 && (
        <FilterChips
          search={urlSearch}
          onRemove={(key, cfKey) => {
            if (key === "expiry_from") {
              setSearch({ expiry_from: undefined, expiry_to: undefined });
            } else if (key === "expiry_to") {
              setSearch({ expiry_from: undefined, expiry_to: undefined });
            } else if (key === "updated_from") {
              setSearch({ updated_from: undefined, updated_to: undefined });
            } else if (key === "updated_to") {
              setSearch({ updated_from: undefined, updated_to: undefined });
            } else if (key === "cfFilters" && cfKey) {
              const { [cfKey]: _removed, ...rest } = urlSearch.cfFilters ?? {};
              setSearch({ cfFilters: Object.keys(rest).length > 0 ? rest : undefined });
            } else if (key === "cfDateFilters" && cfKey) {
              // v1.24.17 — remove single date-range cf field
              const { [cfKey]: _removed, ...rest } = urlSearch.cfDateFilters ?? {};
              setSearch({ cfDateFilters: Object.keys(rest).length > 0 ? rest : undefined });
            } else {
              removeFilter(key);
            }
          }}
          onClearAll={resetFilters}
          onChipClick={(_key) => {
            setFilterPanelOpen(true);
          }}
        />
      )}

      {/* ── FilterSheet (right-side drawer) ──────────────────────────────────── */}
      <FilterSheet
        open={filterPanelOpen}
        onClose={() => {
          setFilterPanelOpen(false);
        }}
        urlSearch={urlSearch}
        onApply={(draft) => {
          setSearch({
            ...draft,
            page: 1,
          } as Parameters<typeof setSearch>[0]);
          setFilterPanelOpen(false);
        }}
        onReset={resetFilters}
        triggerRef={filterButtonRef}
      />

      {/* ── Bulk selection warning ─────────────────────────────────────────── */}
      {canEdit && selectedCount >= BULK_ARCHIVE_MAX && (
        <div
          role="alert"
          className="px-4 py-2 text-xs bg-status-soon/10 text-status-soon border-b border-status-soon/20"
        >
          Максимум {BULK_ARCHIVE_MAX} документов за одну операцию
        </div>
      )}

      {/* ── Table — desktop (≥768px) ─────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto hidden md:block" ref={scrollContainerRef} tabIndex={-1}>
        <table
          className="w-full border-collapse text-sm"
          role="grid"
          aria-label={t("registry.title")}
          aria-rowcount={totalCount}
        >
          <thead className="sticky top-0 z-20 bg-muted/95 backdrop-blur-sm border-b">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const meta = COLUMN_META[header.column.id];
                  const isPinned = header.column.id === effectivePinnedId;
                  const isSortable = header.column.getCanSort();
                  const sortDir = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      data-analytics-key={meta?.analyticsKey}
                      aria-sort={
                        sortDir === "asc"
                          ? "ascending"
                          : sortDir === "desc"
                            ? "descending"
                            : isSortable
                              ? "none"
                              : undefined
                      }
                      className={cn(
                        "px-3 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider whitespace-nowrap",
                        isPinned &&
                          "sticky left-0 z-10 bg-muted/95 backdrop-blur-sm shadow-[2px_0_4px_-2px_rgba(0,0,0,0.15)]",
                        isSortable && "cursor-pointer select-none hover:text-foreground",
                        // Hide secondary columns at tablet breakpoint
                        meta?.hideAtTablet && "xl:table-cell hidden",
                      )}
                      onClick={isSortable ? header.column.getToggleSortingHandler() : undefined}
                      onKeyDown={
                        isSortable
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                header.column.toggleSorting();
                              }
                            }
                          : undefined
                      }
                      tabIndex={isSortable ? 0 : undefined}
                    >
                      {header.isPlaceholder ? null : (
                        <HeaderCellContent
                          header={header}
                          meta={meta}
                          urlSearch={urlSearch}
                          columnPopover={columnPopover}
                          setSearch={setSearch}
                          sortDir={sortDir}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>

          <tbody
            style={isVirtualized ? { height: `${totalSize}px`, position: "relative" } : undefined}
          >
            {rows.length === 0 ? (
              <tr>
                <td colSpan={table.getVisibleLeafColumns().length} className="text-center py-24">
                  <EmptyState
                    isFiltered={!!(urlSearch.q || activeFilterCount > 0)}
                    onReset={resetFilters}
                    onAdd={canEdit ? () => setCreateOpen(true) : undefined}
                  />
                </td>
              </tr>
            ) : (
              (isVirtualized
                ? virtualItems.map((v) => ({ index: v.index, start: v.start }))
                : rows.map((_, i) => ({ index: i, start: 0 }))
              ).map((virtualRow) => {
                const row = rows[virtualRow.index];
                if (!row) return null;

                const doc = row.original;
                const isSelected = row.getIsSelected();

                return (
                  <tr
                    key={row.id}
                    aria-rowindex={virtualRow.index + 2}
                    aria-selected={canEdit ? isSelected : undefined}
                    data-index={virtualRow.index}
                    style={
                      isVirtualized
                        ? {
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            height: `${rowHeight}px`,
                            transform: `translateY(${virtualRow.start}px)`,
                          }
                        : { height: `${rowHeight}px` }
                    }
                    className={cn(
                      "border-b transition-colors",
                      isSelected ? "bg-primary/5" : "hover:bg-muted/30",
                      "cursor-pointer",
                    )}
                    onClick={(e) => {
                      // Single click: open drawer (unless clicking checkbox)
                      if ((e.target as HTMLElement).closest("input[type=checkbox]")) return;
                      if (editingCell?.rowId === row.id) return;
                      setSelectedDoc(doc);
                    }}
                  >
                    {row.getVisibleCells().map((cell, colIdx) => {
                      const meta = COLUMN_META[cell.column.id];
                      const isPinned = cell.column.id === effectivePinnedId;
                      const isEditable =
                        canEdit &&
                        cell.column.id !== "select" &&
                        cell.column.id !== "status" &&
                        cell.column.id !== "responsible_user_name";

                      const isCurrentlyEditing =
                        editingCell?.rowId === row.id && editingCell.columnId === cell.column.id;

                      return (
                        <td
                          key={cell.id}
                          ref={(el) => {
                            if (el) {
                              cellRefs.current.set(`${row.id}-${cell.column.id}`, el);
                            } else {
                              cellRefs.current.delete(`${row.id}-${cell.column.id}`);
                            }
                          }}
                          // biome-ignore lint/a11y/noNoninteractiveElementToInteractiveRole: role="gridcell" on td is valid per ARIA 1.2 inside a role="grid"
                          role="gridcell"
                          tabIndex={
                            focusedCell?.row === virtualRow.index && focusedCell?.col === colIdx
                              ? 0
                              : -1
                          }
                          data-testid={`cell-${row.id}-${cell.column.id}`}
                          className={cn(
                            "px-3 py-2 text-sm align-middle",
                            isPinned &&
                              "sticky left-0 z-10 bg-background group-hover:bg-muted/30 shadow-[2px_0_4px_-2px_rgba(0,0,0,0.15)]",
                            isSelected && isPinned && "bg-primary/5",
                            meta?.hideAtTablet && "xl:table-cell hidden",
                            isEditable &&
                              "focus-within:ring-2 focus-within:ring-inset focus-within:ring-ring",
                          )}
                          onDoubleClick={() => handleCellDoubleClick(row, cell.column.id)}
                          onKeyDown={(e) =>
                            handleCellKeyDown(e, virtualRow.index, colIdx, row, cell.column.id)
                          }
                          onFocus={() => setFocusedCell({ row: virtualRow.index, col: colIdx })}
                        >
                          {isCurrentlyEditing ? (
                            <InlineCellEditor
                              initialValue={editingCell.initialValue}
                              onCommit={(val) =>
                                void handleCellEditCommit(row.id, cell.column.id, val)
                              }
                              onCancel={() => setEditingCell(null)}
                            />
                          ) : (
                            flexRender(cell.column.columnDef.cell, cell.getContext())
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* ── Card view — mobile (<768px) ──────────────────────────────────────── */}
      <div className="flex-1 overflow-auto md:hidden">
        {rows.length === 0 ? (
          <EmptyState
            isFiltered={!!(urlSearch.q || activeFilterCount > 0)}
            onReset={resetFilters}
            onAdd={canEdit ? () => setCreateOpen(true) : undefined}
          />
        ) : (
          <ul aria-label={t("registry.title")} className="divide-y">
            {rows.map((row) => {
              const doc = row.original;
              const status = doc.status ?? computeStatus(doc.expiry_date, doc.deleted_at);
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    className="w-full text-left p-4 hover:bg-muted/30 cursor-pointer"
                    onClick={() => setSelectedDoc(doc)}
                    aria-label={`${doc.asset_name} — ${doc.number}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium truncate">{doc.asset_name}</p>
                        <p className="text-xs text-muted-foreground truncate">
                          {doc.type_display_name}
                        </p>
                      </div>
                      <StatusBadge status={status as DocumentStatus} />
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="font-mono">{doc.number}</span>
                      {doc.expiry_date && <span>до {formatDate(doc.expiry_date)}</span>}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ── Footer ──────────────────────────────────────────────────────────── */}
      <div
        className="flex items-center justify-between px-4 py-2 border-t bg-muted/30 text-xs text-muted-foreground shrink-0"
        aria-live="polite"
      >
        <span>
          Всего: {totalCount} · Показано: {showingCount}
          {selectedCount > 0 && ` · Выбрано: ${selectedCount}`}
        </span>

        {/* Pagination */}
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={(urlSearch.page ?? 1) <= 1}
            onClick={() => setSearch({ page: (urlSearch.page ?? 1) - 1 })}
            aria-label="Предыдущая страница"
          >
            ‹
          </Button>
          <span>
            Стр. {urlSearch.page ?? 1} / {Math.max(1, Math.ceil(totalCount / PAGE_SIZE))}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={(urlSearch.page ?? 1) >= Math.ceil(totalCount / PAGE_SIZE)}
            onClick={() => setSearch({ page: (urlSearch.page ?? 1) + 1 })}
            aria-label="Следующая страница"
          >
            ›
          </Button>
        </div>
      </div>

      {/* ── Modals / overlays ────────────────────────────────────────────────── */}
      <DocumentDetailDrawer document={selectedDoc} onClose={() => setSelectedDoc(null)} />

      <DocumentCreateDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <ImportXlsxDialog open={importOpen} onClose={() => setImportOpen(false)} />

      <ExportJobsModal open={exportModalOpen} onClose={() => setExportModalOpen(false)} />

      {/* Edit column dialog (admin) — renders only when a column is selected */}
      {(() => {
        if (!editColumnId) return null;
        const isCustom = editColumnId.startsWith("cf_");
        const fieldKey = editColumnId.replace(/^cf_/, "");
        const cf = isCustom
          ? (docTypes ?? [])
              .flatMap((dt) => dt.custom_field_schema ?? [])
              .find((f) => f.key === fieldKey)
          : undefined;
        const labelNow = effectiveLabel(editColumnId, isCustom ? cf?.display_name : undefined);
        return (
          <EditColumnDialog
            open={true}
            columnId={editColumnId}
            currentLabel={labelNow}
            isCustom={isCustom}
            currentType={cf?.type}
            onClose={() => setEditColumnId(null)}
          />
        );
      })()}
    </div>
  );
}

// ── HeaderCellContent ──────────────────────────────────────────────────────────

/**
 * HeaderCellContent — renders the content of a <th> cell, including the
 * sort indicator and the optional per-column filter button.
 */
function HeaderCellContent({
  header,
  meta,
  urlSearch,
  columnPopover,
  setSearch,
  sortDir,
}: {
  header: ReturnType<
    ReturnType<typeof useReactTable<Document>>["getHeaderGroups"]
  >[number]["headers"][number];
  meta: import("@/features/registry/columnConfig").ColumnMeta | undefined;
  urlSearch: import("@/features/registry/hooks/useUrlState").RegistrySearch;
  columnPopover: ReturnType<typeof useOpenColumnPopover>;
  setSearch: (
    patch: Partial<import("@/features/registry/hooks/useUrlState").RegistrySearch>,
  ) => void;
  sortDir: false | "asc" | "desc";
}) {
  const colId = header.column.id;
  const colMeta = header.column.columnDef.meta as
    | {
        filterType?: import("@/features/registry/columnConfig").ColumnFilterType;
        fieldKey?: string;
        enumOptions?: { value: string; label: string }[];
        isCustomField?: boolean;
        supportsNull?: boolean;
      }
    | undefined;

  // Resolve filterType: from column def meta first, then COLUMN_META fallback
  const filterType = colMeta?.filterType ?? meta?.filterType ?? null;
  const fieldKey = colMeta?.fieldKey ?? meta?.fieldKey;
  const enumOptions = colMeta?.enumOptions ?? meta?.enumOptions;
  const supportsNull = colMeta?.supportsNull ?? meta?.supportsNull;

  const activeCount = fieldKey ? getFieldValueCount(urlSearch, fieldKey) : 0;
  const isOpen = columnPopover.isOpen(colId);

  const rawLabel = flexRender(header.column.columnDef.header, header.getContext());
  const labelStr =
    typeof header.column.columnDef.header === "string"
      ? header.column.columnDef.header
      : String(colId);

  if (filterType === null || filterType === undefined || !fieldKey) {
    // No filter button — render label + sort indicator only
    return (
      <>
        {rawLabel}
        {sortDir === "asc" && " ↑"}
        {sortDir === "desc" && " ↓"}
      </>
    );
  }

  return (
    <ColumnFilterHeaderContent
      columnId={colId}
      label={
        <span className="flex items-center gap-0.5">
          {rawLabel}
          {sortDir === "asc" && " ↑"}
          {sortDir === "desc" && " ↓"}
        </span>
      }
      columnLabel={labelStr}
      filterType={filterType}
      activeCount={activeCount}
      isOpen={isOpen}
      onToggle={() => columnPopover.toggleColumn(colId)}
      onClose={() => columnPopover.closeColumn()}
      renderPopover={() => (
        <ColumnFilterPopover
          columnId={colId}
          columnLabel={labelStr}
          filterType={filterType}
          fieldKey={fieldKey}
          {...(enumOptions !== undefined ? { enumOptions } : {})}
          {...(supportsNull !== undefined ? { supportsNull } : {})}
          search={urlSearch}
          onApply={(patch) => {
            setSearch(patch as Parameters<typeof setSearch>[0]);
          }}
          onClose={() => columnPopover.closeColumn()}
        />
      )}
    />
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function InlineCellEditor({
  initialValue,
  onCommit,
  onCancel,
}: {
  initialValue: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = React.useState(initialValue);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const commit = () => {
    if (value.trim() === "") return; // Reject empty required field attempt
    onCommit(value);
  };

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        }
        if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
        e.stopPropagation(); // Don't bubble to row's keydown
      }}
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "w-full rounded border border-primary bg-background px-2 py-0.5 text-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
      aria-label="Редактирование ячейки"
    />
  );
}

function EmptyState({
  isFiltered,
  onReset,
  onAdd,
}: {
  isFiltered: boolean;
  onReset: () => void;
  onAdd?: (() => void) | undefined;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4 text-center">
      <FileX2 className="size-16 text-muted-foreground/40" aria-hidden />
      {isFiltered ? (
        <>
          <p className="text-base font-medium">Ничего не найдено</p>
          <button
            type="button"
            onClick={onReset}
            className="text-sm text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            Сбросить фильтры
          </button>
        </>
      ) : (
        <>
          <div>
            <p className="text-base font-medium">Документы не найдены</p>
            <p className="mt-1 text-sm text-muted-foreground max-w-xs mx-auto">
              Добавьте первый документ, чтобы начать вести реестр.
            </p>
          </div>
          {onAdd && (
            <Button onClick={onAdd}>
              <Plus className="size-4" aria-hidden />
              Добавить документ
            </Button>
          )}
          {!onAdd && (
            <p className="text-sm text-muted-foreground">
              Обратитесь к администратору или редактору для добавления документов.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ColumnVisibilityPanel({
  table,
  onClose,
  onReset,
  onShowAll,
  canReorder,
  pinnedColumnId,
  onReorder,
  onChangePinned,
  onEditColumn,
  triggerRef,
}: {
  table: ReturnType<typeof useReactTable<Document>>;
  onClose: () => void;
  onReset: () => void;
  /** Show every column (including custom-field cf_* ones) — useful right
   * after an import when the user wants the registry to mirror the xlsx. */
  onShowAll: () => void;
  /** True for admin role — gates reorder gestures and arrow buttons. */
  canReorder: boolean;
  /** Currently pinned (sticky-left) column id. */
  pinnedColumnId: string;
  /** Called with the new full column order (incl. `select`) when the user
   * drags / arrow-keys. The caller persists it server-side. */
  onReorder: (nextOrder: string[]) => void;
  /** Called when admin clicks the pin icon on a non-pinned column. */
  onChangePinned: (newPinnedId: string) => void;
  /** Open edit-column dialog for the given column id (admin-only). */
  onEditColumn?: ((colId: string) => void) | undefined;
  /** Ref to the trigger button — used to compute portal position so the
   * panel escapes any `overflow:hidden` on the page shell. */
  triggerRef: React.RefObject<HTMLElement | null>;
}) {
  const panelRef = React.useRef<HTMLDivElement>(null);
  const [position, setPosition] = React.useState<{ top: number; right: number }>({
    top: 0,
    right: 0,
  });

  // Compute portal coordinates from the trigger button's bounding rect.
  React.useLayoutEffect(() => {
    function updatePosition() {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const rect = trigger.getBoundingClientRect();
      setPosition({
        top: rect.bottom + 4, // 4px gap below the button
        right: window.innerWidth - rect.right,
      });
    }
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [triggerRef]);

  React.useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      // Don't close if click is inside panel OR on the trigger button itself
      // (the button toggles open/close via its own onClick).
      if (panelRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      onClose();
    };
    const keyHandler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", keyHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", keyHandler);
    };
  }, [onClose, triggerRef]);

  const allOptionalHidden = table
    .getAllLeafColumns()
    .filter((c) => !COLUMN_META[c.id]?.alwaysVisible)
    .every((c) => !c.getIsVisible());

  // Visible (in panel) leaf columns — `select` is administrative, hidden
  // from the user-facing list.
  const visibleColumns = React.useMemo(
    () => table.getAllLeafColumns().filter((c) => c.id !== "select"),
    // table is referentially stable per render; depends on column defs.
    // biome-ignore lint/correctness/useExhaustiveDependencies: table identity is stable
    [table.getAllLeafColumns()],
  );
  const sortableIds = visibleColumns.map((c) => c.id);

  // Move helper — applied for both DnD drop and ↑↓ buttons.
  // Always builds the FULL order (select first), so server stores a
  // self-contained list independent of FE column-def changes.
  function buildFullOrder(panelOrder: string[]): string[] {
    return ["select", ...panelOrder];
  }
  function handleMove(fromId: string, toId: string) {
    if (fromId === toId) return;
    const fromIdx = sortableIds.indexOf(fromId);
    const toIdx = sortableIds.indexOf(toId);
    if (fromIdx < 0 || toIdx < 0) return;
    // Disallow moving the pinned column away from index 0 in the panel
    // (it MUST stay first — sticky left contract).
    if (fromId === pinnedColumnId || toIdx === 0) return;
    const next = [...sortableIds];
    next.splice(fromIdx, 1);
    next.splice(toIdx, 0, fromId);
    onReorder(buildFullOrder(next));
  }

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  return createPortal(
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Настройка колонок"
      className="fixed z-50 w-72 rounded-md border bg-popover p-3 shadow-md flex flex-col"
      style={{
        top: position.top,
        right: position.right,
        maxHeight: "calc(100vh - 8rem)",
      }}
    >
      <div className="mb-2 flex items-baseline justify-between">
        <p className="text-xs font-semibold text-muted-foreground">Колонки</p>
        {canReorder && <p className="text-[10px] text-muted-foreground">Перетащи или ↑/↓</p>}
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={(e: DragEndEvent) => {
          const fromId = String(e.active.id);
          const toId = e.over ? String(e.over.id) : "";
          if (toId) handleMove(fromId, toId);
        }}
      >
        <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
          <ul className="space-y-1">
            {visibleColumns.map((col, idx) => (
              <SortableColumnRow
                key={col.id}
                col={col}
                index={idx}
                lastIndex={visibleColumns.length - 1}
                canReorder={canReorder}
                pinnedColumnId={pinnedColumnId}
                onPin={() => onChangePinned(col.id)}
                onMoveUp={() => {
                  const prev = visibleColumns[idx - 1];
                  if (prev) handleMove(col.id, prev.id);
                }}
                onMoveDown={() => {
                  const next = visibleColumns[idx + 1];
                  if (next) handleMove(col.id, next.id);
                }}
                onEdit={onEditColumn ? () => onEditColumn(col.id) : undefined}
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
      {allOptionalHidden && (
        <p className="mt-2 text-xs text-muted-foreground">
          Минимальный набор: Контрагент, Тип, Статус
        </p>
      )}
      <div className="mt-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onShowAll}
          className="text-xs text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          Показать все
        </button>
        <button
          type="button"
          onClick={onReset}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        >
          По умолчанию
        </button>
      </div>
    </div>,
    document.body,
  );
}

function SortableColumnRow({
  col,
  index,
  lastIndex,
  canReorder,
  pinnedColumnId,
  onPin,
  onMoveUp,
  onMoveDown,
  onEdit,
}: {
  col: ReturnType<ReturnType<typeof useReactTable<Document>>["getAllLeafColumns"]>[number];
  index: number;
  lastIndex: number;
  canReorder: boolean;
  pinnedColumnId: string;
  onPin: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  /** Open edit dialog for this column id (admin-only). */
  onEdit?: (() => void) | undefined;
}) {
  const meta = COLUMN_META[col.id];
  const isPinned = col.id === pinnedColumnId;
  // Pinned column is force-visible — checkbox disabled.
  const isAlwaysVisible = (meta?.alwaysVisible ?? false) || isPinned;
  const isDynamic = col.id.startsWith("cf_");
  const label = isDynamic
    ? (col.columnDef.header as string)
    : meta?.labelKey
      ? col.id === "asset_name"
        ? "Контрагент"
        : col.id === "type_display_name"
          ? "Тип"
          : col.id === "number"
            ? "№ документа"
            : col.id === "expiry_date"
              ? "Действ. до"
              : col.id === "responsible_user_name"
                ? "Ответственный"
                : col.id === "status"
                  ? "Статус"
                  : col.id === "notes"
                    ? "Заметки"
                    : "Дата создания"
      : col.id;

  // Pinned column (Контрагент) is not draggable — keeps it first.
  const allowDrag = canReorder && !isPinned;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: col.id,
    disabled: !allowDrag,
  });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={cn("flex items-center gap-1 rounded px-1 py-0.5", isDragging && "bg-accent")}
    >
      {allowDrag ? (
        <button
          type="button"
          {...attributes}
          {...listeners}
          aria-label={`Перетащить колонку «${label}»`}
          className="cursor-grab text-muted-foreground hover:text-foreground p-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <GripVertical className="size-3.5" aria-hidden />
        </button>
      ) : (
        <span className="w-[18px] text-muted-foreground/50" aria-hidden>
          {isPinned ? <Pin className="size-3.5" /> : null}
        </span>
      )}
      <input
        type="checkbox"
        id={`col-toggle-${col.id}`}
        checked={col.getIsVisible()}
        disabled={isAlwaysVisible}
        onChange={col.getToggleVisibilityHandler()}
        className="rounded focus-visible:ring-2 focus-visible:ring-ring"
        title={isAlwaysVisible ? "Эта колонка закреплена" : undefined}
      />
      <label
        htmlFor={`col-toggle-${col.id}`}
        className={cn(
          "text-sm flex-1 truncate",
          isAlwaysVisible && "text-muted-foreground",
          isDynamic && "italic",
        )}
      >
        {label}
      </label>
      {canReorder && (
        <span className="flex shrink-0 items-center">
          {onEdit && (
            <button
              type="button"
              onClick={onEdit}
              aria-label={`Переименовать колонку «${label}»`}
              title="Изменить название и тип данных"
              className="text-muted-foreground hover:text-primary p-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Pencil className="size-3.5" aria-hidden />
            </button>
          )}
          {!isPinned && (
            <button
              type="button"
              onClick={onPin}
              aria-label={`Закрепить колонку «${label}» слева`}
              title="Сделать закреплённой колонкой"
              className="text-muted-foreground hover:text-primary p-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Pin className="size-3.5" aria-hidden />
            </button>
          )}
          {!isPinned && (
            <button
              type="button"
              onClick={onMoveUp}
              disabled={index <= 1}
              aria-label={`Передвинуть «${label}» вверх`}
              className="text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed p-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronUp className="size-3.5" aria-hidden />
            </button>
          )}
          {!isPinned && (
            <button
              type="button"
              onClick={onMoveDown}
              disabled={index >= lastIndex}
              aria-label={`Передвинуть «${label}» вниз`}
              className="text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed p-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ChevronDown className="size-3.5" aria-hidden />
            </button>
          )}
          {isPinned && (
            <span className="text-primary p-0.5" title="Закреплённая колонка — всегда первая слева">
              <Pin className="size-3.5 fill-current" aria-hidden />
            </span>
          )}
        </span>
      )}
    </li>
  );
}

// ── DynamicFieldCell ──────────────────────────────────────────────────────────

function DynamicFieldCell({ field, row }: { field: CustomField; row: Document }) {
  const val = row.custom_field_values?.[field.key] ?? null;

  if (val === null || val === undefined) {
    return <span className="text-muted-foreground text-xs">—</span>;
  }

  if (field.type === "number") {
    return (
      <span className="font-mono text-xs tabular-nums text-right block">
        {typeof val === "number" ? val.toLocaleString("ru-RU") : String(val)}
      </span>
    );
  }

  if (field.type === "date") {
    return <span className="font-mono text-xs tabular-nums">{formatDate(String(val))}</span>;
  }

  if (field.type === "enum") {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
        {String(val)}
      </span>
    );
  }

  // text
  return (
    <span className="truncate max-w-[200px] block text-xs" title={String(val)}>
      {String(val)}
    </span>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function RegistrySkeleton() {
  return (
    <div
      className="flex flex-col h-full"
      aria-busy="true"
      role="status"
      aria-label="Загрузка реестра..."
    >
      {/* Toolbar skeleton */}
      <div className="flex items-center gap-2 px-4 py-3 border-b">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-8 w-28" />
        <div className="ml-auto flex gap-1">
          <Skeleton className="h-8 w-20" />
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-8 w-28" />
        </div>
      </div>
      {/* Table header skeleton */}
      <div className="flex gap-0 border-b px-3 py-2.5">
        {[40, 180, 120, 121, 110, 130, 90].map((w) => (
          <Skeleton key={w} className="h-4 mr-3" style={{ width: `${w}px` }} />
        ))}
      </div>
      {/* Row skeletons — 12 rows matching final shape */}
      {Array.from({ length: 12 }, (_, i) => i).map((i) => (
        <div key={i} className="flex items-center gap-3 px-3 py-3 border-b">
          <Skeleton className="h-4 w-4 shrink-0" />
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-5 w-16 rounded-full" />
        </div>
      ))}
    </div>
  );
}

// ── Date helpers ───────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return format(parseISO(iso), "dd.MM.yyyy", { locale: ru });
  } catch {
    return iso;
  }
}
