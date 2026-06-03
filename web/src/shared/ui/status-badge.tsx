// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * StatusBadge — WCAG-compliant status indicator.
 * Always shows icon + text label. Never color alone.
 * Used in the document registry table and cards.
 */
import { AlertCircle, Archive, CheckCircle, Clock } from "lucide-react";
import { cn } from "@/shared/lib/cn";

export type DocumentStatus = "ok" | "soon" | "overdue" | "archived";

const variants = {
  ok: {
    Icon: CheckCircle,
    label: "ОК",
    cls: "bg-status-ok/10 text-status-ok",
  },
  soon: {
    Icon: Clock,
    label: "Скоро",
    cls: "bg-status-soon/10 text-status-soon",
  },
  overdue: {
    Icon: AlertCircle,
    label: "Просрочено",
    cls: "bg-status-overdue/10 text-status-overdue",
  },
  archived: {
    Icon: Archive,
    label: "Архив",
    cls: "bg-status-archived/10 text-status-archived",
  },
} as const satisfies Record<
  DocumentStatus,
  { Icon: React.ComponentType<{ className?: string }>; label: string; cls: string }
>;

interface StatusBadgeProps {
  status: DocumentStatus;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  // Defensive: API may send raw DB enums ("active"/"archived") or unknown.
  // Default to "ok" so the badge renders instead of crashing the row.
  const v = variants[status] ?? variants.ok;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium",
        v.cls,
        className,
      )}
      data-status={status}
    >
      <v.Icon className="size-3 shrink-0" aria-hidden />
      <span>{v.label}</span>
    </span>
  );
}
