// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * DateFilterPopover — date range (from/to) + quick presets + optional perpetual checkbox.
 *
 * Used for: expiry_date (range + null), created_at/updated_at (range only),
 *           custom date fields (equality only in V1).
 *
 * Design spec §4.3 (the design spec).
 *
 * The popover operates with a local draft; writes to URL only on "Применить".
 * Custom date fields: V1 limitation — only equality (single DatePicker, range disabled).
 */

import { addDays, endOfQuarter, endOfYear, format, isAfter, parseISO, startOfQuarter, startOfYear, subDays } from "date-fns";
import * as React from "react";
import { cn } from "@/shared/lib/cn";
import { Input } from "@/shared/ui/input";
import type { RegistrySearch } from "../../hooks/useUrlState";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

interface DateFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  /**
   * 'system-range'    — full range + perpetual (for expiry_date, created_at, updated_at)
   * 'custom-equality' — legacy single-date equality (deprecated — kept for backward compat)
   * 'custom-range'    — v1.24.17 range mode for cf_date fields (from + to + null)
   */
  mode: "system-range" | "custom-equality" | "custom-range";
  /** Whether to show "Не задано" / "Только бессрочные" checkbox */
  supportsNull?: boolean;
  /** Current committed from/to/null from URL state */
  currentFrom?: string;
  currentTo?: string;
  currentNull?: boolean;
  onApply: (patch: Partial<RegistrySearch>) => void;
  onClose: () => void;
}

type QuickPeriod = "today" | "7d" | "30d" | "quarter" | "year" | "overdue";

function computeQuickPeriod(period: QuickPeriod): { from: string; to: string } {
  const today = new Date();
  const fmt = (d: Date) => format(d, "yyyy-MM-dd");
  switch (period) {
    case "today":
      return { from: fmt(today), to: fmt(today) };
    case "7d":
      return { from: fmt(today), to: fmt(addDays(today, 7)) };
    case "30d":
      return { from: fmt(today), to: fmt(addDays(today, 30)) };
    case "quarter":
      return { from: fmt(startOfQuarter(today)), to: fmt(endOfQuarter(today)) };
    case "year":
      return { from: fmt(startOfYear(today)), to: fmt(endOfYear(today)) };
    case "overdue":
      return { from: "", to: fmt(subDays(today, 1)) };
  }
}

function isActiveQuickPeriod(
  period: QuickPeriod,
  from: string,
  to: string,
): boolean {
  const { from: pf, to: pt } = computeQuickPeriod(period);
  return from === pf && to === pt;
}

export function DateFilterPopover({
  columnId,
  columnLabel,
  mode,
  supportsNull = false,
  currentFrom,
  currentTo,
  currentNull,
  onApply,
  onClose,
}: DateFilterPopoverProps) {
  const isCustomEquality = mode === "custom-equality";
  const isCustomRange = mode === "custom-range";

  const [from, setFrom] = React.useState(currentFrom ?? "");
  const [to, setTo] = React.useState(currentTo ?? "");
  const [nullFlag, setNullFlag] = React.useState(currentNull ?? false);
  // For custom equality: single date
  const [singleDate, setSingleDate] = React.useState(currentFrom ?? "");

  const rangeInvalid =
    !isCustomEquality &&
    from.length > 0 &&
    to.length > 0 &&
    (() => {
      try {
        return isAfter(parseISO(from), parseISO(to));
      } catch {
        return false;
      }
    })();

  const applyDisabled = rangeInvalid;

  const handleApply = () => {
    if (isCustomEquality) {
      // Custom date: equality only (legacy)
      onApply({ cfFilters: { [columnId.replace(/^cf_/, "")]: singleDate || "" } } as Partial<RegistrySearch>);
      return;
    }
    if (isCustomRange) {
      // v1.24.17 — custom date range. Writes to cfDateFilters[<key>].
      const cfKey = columnId.replace(/^cf_/, "");
      const entry: { from?: string; to?: string; isNull?: boolean } = {};
      if (nullFlag) {
        entry.isNull = true;
      } else {
        if (from) entry.from = from;
        if (to) entry.to = to;
      }
      const hasAny = entry.from || entry.to || entry.isNull;
      onApply({
        cfDateFilters: hasAny ? { [cfKey]: entry } : undefined,
      } as Partial<RegistrySearch>);
      return;
    }
    // system-range
    if (nullFlag) {
      onApply({ expiry_from: undefined, expiry_to: undefined, expiry_perpetual: true });
    } else {
      // Determine which param this column maps to
      onApply({
        expiry_from: columnId === "expiry_date" ? (from || undefined) : undefined,
        expiry_to: columnId === "expiry_date" ? (to || undefined) : undefined,
        expiry_perpetual: undefined,
        ...(columnId === "updated_at" || columnId === "created_at"
          ? { updated_from: from || undefined, updated_to: to || undefined }
          : {}),
      });
    }
  };

  const handleReset = () => {
    setFrom("");
    setTo("");
    setNullFlag(false);
    setSingleDate("");
  };

  const applyQuickPeriod = (period: QuickPeriod) => {
    const { from: f, to: t } = computeQuickPeriod(period);
    setFrom(f);
    setTo(t);
    setNullFlag(false);
  };

  if (isCustomEquality) {
    return (
      <FilterPopoverFrame
        id={`date-filter-${columnId}`}
        columnLabel={columnLabel}
        onApply={handleApply}
        onReset={handleReset}
        onClose={onClose}
      >
        <div className="mb-2">
          <label
            htmlFor={`date-eq-${columnId}`}
            className="block text-xs text-muted-foreground mb-1"
          >
            Равно:
          </label>
          <Input
            id={`date-eq-${columnId}`}
            type="date"
            value={singleDate}
            onChange={(e) => setSingleDate(e.target.value)}
            className="text-sm"
          />
          <p className="text-xs text-muted-foreground mt-1.5">
            Поиск по точной дате. Диапазон — в следующей версии.
          </p>
        </div>
      </FilterPopoverFrame>
    );
  }

  const QUICK_PERIODS: { key: QuickPeriod; label: string; forExpiry?: boolean }[] = [
    { key: "today", label: "Сегодня" },
    { key: "7d", label: "7 дней" },
    { key: "30d", label: "30 дней" },
    { key: "quarter", label: "Этот квартал" },
    { key: "year", label: "Год" },
    ...(supportsNull ? [{ key: "overdue" as QuickPeriod, label: "Просрочено", forExpiry: true }] : []),
  ];

  return (
    <FilterPopoverFrame
      id={`date-filter-${columnId}`}
      columnLabel={columnLabel}
      onApply={handleApply}
      onReset={handleReset}
      onClose={onClose}
      applyDisabled={applyDisabled}
    >
      {/* Range inputs */}
      {nullFlag && (
        <p className="text-xs text-muted-foreground mb-2 bg-muted/50 rounded px-2 py-1.5">
          Снимите «{isCustomRange ? "Не задано" : "Только бессрочные"}» чтобы выбрать диапазон.
        </p>
      )}

      <div className="grid grid-cols-2 gap-2 mb-2.5">
        <div>
          <label
            htmlFor={`date-from-${columnId}`}
            className="block text-xs text-muted-foreground mb-1"
          >
            От:
          </label>
          <Input
            id={`date-from-${columnId}`}
            type="date"
            value={from}
            disabled={nullFlag}
            aria-disabled={nullFlag}
            onChange={(e) => setFrom(e.target.value)}
            className="text-sm"
            title={nullFlag ? "Снимите «Только бессрочные» чтобы выбрать диапазон" : undefined}
          />
        </div>
        <div>
          <label
            htmlFor={`date-to-${columnId}`}
            className="block text-xs text-muted-foreground mb-1"
          >
            До:
          </label>
          <Input
            id={`date-to-${columnId}`}
            type="date"
            value={to}
            disabled={nullFlag}
            aria-disabled={nullFlag}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
            className="text-sm"
            title={nullFlag ? "Снимите «Только бессрочные» чтобы выбрать диапазон" : undefined}
          />
        </div>
      </div>

      {rangeInvalid && (
        <p className="text-xs text-destructive mb-2" role="alert">
          Дата «от» не может быть позже даты «до»
        </p>
      )}

      {/* Quick periods */}
      <div className="mb-2.5">
        <p className="text-xs text-muted-foreground mb-1.5">Быстрые периоды:</p>
        <div className="flex flex-wrap gap-1">
          {QUICK_PERIODS.map(({ key, label }) => {
            const isActive = !nullFlag && isActiveQuickPeriod(key, from, to);
            return (
              <button
                key={key}
                type="button"
                onClick={() => applyQuickPeriod(key)}
                className={cn(
                  "px-2 py-0.5 rounded text-xs border transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-secondary text-foreground border-border hover:bg-muted",
                )}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Perpetual checkbox — expiry_date only */}
      {supportsNull && (
        <>
          <hr className="border-border mb-2" />
          <label
            className={cn(
              "flex items-center gap-2 text-xs cursor-pointer",
              (from || to) ? "opacity-50" : "",
            )}
          >
            <input
              type="checkbox"
              checked={nullFlag}
              disabled={!!(from || to)}
              title={
                from || to
                  ? "Очистите даты выше чтобы выбрать бессрочные"
                  : undefined
              }
              onChange={(e) => {
                if (e.target.checked) {
                  setNullFlag(true);
                  setFrom("");
                  setTo("");
                } else {
                  setNullFlag(false);
                }
              }}
              className="rounded focus-visible:ring-2 focus-visible:ring-ring"
            />
            {isCustomRange ? "Не задано" : "Только бессрочные"}
          </label>
        </>
      )}
    </FilterPopoverFrame>
  );
}
