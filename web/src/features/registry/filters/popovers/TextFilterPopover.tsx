// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * TextFilterPopover — free-text + distinct-values multi-select popover.
 *
 * Used for: number (№ документа), custom text fields (cf_*).
 *
 * UX decisions (per design §4.1):
 * - Server distinct-values top-100 loaded lazily on first open (useDistinctValues).
 * - Free-text input: debounce 200ms triggers client-side filter of already-loaded list.
 * - Mutex: if checkboxes are selected, free-text input is muted (disabled with tooltip).
 * - Apply: writes the chosen mode: either `{free_text: value}` or `{selected: [...]}`
 *   to the URL param. Backend interprets a single value as "contains".
 * - Empty distinct-values → shows only free-text input.
 * - Distinct-values error → shows warning + falls back to free-text only.
 */

import { AlertCircle, Search } from "lucide-react";
import * as React from "react";
import { useDistinctValues } from "@/features/registry/hooks/useDistinctValues";
import { cn } from "@/shared/lib/cn";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

interface TextFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  /** URL param field name — used as `field` in distinct-values query */
  fieldName: string;
  /** Current committed value (string = free-text, string[] = selected from list) */
  value: string | string[] | undefined;
  onApply: (value: string | string[] | undefined) => void;
  onClose: () => void;
}

export function TextFilterPopover({
  columnId,
  columnLabel,
  fieldName,
  value,
  onApply,
  onClose,
}: TextFilterPopoverProps) {
  // Initialize local draft from committed value
  const initText = typeof value === "string" ? value : "";
  const initSelected = Array.isArray(value) ? value : [];

  const [freeText, setFreeText] = React.useState(initText);
  const [selected, setSelected] = React.useState<string[]>(initSelected);
  const [searchQuery, setSearchQuery] = React.useState("");
  const searchDebounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedQuery, setDebouncedQuery] = React.useState("");

  React.useEffect(() => {
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => setDebouncedQuery(searchQuery), 200);
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [searchQuery]);

  // Distinct values — loaded once on open
  const {
    data: distinctData,
    isLoading,
    isError,
    refetch,
  } = useDistinctValues({ field: fieldName, limit: 100 }, { enabled: true });

  const allValues = distinctData?.values ?? [];
  const nullCount = distinctData?.null_count ?? 0;

  // Client-side filter by debouncedQuery
  const filteredValues = React.useMemo(() => {
    if (!debouncedQuery) return allValues;
    const q = debouncedQuery.toLowerCase();
    return allValues.filter((v) => v.value.toLowerCase().includes(q));
  }, [allValues, debouncedQuery]);

  // Has free-text been entered (length > 0)?
  const hasFreeText = freeText.trim().length > 0;
  // Has any checkboxes selected?
  const hasSelected = selected.length > 0;

  // Mutex: if selected, mute free-text; if free-text, show hint
  const freeTextDisabled = hasSelected;
  const checkboxesDisabled = hasFreeText;

  const toggleValue = (v: string) => {
    if (checkboxesDisabled) return;
    setSelected((prev) =>
      prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v],
    );
  };

  const handleApply = () => {
    if (hasSelected) {
      onApply(selected);
    } else if (hasFreeText) {
      onApply(freeText.trim());
    } else {
      onApply(undefined);
    }
  };

  const handleReset = () => {
    setFreeText("");
    setSelected([]);
    setSearchQuery("");
  };

  const isEmpty = !isLoading && !isError && allValues.length === 0;

  // Free-text "search: value" fallback when no match in list
  const showFreeTextFallback =
    debouncedQuery.length > 0 &&
    filteredValues.length === 0 &&
    !isError;

  return (
    <FilterPopoverFrame
      id={`text-filter-${columnId}`}
      columnLabel={columnLabel}
      onApply={handleApply}
      onReset={handleReset}
      onClose={onClose}
    >
      {/* Free-text input */}
      <div className="mb-2.5">
        <label
          htmlFor={`text-filter-input-${columnId}`}
          className="block text-xs text-muted-foreground mb-1"
        >
          Содержит:
        </label>
        <div className="relative">
          <Input
            id={`text-filter-input-${columnId}`}
            type="text"
            value={freeText}
            onChange={(e) => {
              if (!hasSelected) setFreeText(e.target.value);
            }}
            disabled={freeTextDisabled}
            aria-disabled={freeTextDisabled}
            placeholder="Например: ДГ-2026-001"
            className={cn("text-sm pr-8", freeTextDisabled && "opacity-50 cursor-not-allowed")}
            title={
              freeTextDisabled
                ? "Снимите выбранные значения ниже чтобы использовать ввод"
                : undefined
            }
          />
          {freeTextDisabled && (
            <span
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground"
              title="Ввод недоступен при выбранных значениях из списка"
            >
              —
            </span>
          )}
        </div>
        {hasFreeText && hasSelected && (
          <p className="text-xs text-muted-foreground mt-1">
            Выберите либо текст, либо значения из списка
          </p>
        )}
      </div>

      {/* Distinct values list */}
      {isError ? (
        <div className="flex flex-col gap-1.5 py-2">
          <div className="flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="size-3.5" aria-hidden />
            <span>Не удалось загрузить подсказки</span>
          </div>
          <button
            type="button"
            onClick={() => void refetch()}
            className="text-xs text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            Повторить
          </button>
        </div>
      ) : (
        <>
          {/* Search within list */}
          {!isEmpty && (
            <div className="mb-1.5">
              <p className="text-xs text-muted-foreground mb-1">
                {isEmpty ? "Нет данных" : "Часто встречающиеся значения:"}
              </p>
              {allValues.length > 5 && (
                <div className="relative mb-1.5">
                  <Search
                    className="absolute left-2 top-1/2 -translate-y-1/2 size-3 text-muted-foreground pointer-events-none"
                    aria-hidden
                  />
                  <Input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Поиск..."
                    aria-label="Поиск по значениям"
                    className="pl-6 text-xs h-7"
                  />
                </div>
              )}
            </div>
          )}

          <div
            role="listbox"
            aria-multiselectable="true"
            aria-label="Значения для выбора"
            className="space-y-0.5 max-h-40 overflow-y-auto"
          >
            {isLoading && (
              <>
                <Skeleton className="h-6 w-full rounded" />
                <Skeleton className="h-6 w-4/5 rounded" />
                <Skeleton className="h-6 w-3/5 rounded" />
              </>
            )}

            {!isLoading && isEmpty && (
              <p className="text-xs text-muted-foreground py-2">Нет данных</p>
            )}

            {/* v1.24.6 — «Не задано» option for docs where the field is missing/empty.
                Sentinel value "__NULL__" travels through cfFilters → backend filter. */}
            {!isLoading && !debouncedQuery && nullCount > 0 && (
              <label
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1 text-xs cursor-pointer italic",
                  "hover:bg-muted",
                  checkboxesDisabled && "opacity-50 cursor-not-allowed",
                )}
                title={
                  checkboxesDisabled
                    ? "Очистите поле «Содержит» выше"
                    : "Документы, у которых это поле не заполнено"
                }
              >
                <input
                  type="checkbox"
                  checked={selected.includes("__NULL__")}
                  disabled={checkboxesDisabled}
                  onChange={() => toggleValue("__NULL__")}
                  className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Не задано (${nullCount})`}
                />
                <span className="flex-1 truncate text-muted-foreground">— Не задано</span>
                <span className="text-muted-foreground shrink-0">({nullCount})</span>
              </label>
            )}

            {!isLoading &&
              filteredValues.map((item) => {
                const isChecked = selected.includes(item.value);
                return (
                  <label
                    key={item.value}
                    className={cn(
                      "flex items-center gap-2 rounded px-2 py-1 text-xs cursor-pointer",
                      "hover:bg-muted",
                      checkboxesDisabled && "opacity-50 cursor-not-allowed",
                    )}
                    title={checkboxesDisabled ? "Очистите поле «Содержит» выше" : undefined}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      disabled={checkboxesDisabled}
                      onChange={() => toggleValue(item.value)}
                      className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`${item.value} (${item.count})`}
                    />
                    <span className="flex-1 truncate">{item.value}</span>
                    <span className="text-muted-foreground shrink-0">({item.count})</span>
                  </label>
                );
              })}

            {/* Free-text fallback when no match in list */}
            {showFreeTextFallback && (
              <button
                type="button"
                onClick={() => {
                  setFreeText(debouncedQuery);
                  setSearchQuery("");
                }}
                className={cn(
                  "w-full text-left rounded px-2 py-1 text-xs",
                  "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
              >
                <span className="text-muted-foreground">Искать: </span>
                <span className="font-medium">«{debouncedQuery}»</span>
              </button>
            )}
          </div>

          {/* Truncated hint */}
          {distinctData?.truncated && (
            <p className="text-xs text-muted-foreground mt-1.5">
              Показано {allValues.length} из {distinctData.total_distinct}
            </p>
          )}

          {/* Selection counter */}
          {selected.length > 0 && (
            <p className="text-xs text-muted-foreground mt-1.5">
              Выбрано: {selected.length}
            </p>
          )}
        </>
      )}
    </FilterPopoverFrame>
  );
}
