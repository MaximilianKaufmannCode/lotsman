// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FKResponsibleFilterPopover — responsible user filter with special options.
 *
 * Design §4.4.b:
 * - "Я" (me) — single-select, mutex with others
 * - "Не назначен" — mutex with others
 * - User list — typeahead (future: GET /api/v1/auth/users?q=)
 *   V1 simplification: uses the auth claims for "me" + hardcoded special options.
 *   Full user typeahead is out of scope if /api/v1/auth/users endpoint isn't declared
 *   — this is backed by a simple radio-group for V1 (matching sidebar pattern).
 */

import * as React from "react";
import { useAuth } from "@/features/auth/AuthProvider";
import { cn } from "@/shared/lib/cn";
import { FilterPopoverFrame } from "./FilterPopoverFrame";

interface FKResponsibleFilterPopoverProps {
  columnId: string;
  columnLabel: string;
  /** Currently committed value: 'me' | 'unassigned' | UUID | undefined */
  value: string | undefined;
  onApply: (value: string | undefined) => void;
  onClose: () => void;
}

type ResponsibleOption = "any" | "me" | "unassigned";

export function FKResponsibleFilterPopover({
  columnId,
  columnLabel,
  value,
  onApply,
  onClose,
}: FKResponsibleFilterPopoverProps) {
  const { claims } = useAuth();

  const initOption: ResponsibleOption =
    value === "me" ? "me" : value === "unassigned" ? "unassigned" : "any";

  const [selected, setSelected] = React.useState<ResponsibleOption>(initOption);

  const handleApply = () => {
    if (selected === "any") {
      onApply(undefined);
    } else {
      onApply(selected);
    }
  };

  const handleReset = () => {
    setSelected("any");
  };

  const options: { value: ResponsibleOption; label: string; description?: string }[] = [
    { value: "any", label: "Любой" },
    {
      value: "me",
      label: "Я",
      description: claims?.email ?? "текущий пользователь",
    },
    { value: "unassigned", label: "Не назначен" },
  ];

  return (
    <FilterPopoverFrame
      id={`fk-responsible-filter-${columnId}`}
      columnLabel={columnLabel}
      onApply={handleApply}
      onReset={handleReset}
      onClose={onClose}
    >
      <div
        role="radiogroup"
        aria-label="Фильтр по ответственному"
        className="space-y-1.5"
      >
        {options.map((opt) => (
          <label
            key={opt.value}
            className={cn(
              "flex items-start gap-2 rounded px-2 py-1.5 cursor-pointer text-xs",
              "hover:bg-muted",
              selected === opt.value && "bg-primary/5",
            )}
          >
            <input
              type="radio"
              name={`responsible-${columnId}`}
              value={opt.value}
              checked={selected === opt.value}
              onChange={() => setSelected(opt.value)}
              className="mt-0.5 focus-visible:ring-2 focus-visible:ring-ring"
            />
            <div>
              <span className="font-medium">{opt.label}</span>
              {opt.description && (
                <p className="text-muted-foreground text-[10px] mt-0.5">{opt.description}</p>
              )}
            </div>
          </label>
        ))}
      </div>
    </FilterPopoverFrame>
  );
}
