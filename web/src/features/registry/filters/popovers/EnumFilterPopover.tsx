// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * EnumFilterPopover — multi-select checkboxes for enum columns.
 *
 * Used for: doc_status (Статус документа), status (urgency), custom enum cf_ fields.
 * Design §4.5.
 *
 * - If ≤ 8 options: no search bar.
 * - If > 8 options: search input shown.
 * - For custom enum fields: loads distinct-values from server (options from schema).
 */

import { Search } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";
import { Input } from "@/shared/ui/input";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

const SEARCH_THRESHOLD = 8;

interface EnumOption {
  value: string;
  label: string;
}

interface EnumFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  options: EnumOption[];
  /** Currently committed values */
  value: string[] | undefined;
  onApply: (value: string[] | undefined) => void;
  onClose: () => void;
  /** v1.24.5 — radio-style (single value max). Used for urgency `status` filter
   *  where the backend query-param accepts only one value. */
  singleSelect?: boolean;
}

export function EnumFilterPopover({
  columnId,
  columnLabel,
  options,
  value,
  onApply,
  onClose,
  singleSelect = false,
}: EnumFilterPopoverProps) {
  const [selected, setSelected] = React.useState<string[]>(value ?? []);
  const [search, setSearch] = React.useState("");

  const showSearch = options.length > SEARCH_THRESHOLD;

  const filteredOptions = React.useMemo(() => {
    if (!search) return options;
    const q = search.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, search]);

  const toggle = (v: string) => {
    setSelected((prev) => {
      if (singleSelect) {
        // Radio-like: clicking the same value clears; clicking another replaces.
        return prev.includes(v) ? [] : [v];
      }
      return prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v];
    });
  };

  const handleApply = () => {
    onApply(selected.length > 0 ? selected : undefined);
  };

  const handleReset = () => {
    setSelected([]);
    setSearch("");
  };

  return (
    <FilterPopoverFrame
      id={`enum-filter-${columnId}`}
      columnLabel={columnLabel}
      onApply={handleApply}
      onReset={handleReset}
      onClose={onClose}
    >
      {showSearch && (
        <div className="relative mb-2">
          <Search
            className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground pointer-events-none"
            aria-hidden
          />
          <Input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск..."
            aria-label="Поиск по значениям"
            className="pl-6 text-xs h-7"
          />
        </div>
      )}

      <div
        role={singleSelect ? "radiogroup" : "listbox"}
        aria-multiselectable={singleSelect ? undefined : true}
        aria-label={`Значения фильтра ${columnLabel}`}
        className="space-y-0.5"
      >
        {filteredOptions.length === 0 ? (
          <p className="text-xs text-muted-foreground py-2">Нет совпадений</p>
        ) : (
          filteredOptions.map((opt) => {
            const isChecked = selected.includes(opt.value);
            return (
              <label
                key={opt.value}
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1.5 text-xs cursor-pointer",
                  "hover:bg-muted",
                )}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(opt.value)}
                  className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={opt.label}
                />
                <span className="flex-1">{opt.label}</span>
              </label>
            );
          })
        )}
      </div>

      {selected.length > 0 && (
        <p className="text-xs text-muted-foreground mt-2">
          Выбрано: {selected.length} из {options.length}
        </p>
      )}
    </FilterPopoverFrame>
  );
}
