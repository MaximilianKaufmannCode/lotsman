// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FKAssetFilterPopover — typeahead + multi-select for asset (counterparty) filter.
 *
 * Design §4.4.a.
 * Server search: GET /api/v1/assets?q=<input>&limit=50.
 * Pre-loads top-50 on first open (no query).
 * Multi-select — selected shown at top.
 */

import { Search } from "lucide-react";
import * as React from "react";
import { useAssets } from "@/features/registry/hooks/useAssets";
import { cn } from "@/shared/lib/cn";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

interface FKAssetFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  /** Currently committed asset UUID array */
  value: string[] | undefined;
  onApply: (value: string[] | undefined) => void;
  onClose: () => void;
}

export function FKAssetFilterPopover({
  columnId,
  columnLabel,
  value,
  onApply,
  onClose,
}: FKAssetFilterPopoverProps) {
  const [selected, setSelected] = React.useState<string[]>(value ?? []);
  const [query, setQuery] = React.useState("");
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 200);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  const { data: assetsData, isLoading } = useAssets({
    ...(debouncedQuery ? { q: debouncedQuery } : {}),
    page_size: 50,
  });

  const allAssets = assetsData?.items ?? [];

  const toggle = (id: string) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  // Selected assets appear first in list
  const orderedAssets = React.useMemo(() => {
    const selectedItems = allAssets.filter((a) => selected.includes(a.id));
    const rest = allAssets.filter((a) => !selected.includes(a.id));
    return [...selectedItems, ...rest];
  }, [allAssets, selected]);

  const handleApply = () => {
    onApply(selected.length > 0 ? selected : undefined);
  };

  const handleReset = () => {
    setSelected([]);
    setQuery("");
  };

  return (
    <FilterPopoverFrame
      id={`fk-asset-filter-${columnId}`}
      columnLabel={columnLabel}
      onApply={handleApply}
      onReset={handleReset}
      onClose={onClose}
    >
      <div className="relative mb-2">
        <Search
          className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground pointer-events-none"
          aria-hidden
        />
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Найти компанию..."
          aria-label="Поиск компании"
          className="pl-6 text-xs h-8"
        />
      </div>

      <div
        role="listbox"
        aria-multiselectable="true"
        aria-label="Компании"
        className="space-y-0.5 max-h-48 overflow-y-auto"
      >
        {isLoading && (
          <>
            <Skeleton className="h-8 w-full rounded" />
            <Skeleton className="h-8 w-4/5 rounded" />
            <Skeleton className="h-8 w-3/5 rounded" />
          </>
        )}
        {!isLoading && allAssets.length === 0 && (
          <p className="text-xs text-muted-foreground py-2">Ничего не найдено</p>
        )}
        {!isLoading &&
          orderedAssets.map((asset) => {
            const isChecked = selected.includes(asset.id);
            return (
              <label
                key={asset.id}
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1.5 cursor-pointer",
                  "hover:bg-muted",
                  isChecked && "bg-primary/5",
                )}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(asset.id)}
                  className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={asset.name}
                />
                <div className="min-w-0">
                  <p className="text-xs font-medium truncate">{asset.name}</p>
                  {asset.inn && (
                    <p className="text-[10px] text-muted-foreground">ИНН {asset.inn}</p>
                  )}
                </div>
              </label>
            );
          })}
      </div>

      {selected.length > 0 && (
        <p className="text-xs text-muted-foreground mt-1.5">
          Выбрано: {selected.length}
        </p>
      )}
    </FilterPopoverFrame>
  );
}
