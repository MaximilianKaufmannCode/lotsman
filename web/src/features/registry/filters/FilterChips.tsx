// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FilterChips — horizontal bar of active filter condition chips.
 *
 * - Shows one chip per active filter condition.
 * - Each chip: label + value + ✕ button.
 * - Overflow >5: "+N ещё ▾" popover with the rest.
 * - "Очистить всё" rightmost.
 * - Click chip text → open Sheet and focus that field.
 * - Click ✕ → remove that condition from URL instantly.
 * - Keyboard: Backspace/Delete on ✕ = remove; Enter on text = open Sheet.
 *
 * Design: the design spec §8
 * A11y: role="region", aria-live="polite", each chip button has aria-label.
 */

import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import { ChevronDown, X } from "lucide-react";
import * as React from "react";
import type { FilterKey, RegistrySearch } from "@/features/registry/hooks/useUrlState";
import { cn } from "@/shared/lib/cn";

// ── Chip descriptor ───────────────────────────────────────────────────────────

interface ChipInfo {
  /** Which filter key this chip represents */
  filterKey: FilterKey;
  /** Short label for prefix */
  label: string;
  /** Short display value */
  value: string;
  /** Full value for title/aria (may be truncated in value) */
  fullValue: string;
  /**
   * For cfFilters chips: the specific custom field key within cfFilters
   * (e.g. "yurisdikciya" for cf_yurisdikciya). Used in onRemove handler.
   */
  cfKey?: string;
}

// ── Date formatting helpers ───────────────────────────────────────────────────

function fmtDate(iso: string): string {
  try {
    return format(parseISO(iso), "dd.MM.yyyy", { locale: ru });
  } catch {
    return iso;
  }
}

// ── Build chip list from URL search ──────────────────────────────────────────

export function buildChips(search: RegistrySearch): ChipInfo[] {
  const chips: ChipInfo[] = [];

  // Type codes (new multi-value) — takes precedence over legacy type_code
  if (search.type_codes && search.type_codes.length > 0) {
    const display =
      search.type_codes.length === 1
        ? (search.type_codes[0] ?? "")
        : `${search.type_codes.slice(0, 2).join(", ")}${search.type_codes.length > 2 ? ` +${search.type_codes.length - 2}` : ""}`;
    chips.push({
      filterKey: "type_codes",
      label: "Тип",
      value: display,
      fullValue: search.type_codes.join(", "),
    });
  } else if (search.type_code) {
    chips.push({
      filterKey: "type_code",
      label: "Тип",
      value: search.type_code,
      fullValue: search.type_code,
    });
  }

  // Asset ids
  if (search.asset_ids && search.asset_ids.length > 0) {
    const display =
      search.asset_ids.length === 1
        ? `${search.asset_ids[0]?.slice(0, 8)}…`
        : `${search.asset_ids.length} контрагентов`;
    chips.push({
      filterKey: "asset_ids",
      label: "Контрагент",
      value: display,
      fullValue: search.asset_ids.join(", "),
    });
  } else if (search.asset_id) {
    chips.push({
      filterKey: "asset_id",
      label: "Контрагент",
      value: `${search.asset_id.slice(0, 8)}…`,
      fullValue: search.asset_id,
    });
  }

  // Responsible
  if (search.responsible) {
    const display =
      search.responsible === "me"
        ? "Я"
        : search.responsible === "unassigned"
          ? "Не назначен"
          : `${search.responsible.slice(0, 8)}…`;
    chips.push({
      filterKey: "responsible",
      label: "Ответственный",
      value: display,
      fullValue: search.responsible,
    });
  }

  // Urgency status — v1.25.5: multi-select array
  if (search.status && search.status.length > 0) {
    const labels: Record<string, string> = {
      ok: "ОК",
      soon: "Скоро",
      overdue: "Просрочено",
      archived: "В архиве",
    };
    chips.push({
      filterKey: "status",
      label: "Статус",
      value: search.status.map((s) => labels[s] ?? s).join(", "),
      fullValue: search.status.join(", "),
    });
  }

  // Expiry range / perpetual
  if (search.expiry_perpetual) {
    chips.push({
      filterKey: "expiry_perpetual",
      label: "Действ. до",
      value: "Бессрочные",
      fullValue: "expiry_date IS NULL",
    });
  } else if (search.expiry_from || search.expiry_to) {
    const from = search.expiry_from ? fmtDate(search.expiry_from) : null;
    const to = search.expiry_to ? fmtDate(search.expiry_to) : null;
    const display = from && to ? `${from} — ${to}` : from ? `с ${from}` : `до ${to ?? ""}`;
    // Represent as a single chip; removing it clears both
    chips.push({
      filterKey: "expiry_from",
      label: "Действ. до",
      value: display,
      fullValue: display,
    });
  }

  // Jurisdiction
  if (search.jurisdiction && search.jurisdiction.length > 0) {
    const display =
      search.jurisdiction.length <= 2
        ? search.jurisdiction.join(", ")
        : `${search.jurisdiction.slice(0, 2).join(", ")} +${search.jurisdiction.length - 2}`;
    chips.push({
      filterKey: "jurisdiction",
      label: "Юрисдикция",
      value: display,
      fullValue: search.jurisdiction.join(", "),
    });
  }

  // v1.24.4 — sidebar «Статус жизненного цикла контрагента» удалён.
  // Колонка «Активность» в таблице фильтруется через воронку в её шапке
  // (per-column popover на cf_aktivnost), чипы рендерятся через cfFilters ниже.

  // INN
  if (search.inn) {
    chips.push({
      filterKey: "inn",
      label: "ИНН",
      value: search.inn,
      fullValue: search.inn,
    });
  }

  // Number — v1.25.6 also covers «— Не задано» tick from column funnel.
  if (search.number || search.number_is_null) {
    const parts: string[] = [];
    if (search.number) parts.push(search.number);
    if (search.number_is_null) parts.push("— Не задано");
    chips.push({
      filterKey: "number",
      label: "№",
      value: parts.join(", "),
      fullValue: parts.join(", "),
    });
  }

  // Updated range
  if (search.updated_from || search.updated_to) {
    const from = search.updated_from ? fmtDate(search.updated_from.slice(0, 10)) : null;
    const to = search.updated_to ? fmtDate(search.updated_to.slice(0, 10)) : null;
    const display = from && to ? `${from} — ${to}` : from ? `с ${from}` : `до ${to ?? ""}`;
    chips.push({
      filterKey: "updated_from",
      label: "Изменён",
      value: display,
      fullValue: display,
    });
  }

  // Doc status
  if (search.doc_status && search.doc_status.length > 0) {
    // Only show chip if non-default (just "active" is the default — skip)
    const isDefault = search.doc_status.length === 1 && search.doc_status[0] === "active";
    if (!isDefault) {
      const labels: Record<string, string> = { active: "Активные", archived: "Архивные" };
      chips.push({
        filterKey: "doc_status",
        label: "Статус документа",
        value: search.doc_status.map((s) => labels[s] ?? s).join(", "),
        fullValue: search.doc_status.join(", "),
      });
    }
  } else if (search.show_archived) {
    chips.push({
      filterKey: "show_archived",
      label: "Статус документа",
      value: "Включая архивные",
      fullValue: "show_archived=true",
    });
  }

  // Custom field filters (cfFilters) — one chip per active cf_ key
  if (search.cfFilters) {
    for (const [cfKey, cfValue] of Object.entries(search.cfFilters)) {
      if (!cfValue) continue;
      // Display value: truncate CSV list to first 2 items; replace __NULL__
      // sentinel with «Не задано» label (v1.24.6 — "missing/empty" filter).
      const parts = cfValue.split(",").filter(Boolean).map((p) =>
        p === "__NULL__" ? "Не задано" : p,
      );
      const display =
        parts.length <= 2
          ? parts.join(", ")
          : `${parts.slice(0, 2).join(", ")} +${parts.length - 2}`;
      chips.push({
        filterKey: "cfFilters",
        cfKey,
        label: cfKey,
        value: display,
        fullValue: cfValue,
      });
    }
  }

  // v1.24.17 — Custom date range filters
  if (search.cfDateFilters) {
    for (const [cfKey, range] of Object.entries(search.cfDateFilters)) {
      if (!range) continue;
      const parts: string[] = [];
      if (range.isNull) {
        parts.push("Не задано");
      } else {
        if (range.from && range.to) parts.push(`${range.from} … ${range.to}`);
        else if (range.from) parts.push(`от ${range.from}`);
        else if (range.to) parts.push(`до ${range.to}`);
      }
      if (parts.length === 0) continue;
      const display = parts.join("");
      chips.push({
        filterKey: "cfDateFilters",
        cfKey,
        label: cfKey,
        value: display,
        fullValue: display,
      });
    }
  }

  return chips;
}

// ── FilterChips component ─────────────────────────────────────────────────────

const VISIBLE_CHIPS_MAX = 5;

interface FilterChipsProps {
  search: RegistrySearch;
  /**
   * Called when a chip ✕ is clicked.
   * @param key         - The FilterKey to remove
   * @param cfKey       - For cfFilters chips: the specific custom field key to remove
   *                      (e.g. "yurisdikciya" to remove only cf_yurisdikciya, not all cfFilters)
   * @param extraKeys   - Additional keys to clear simultaneously (e.g. expiry_to when removing expiry_from)
   */
  onRemove: (key: FilterKey, cfKey?: string, extraKeys?: FilterKey[]) => void;
  onClearAll: () => void;
  /** Called when user clicks chip text — open Sheet and focus that field */
  onChipClick?: (key: FilterKey) => void;
  className?: string;
}

export function FilterChips({
  search,
  onRemove,
  onClearAll,
  onChipClick,
  className,
}: FilterChipsProps) {
  const chips = React.useMemo(() => buildChips(search), [search]);
  const [overflowOpen, setOverflowOpen] = React.useState(false);
  const overflowRef = React.useRef<HTMLDivElement>(null);

  // Close overflow on outside click
  React.useEffect(() => {
    if (!overflowOpen) return;
    const handler = (e: MouseEvent) => {
      if (!overflowRef.current?.contains(e.target as Node)) setOverflowOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [overflowOpen]);

  if (chips.length === 0) return null;

  const visible = chips.slice(0, VISIBLE_CHIPS_MAX);
  const overflow = chips.slice(VISIBLE_CHIPS_MAX);

  const handleRemove = (chip: ChipInfo) => {
    if (chip.filterKey === "expiry_from") {
      onRemove("expiry_from");
      onRemove("expiry_to");
    } else if (chip.filterKey === "updated_from") {
      onRemove("updated_from");
      onRemove("updated_to");
    } else if (chip.filterKey === "number") {
      // v1.25.6 — single chip covers both `number` and `number_is_null`
      onRemove("number");
      onRemove("number_is_null");
    } else if (chip.filterKey === "cfFilters" && chip.cfKey) {
      onRemove("cfFilters", chip.cfKey);
    } else if (chip.filterKey === "cfDateFilters" && chip.cfKey) {
      onRemove("cfDateFilters", chip.cfKey);
    } else {
      onRemove(chip.filterKey);
    }
  };

  return (
    <section
      aria-label="Активные фильтры"
      aria-live="polite"
      className={cn(
        "flex flex-wrap items-center gap-1.5 px-4 py-2 border-b bg-background/50",
        className,
      )}
    >
      {visible.map((chip) => (
        <FilterChip
          key={chip.filterKey}
          chip={chip}
          onRemove={() => handleRemove(chip)}
          onClick={() => onChipClick?.(chip.filterKey)}
        />
      ))}

      {/* Overflow */}
      {overflow.length > 0 && (
        <div ref={overflowRef} className="relative">
          <button
            type="button"
            onClick={() => setOverflowOpen((v) => !v)}
            aria-expanded={overflowOpen}
            aria-label={`Ещё ${overflow.length} условий`}
            className={cn(
              "inline-flex items-center gap-1 h-7 px-2 rounded-full border text-xs",
              "bg-secondary text-foreground hover:bg-muted",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            +{overflow.length} ещё
            <ChevronDown className="size-3" aria-hidden />
          </button>
          {overflowOpen && (
            <div className="absolute top-full left-0 mt-1 z-50 rounded border bg-popover shadow-md p-2 flex flex-wrap gap-1.5 max-w-72">
              {overflow.map((chip) => (
                <FilterChip
                  key={chip.filterKey}
                  chip={chip}
                  onRemove={() => {
                    handleRemove(chip);
                    if (overflow.length <= 1) setOverflowOpen(false);
                  }}
                  onClick={() => {
                    onChipClick?.(chip.filterKey);
                    setOverflowOpen(false);
                  }}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Clear all */}
      <button
        type="button"
        onClick={onClearAll}
        className={cn(
          "ml-auto text-xs text-muted-foreground underline-offset-2 hover:underline hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded",
        )}
      >
        Очистить всё
      </button>
    </section>
  );
}

// ── Single chip ───────────────────────────────────────────────────────────────

function FilterChip({
  chip,
  onRemove,
  onClick,
}: {
  chip: ChipInfo;
  onRemove: () => void;
  onClick: () => void;
}) {
  const displayValue = chip.value.length > 30 ? `${chip.value.slice(0, 28)}…` : chip.value;

  return (
    <span className="inline-flex items-center h-7 rounded-full border bg-secondary text-sm overflow-hidden">
      {/* Chip text — click to open Sheet and focus that field */}
      <button
        type="button"
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === "Delete" || e.key === "Backspace") {
            e.preventDefault();
            onRemove();
          }
        }}
        title={chip.fullValue !== chip.value ? chip.fullValue : undefined}
        className={cn(
          "pl-2.5 pr-1 py-0 text-xs",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <span className="text-muted-foreground">{chip.label}:</span>{" "}
        <span className="text-foreground font-medium">{displayValue}</span>
      </button>

      {/* Remove ✕ */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        aria-label={`Удалить условие ${chip.label}: ${chip.value}`}
        className={cn(
          "h-7 w-7 flex items-center justify-center shrink-0",
          "text-muted-foreground hover:text-foreground hover:bg-muted/60",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <X className="size-3" aria-hidden />
      </button>
    </span>
  );
}
