// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * DoctypeFilterPopover — multi-select for document type codes.
 *
 * Uses useDocumentTypes() for server-loaded list.
 * Inline notice: selecting type(s) changes visible custom columns.
 * Design §4.6.
 */

import { Search } from "lucide-react";
import * as React from "react";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { cn } from "@/shared/lib/cn";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

interface DoctypeFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  /** Currently committed type codes */
  value: string[] | undefined;
  onApply: (value: string[] | undefined) => void;
  onClose: () => void;
}

const SEARCH_THRESHOLD = 8;

export function DoctypeFilterPopover({
  columnId,
  columnLabel,
  value,
  onApply,
  onClose,
}: DoctypeFilterPopoverProps) {
  const { data: docTypes = [], isLoading } = useDocumentTypes();
  const [selected, setSelected] = React.useState<string[]>(value ?? []);
  const [search, setSearch] = React.useState("");

  const showSearch = docTypes.length > SEARCH_THRESHOLD;

  const filteredTypes = React.useMemo(() => {
    if (!search) return docTypes;
    const q = search.toLowerCase();
    return docTypes.filter(
      (dt) =>
        dt.display_name.toLowerCase().includes(q) || dt.code.toLowerCase().includes(q),
    );
  }, [docTypes, search]);

  const toggle = (code: string) => {
    setSelected((prev) =>
      prev.includes(code) ? prev.filter((x) => x !== code) : [...prev, code],
    );
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
      id={`doctype-filter-${columnId}`}
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
            aria-label="Поиск по типам документов"
            className="pl-6 text-xs h-7"
          />
        </div>
      )}

      <div
        role="listbox"
        aria-multiselectable="true"
        aria-label="Типы документов"
        className="space-y-0.5 max-h-48 overflow-y-auto mb-2"
      >
        {isLoading && (
          <>
            <Skeleton className="h-6 w-full rounded" />
            <Skeleton className="h-6 w-3/4 rounded" />
            <Skeleton className="h-6 w-4/5 rounded" />
          </>
        )}
        {!isLoading && filteredTypes.length === 0 && (
          <p className="text-xs text-muted-foreground py-2">Нет совпадений</p>
        )}
        {!isLoading &&
          filteredTypes.map((dt) => {
            const isChecked = selected.includes(dt.code);
            return (
              <label
                key={dt.code}
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1.5 text-xs cursor-pointer",
                  "hover:bg-muted",
                )}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(dt.code)}
                  className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={dt.display_name}
                />
                <span className="flex-1 truncate">{dt.display_name}</span>
              </label>
            );
          })}
      </div>

      {/* Inline notice about custom columns changing */}
      <div
        className="rounded bg-muted/50 px-2.5 py-2 text-xs text-muted-foreground"
        role="note"
      >
        При изменении типа меняется набор custom-полей в таблице.
      </div>

      {selected.length > 0 && (
        <p className="text-xs text-muted-foreground mt-2">
          Выбрано: {selected.length} из {docTypes.length}
        </p>
      )}
    </FilterPopoverFrame>
  );
}
