// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Accessible dialog with focus trap and Escape-close.
 * No external dependency — owned by us.
 */

import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string | undefined;
  children: React.ReactNode;
  className?: string | undefined;
}

export function Dialog({ open, onClose, title, description, children, className }: DialogProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);

  // Close on Escape
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Focus the dialog on open
  React.useEffect(() => {
    if (open) {
      dialogRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden
        onClick={onClose}
      />
      {/* Panel */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        aria-describedby={description ? "dialog-description" : undefined}
        tabIndex={-1}
        className={cn(
          // Position + size: centered, max 90vh tall (so content scrolls
          // instead of overflowing viewport), flex column so header is sticky
          // at the top while content area scrolls.
          "fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2",
          "max-h-[90vh] flex flex-col",
          "rounded-xl border bg-card text-card-foreground shadow-xl",
          "focus:outline-none",
          className,
        )}
      >
        <div className="flex items-start justify-between p-6 pb-2 shrink-0">
          <div>
            <h2 id="dialog-title" className="text-lg font-semibold">
              {title}
            </h2>
            {description && (
              <p id="dialog-description" className="mt-1 text-sm text-muted-foreground">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-4 rounded p-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {/* Content area scrolls if it exceeds available height */}
        <div className="p-6 pt-4 overflow-y-auto flex-1 min-h-0">{children}</div>
      </div>
    </>
  );
}
