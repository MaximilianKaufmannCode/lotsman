// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ColumnFilterPopover — dispatcher: reads column meta and renders the correct
 * per-type filter popover content.
 *
 * This is a pure "factory" — it receives column metadata and current filter state,
 * and renders the appropriate popover component. The portal anchor is managed by
 * ColumnFilterButton.
 *
 * Custom field detection: if columnId starts with 'cf_', the fieldKey is
 * 'cfFilters.<key>' and the popover is driven by the CustomField schema type.
 */

import type { ColumnFilterType } from "@/features/registry/columnConfig";
import type { RegistrySearch } from "@/features/registry/hooks/useUrlState";
import { DateFilterPopover } from "./popovers/DateFilterPopover";
import { DoctypeFilterPopover } from "./popovers/DoctypeFilterPopover";
import { EnumFilterPopover } from "./popovers/EnumFilterPopover";
import { FKAssetFilterPopover } from "./popovers/FKAssetFilterPopover";
import { FKResponsibleFilterPopover } from "./popovers/FKResponsibleFilterPopover";
import { TextFilterPopover } from "./popovers/TextFilterPopover";

export interface ColumnFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  filterType: ColumnFilterType;
  fieldKey: string;
  enumOptions?: { value: string; label: string }[];
  supportsNull?: boolean;
  /** Current applied URL search state */
  search: RegistrySearch;
  onApply: (patch: Partial<RegistrySearch>) => void;
  onClose: () => void;
}

export function ColumnFilterPopover({
  columnId,
  columnLabel,
  filterType,
  fieldKey,
  enumOptions,
  supportsNull,
  search,
  onApply,
  onClose,
}: ColumnFilterPopoverProps) {
  const isCustomField = columnId.startsWith("cf_");
  const cfKey = isCustomField ? columnId.slice(3) : "";

  switch (filterType) {
    case "text": {
      // v1.24.9 — added expiry_date routing (system-array param).
      // v1.25.6 — added number_is_null sentinel preservation.
      const isExpiryDate = fieldKey === "expiry_date";
      const isNumber = fieldKey === "number";
      const distinctField = isCustomField ? columnId : fieldKey;
      const currentValue = isCustomField
        ? (search.cfFilters?.[cfKey] ?? undefined)
        : isExpiryDate
          ? (search.expiry_dates ?? undefined)
          : (search.number ?? undefined);

      // Parse committed value into TextFilterPopover form (string | string[]).
      let initValue: string | string[] | undefined;
      if (Array.isArray(currentValue)) {
        initValue = currentValue.length > 0 ? currentValue : undefined;
      } else if (currentValue) {
        initValue =
          typeof currentValue === "string" && currentValue.includes(",")
            ? currentValue.split(",")
            : currentValue;
      }
      // v1.25.6 — re-attach the __NULL__ sentinel if number_is_null is set,
      // so the popover renders the «— Не задано» checkbox already ticked.
      if (isNumber && search.number_is_null) {
        const arr = Array.isArray(initValue) ? initValue : initValue ? [initValue] : [];
        if (!arr.includes("__NULL__")) arr.push("__NULL__");
        initValue = arr;
      }

      return (
        <TextFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          fieldName={distinctField}
          value={initValue}
          onApply={(val) => {
            const isEmpty =
              val === undefined || val === "" || (Array.isArray(val) && val.length === 0);
            if (isEmpty) {
              if (isCustomField) {
                const { [cfKey]: _removed, ...rest } = search.cfFilters ?? {};
                onApply({ cfFilters: Object.keys(rest).length > 0 ? rest : undefined });
              } else if (isExpiryDate) {
                onApply({ expiry_dates: undefined });
              } else {
                onApply({ number: undefined });
              }
            } else if (isCustomField) {
              onApply({
                cfFilters: {
                  ...(search.cfFilters ?? {}),
                  [cfKey]: Array.isArray(val) ? val.join(",") : val,
                },
              });
            } else if (isExpiryDate) {
              // Multi-select array → expiry_dates list. Single string → wrap.
              const arr = Array.isArray(val) ? val : [val];
              onApply({ expiry_dates: arr });
            } else {
              // v1.25.6 — number field. The __NULL__ sentinel is split out
              // into the dedicated `number_is_null` URL param so the backend
              // knows whether the user wants empty/NULL rows (which would
              // otherwise be silently dropped — sending `?q=__NULL__` was
              // the original bug). Remaining textual values stay in `number`.
              const arr = Array.isArray(val) ? val : val ? [val] : [];
              const wantsNull = arr.includes("__NULL__");
              const textValues = arr.filter((v) => v !== "__NULL__");
              onApply({
                number: textValues.length > 0 ? textValues[0] : undefined,
                number_is_null: wantsNull ? true : undefined,
              });
            }
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "date-system": {
      let currentFrom: string | undefined;
      let currentTo: string | undefined;
      let currentNull: boolean | undefined;

      if (fieldKey === "expiry_date") {
        currentFrom = search.expiry_from;
        currentTo = search.expiry_to;
        currentNull = search.expiry_perpetual ?? false;
      } else if (fieldKey === "updated_at" || fieldKey === "created_at") {
        currentFrom = search.updated_from;
        currentTo = search.updated_to;
      }

      const nullSupported = !!(supportsNull && fieldKey === "expiry_date");
      return (
        <DateFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          mode="system-range"
          {...(nullSupported ? { supportsNull: true } : {})}
          {...(currentFrom !== undefined ? { currentFrom } : {})}
          {...(currentTo !== undefined ? { currentTo } : {})}
          {...(currentNull !== undefined ? { currentNull } : {})}
          onApply={(patch) => {
            // Route date range to correct URL params based on fieldKey
            if (fieldKey === "expiry_date") {
              onApply(patch);
            } else if (fieldKey === "updated_at" || fieldKey === "created_at") {
              // Re-map to updated_from/updated_to (v1.23.0 schema)
              const from = (patch as { expiry_from?: string }).expiry_from ?? patch.updated_from;
              const to = (patch as { expiry_to?: string }).expiry_to ?? patch.updated_to;
              onApply({ updated_from: from, updated_to: to });
            }
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "date": {
      // Custom date field — equality only (V1)
      const currentVal = search.cfFilters?.[cfKey];
      return (
        <DateFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          mode="custom-equality"
          {...(currentVal !== undefined ? { currentFrom: currentVal } : {})}
          onApply={(patch) => {
            // patch will have cfFilters.<key> = date
            const dateVal = patch.cfFilters?.[cfKey];
            if (!dateVal) {
              const { [cfKey]: _removed, ...rest } = search.cfFilters ?? {};
              onApply({ cfFilters: Object.keys(rest).length > 0 ? rest : undefined });
            } else {
              onApply({
                cfFilters: { ...(search.cfFilters ?? {}), [cfKey]: dateVal },
              });
            }
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "date-custom-range": {
      // v1.24.17 — schema-driven date range for any cf-date field.
      // Reads/writes cfDateFilters[<key>].
      const currentEntry = search.cfDateFilters?.[cfKey];
      const currentFrom = currentEntry?.from;
      const currentTo = currentEntry?.to;
      const currentNull = currentEntry?.isNull;
      return (
        <DateFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          mode="custom-range"
          supportsNull
          {...(currentFrom !== undefined ? { currentFrom } : {})}
          {...(currentTo !== undefined ? { currentTo } : {})}
          {...(currentNull !== undefined ? { currentNull } : {})}
          onApply={(patch) => {
            // patch may have cfDateFilters: {[key]: {...}} or undefined
            const next = patch.cfDateFilters as
              | Record<string, { from?: string; to?: string; isNull?: boolean }>
              | undefined;
            const incoming = next?.[cfKey];
            const current = search.cfDateFilters ?? {};
            if (!incoming) {
              // remove
              const { [cfKey]: _removed, ...rest } = current;
              onApply({
                cfDateFilters: Object.keys(rest).length > 0 ? rest : undefined,
              });
            } else {
              onApply({
                cfDateFilters: { ...current, [cfKey]: incoming },
              });
            }
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "fk-asset": {
      const currentIds = search.asset_ids ?? [];
      return (
        <FKAssetFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          value={currentIds.length > 0 ? currentIds : undefined}
          onApply={(val) => {
            onApply({ asset_ids: val });
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "fk-responsible": {
      return (
        <FKResponsibleFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          value={search.responsible}
          onApply={(val) => {
            onApply({ responsible: val });
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "enum": {
      const currentValues = fieldKey === "doc_status"
        ? (search.doc_status ?? [])
        : fieldKey === "status"
          ? (search.status ?? [])  // v1.25.5 — already array
          : (search.cfFilters?.[cfKey]?.split(",").filter(Boolean) ?? []);

      // For custom enum fields, get options from cfFilters or enumOptions prop
      const opts = enumOptions ?? [];

      // Custom enum from cf_: load distinct values if no static options provided
      if (isCustomField && opts.length === 0) {
        // Fall back to text popover for custom enums without static options
        return (
          <TextFilterPopover
            columnId={columnId}
            columnLabel={columnLabel}
            fieldName={columnId}
            value={currentValues.length > 0 ? currentValues : undefined}
            onApply={(val) => {
              if (val === undefined || (Array.isArray(val) && val.length === 0)) {
                const { [cfKey]: _removed, ...rest } = search.cfFilters ?? {};
                onApply({ cfFilters: Object.keys(rest).length > 0 ? rest : undefined });
              } else {
                onApply({
                  cfFilters: {
                    ...(search.cfFilters ?? {}),
                    [cfKey]: Array.isArray(val) ? val.join(",") : val,
                  },
                });
              }
              onClose();
            }}
            onClose={onClose}
          />
        );
      }

      return (
        <EnumFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          options={opts}
          value={currentValues.length > 0 ? currentValues : undefined}
          onApply={(val) => {
            if (fieldKey === "doc_status") {
              onApply({ doc_status: val });
            } else if (fieldKey === "status") {
              // v1.25.5 — urgency status now multi-select array
              onApply({
                status: (val && val.length > 0
                  ? (val as RegistrySearch["status"])
                  : undefined),
              });
            } else if (isCustomField) {
              if (!val || val.length === 0) {
                const { [cfKey]: _removed, ...rest } = search.cfFilters ?? {};
                onApply({ cfFilters: Object.keys(rest).length > 0 ? rest : undefined });
              } else {
                onApply({
                  cfFilters: { ...(search.cfFilters ?? {}), [cfKey]: val.join(",") },
                });
              }
            }
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    case "doctype": {
      return (
        <DoctypeFilterPopover
          columnId={columnId}
          columnLabel={columnLabel}
          value={search.type_codes}
          onApply={(val) => {
            onApply({ type_codes: val });
            onClose();
          }}
          onClose={onClose}
        />
      );
    }

    default:
      return null;
  }
}
