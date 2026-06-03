// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Simple toast system — not a dependency, owned by us.
 * Uses a global event bus + a single Toaster renderer.
 */

import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";

export type ToastVariant = "default" | "destructive" | "success";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastData {
  id: string;
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
  /**
   * Optional inline action (e.g. «Показать архив» after document archive).
   * Click dismisses the toast and runs onClick.
   */
  action?: ToastAction;
}

type ToastListener = (toasts: ToastData[]) => void;

const listeners: Set<ToastListener> = new Set();
let toasts: ToastData[] = [];

function notify() {
  for (const l of listeners) {
    l([...toasts]);
  }
}

export const toast = {
  show(data: Omit<ToastData, "id">): string {
    const id = Math.random().toString(36).slice(2);
    const item: ToastData = { id, duration: 4000, ...data };
    toasts = [...toasts, item];
    notify();
    if (item.duration && item.duration > 0) {
      setTimeout(() => toast.dismiss(id), item.duration);
    }
    return id;
  },
  dismiss(id: string) {
    toasts = toasts.filter((t) => t.id !== id);
    notify();
  },
  subscribe(listener: ToastListener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

// ── Individual Toast component ────────────────────────────────────────────────

interface ToastItemProps {
  data: ToastData;
  onDismiss: (id: string) => void;
}

function ToastItem({ data, onDismiss }: ToastItemProps) {
  const variantCls: Record<ToastVariant, string> = {
    default: "bg-card border-border text-foreground",
    destructive: "bg-destructive border-destructive text-destructive-foreground",
    success: "bg-status-ok/10 border-status-ok text-status-ok",
  };

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={cn(
        "relative flex w-full max-w-sm items-start gap-3 rounded-lg border p-4 shadow-lg",
        variantCls[data.variant ?? "default"],
      )}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">{data.title}</p>
        {data.description && <p className="mt-1 text-sm opacity-80">{data.description}</p>}
        {data.action && (
          <button
            type="button"
            onClick={() => {
              data.action?.onClick();
              onDismiss(data.id);
            }}
            className="mt-2 text-sm font-medium underline underline-offset-2 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            {data.action.label}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(data.id)}
        className="shrink-0 rounded p-0.5 opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Закрыть уведомление"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

// ── Toaster — mount once in <App /> ──────────────────────────────────────────

export function Toaster() {
  const [items, setItems] = React.useState<ToastData[]>([]);

  React.useEffect(() => {
    const unsubscribe = toast.subscribe(setItems);
    return () => {
      unsubscribe();
    };
  }, []);

  if (items.length === 0) return null;

  return (
    <section
      aria-label="Уведомления"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-full max-w-sm"
    >
      {items.map((t) => (
        <ToastItem key={t.id} data={t} onDismiss={toast.dismiss} />
      ))}
    </section>
  );
}
