// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ColumnFilterButton — funnel icon trigger for per-column header filter popovers.
 *
 * Renders the Filter icon in the column header. When active, shows filled icon
 * + numeric badge. Click opens/closes the popover for this column.
 *
 * The popover itself is portal-rendered (createPortal → document.body) to escape
 * table's overflow:hidden and sticky positioning.
 *
 * A11y:
 * - aria-haspopup="dialog"
 * - aria-expanded
 * - aria-pressed (active state)
 * - aria-label describes column + state
 *
 * Design §3.2 (the design spec).
 */

import { Filter } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";
import type { ColumnFilterType } from "@/features/registry/columnConfig";
import type { RegistrySearch } from "@/features/registry/hooks/useUrlState";
import { cn } from "@/shared/lib/cn";

interface ColumnFilterButtonProps {
  columnId: string;
  columnLabel: string;
  filterType: ColumnFilterType;
  /** Number of active filter values for this column */
  activeCount: number;
  /** Whether the popover is currently open for this column */
  isOpen: boolean;
  /** Called when the button is clicked */
  onToggle: () => void;
  /** Called when the popover should close (Esc or outside click) */
  onClose: () => void;
  /** Render prop for the popover content — receives the button's DOM rect */
  renderPopover?: (anchorRect: DOMRect) => React.ReactNode;
}

export function ColumnFilterButton({
  columnId,
  columnLabel,
  filterType,
  activeCount,
  isOpen,
  onToggle,
  onClose,
  renderPopover,
}: ColumnFilterButtonProps) {
  const buttonRef = React.useRef<HTMLButtonElement>(null);
  const [anchorRect, setAnchorRect] = React.useState<DOMRect | null>(null);

  if (filterType === null || filterType === undefined) {
    return null;
  }

  const isActive = activeCount > 0;

  const ariaLabel = isActive
    ? `Фильтр колонки ${columnLabel}, активен, ${activeCount} ${activeCount === 1 ? "значение" : "значений"}`
    : `Фильтр колонки ${columnLabel}, не активен`;

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent bubbling to <th> onClick (which triggers sort)
    if (buttonRef.current) {
      setAnchorRect(buttonRef.current.getBoundingClientRect());
    }
    onToggle();
  };

  // Close on outside click (portal)
  React.useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      // Check if click is inside the popover portal
      const popoverEl = document.querySelector(`[data-filter-popover="${columnId}"]`);
      if (popoverEl?.contains(target)) return;
      onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [isOpen, columnId, onClose]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-pressed={isActive}
        data-testid={`column-filter-btn-${columnId}`}
        onClick={handleClick}
        className={cn(
          "inline-flex items-center justify-center",
          "w-7 h-7 rounded",
          "transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          // Inactive state: muted, low opacity
          !isActive && !isOpen && "text-muted-foreground opacity-60 hover:opacity-100 hover:bg-muted/50",
          // Active state: primary color
          isActive && !isOpen && "text-primary bg-primary/10 hover:bg-primary/20",
          // Open state
          isOpen && (isActive ? "text-primary bg-primary/20" : "text-muted-foreground bg-muted/50 opacity-100"),
        )}
      >
        <Filter
          className={cn(
            "size-3.5",
            isActive && "fill-current",
          )}
          aria-hidden
        />
        {isActive && (
          <span
            className={cn(
              "ml-0.5 inline-flex items-center justify-center",
              "rounded-full bg-primary text-primary-foreground",
              "text-[9px] font-semibold leading-none",
              "min-w-[14px] h-[14px] px-0.5",
            )}
            aria-hidden="true"
          >
            {activeCount}
          </span>
        )}
      </button>

      {/* Portal: popover anchored below the button.
          v1.24.16 — КРИТИЧНО: stopPropagation на click/mousedown.
          React events bubble через PORTAL (React-tree, не DOM-tree).
          Без этого click на checkbox внутри popover'а bubbl'ил вверх к
          <th>, триггерил TanStack Table'овский sort, и setSearch
          переписывал URL ДО того как Apply успевал сохранить выбор.
          Sortable колонки (asset_name / expiry_date / type_display_name /
          responsible_user_name / number) теряли весь фильтр. Cf-колонки
          выживали т.к. у них enableSorting:false. */}
      {isOpen && anchorRect && renderPopover &&
        createPortal(
          <div
            data-filter-popover={columnId}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            style={{
              position: "fixed",
              top: anchorRect.bottom + 4,
              left: Math.min(
                anchorRect.left,
                window.innerWidth - 328, // 320px popover + 8px margin
              ),
              zIndex: 9999,
            }}
          >
            {renderPopover(anchorRect)}
          </div>,
          document.body,
        )}
    </>
  );
}

// ── Hook: open popover state singleton ───────────────────────────────────────
// Ensures only one column popover is open at a time.

export interface OpenPopoverState {
  columnId: string | null;
  openColumn: (id: string) => void;
  closeColumn: () => void;
  toggleColumn: (id: string) => void;
  isOpen: (id: string) => boolean;
}

export function useOpenColumnPopover(): OpenPopoverState {
  const [openId, setOpenId] = React.useState<string | null>(null);

  const openColumn = React.useCallback((id: string) => setOpenId(id), []);
  const closeColumn = React.useCallback(() => setOpenId(null), []);
  const toggleColumn = React.useCallback(
    (id: string) => setOpenId((prev) => (prev === id ? null : id)),
    [],
  );
  const isOpen = React.useCallback((id: string) => openId === id, [openId]);

  return { columnId: openId, openColumn, closeColumn, toggleColumn, isOpen };
}

// ── ColumnFilterHeaderContent ─────────────────────────────────────────────────
// Convenience component that renders the header cell content:
// "<label> <ColumnFilterButton>"

interface ColumnFilterHeaderContentProps {
  columnId: string;
  /** The label to display — can be string or ReactNode (e.g. label + sort indicator) */
  label: React.ReactNode;
  /** Plain string label for aria attributes */
  columnLabel?: string;
  filterType: ColumnFilterType;
  activeCount: number;
  isOpen: boolean;
  onToggle: () => void;
  onClose: () => void;
  renderPopover?: (anchorRect: DOMRect) => React.ReactNode;
}

export function ColumnFilterHeaderContent({
  columnId,
  label,
  columnLabel,
  filterType,
  activeCount,
  isOpen,
  onToggle,
  onClose,
  renderPopover,
}: ColumnFilterHeaderContentProps) {
  const ariaLabel = columnLabel ?? (typeof label === "string" ? label : columnId);
  return (
    <div className="flex items-center gap-1 min-w-0">
      <span className="truncate flex-1">{label}</span>
      <ColumnFilterButton
        columnId={columnId}
        columnLabel={ariaLabel}
        filterType={filterType}
        activeCount={activeCount}
        isOpen={isOpen}
        onToggle={onToggle}
        onClose={onClose}
        {...(renderPopover !== undefined ? { renderPopover } : {})}
      />
    </div>
  );
}

// Re-export for external use
export type { ColumnFilterType };
export type { RegistrySearch };
