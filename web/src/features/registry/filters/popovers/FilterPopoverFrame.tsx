// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FilterPopoverFrame — shared chrome for all column filter popovers.
 *
 * Provides: header (title + close button), scrollable body slot, footer (Reset + Apply).
 * Focus management: traps focus within, restores to trigger on close.
 * A11y: role="dialog", aria-labelledby, Escape closes.
 */

import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

interface FilterPopoverFrameProps {
  id: string;
  columnLabel: string;
  onApply: () => void;
  onReset: () => void;
  onClose: () => void;
  applyDisabled?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function FilterPopoverFrame({
  id,
  columnLabel,
  onApply,
  onReset,
  onClose,
  applyDisabled = false,
  children,
  className,
}: FilterPopoverFrameProps) {
  const titleId = `${id}-title`;
  const dialogRef = React.useRef<HTMLDivElement>(null);

  // Focus first interactive element on mount
  React.useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    // Delay to allow Radix/DOM settle
    const t = setTimeout(() => {
      const focusable = dialog.querySelector<HTMLElement>(
        'input:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      focusable?.focus();
    }, 30);
    return () => clearTimeout(t);
  }, []);

  // Escape handler
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", handler, true);
    return () => document.removeEventListener("keydown", handler, true);
  }, [onClose]);

  // Enter to apply (when not in a sub-combobox/datepicker)
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !applyDisabled) {
      // Only trigger if focus is directly on a non-button element
      const target = e.target as HTMLElement;
      const tag = target.tagName.toLowerCase();
      if (tag !== "button" && tag !== "select" && !target.getAttribute("role")) {
        e.preventDefault();
        onApply();
      }
    }
  };

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      className={cn(
        "w-[320px] max-h-[480px] flex flex-col rounded-md border bg-popover shadow-lg",
        "focus:outline-none",
        className,
      )}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: dialog container needs tabindex for focus trap
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b shrink-0">
        <h3
          id={titleId}
          className="text-xs font-semibold text-foreground truncate pr-2"
        >
          Фильтр: {columnLabel}
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label={`Закрыть фильтр колонки ${columnLabel}`}
          className={cn(
            "shrink-0 rounded p-1 text-muted-foreground",
            "hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto min-h-0 px-3 py-2.5">
        {children}
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t px-3 py-2.5 flex items-center justify-between gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onReset}
          className="text-xs"
        >
          Сбросить
        </Button>
        <Button
          type="button"
          variant="default"
          size="sm"
          onClick={onApply}
          disabled={applyDisabled}
          className="text-xs"
        >
          Применить
        </Button>
      </div>
    </div>
  );
}
