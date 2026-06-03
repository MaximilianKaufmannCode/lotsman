// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * NotificationBell — header in-app notification center (ADR-0011 Phase 3).
 *
 * Polls the unread count (30s), shows a badge, and on open lists the feed.
 * Clicking an item navigates to the document deep-link and marks it read.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Bell, CheckCheck } from "lucide-react";
import * as React from "react";
import {
  getMyNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from "@/features/auth/api";
import { cn } from "@/shared/lib/cn";

const UNREAD_KEY = ["notifications", "unread"];
const FEED_KEY = ["notifications", "feed"];

export function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);

  // Lightweight unread poll (also returns items, but we only read .unread here).
  const { data: feed } = useQuery({
    queryKey: open ? FEED_KEY : UNREAD_KEY,
    queryFn: () => getMyNotifications(20),
    refetchInterval: open ? false : 30_000,
    refetchOnWindowFocus: true,
  });

  const unread = feed?.unread ?? 0;
  const items: NotificationItem[] = feed?.items ?? [];

  const markRead = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FEED_KEY });
      qc.invalidateQueries({ queryKey: UNREAD_KEY });
    },
  });

  const markAll = useMutation({
    mutationFn: () => markAllNotificationsRead(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: FEED_KEY });
      qc.invalidateQueries({ queryKey: UNREAD_KEY });
    },
  });

  // Close on outside click / Escape.
  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const onItemClick = (item: NotificationItem) => {
    if (!item.is_read) markRead.mutate(item.id);
    setOpen(false);
    if (item.document_id) {
      navigate({
        to: "/registry",
        search: (prev: Record<string, unknown>) => ({
          ...prev,
          document_id: item.document_id,
        }),
      });
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={unread > 0 ? `Уведомления: ${unread} непрочитанных` : "Уведомления"}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "relative inline-flex h-9 w-9 items-center justify-center rounded-md",
          "text-foreground hover:bg-muted transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <Bell className="h-5 w-5" aria-hidden />
        {unread > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-red-600 text-white text-[10px] font-semibold flex items-center justify-center"
            aria-hidden
          >
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Уведомления"
          className={cn(
            "absolute right-0 mt-2 w-80 max-w-[90vw] z-50 rounded-md border border-border",
            "bg-popover shadow-lg overflow-hidden",
          )}
        >
          <div className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="text-sm font-semibold">Уведомления</span>
            <button
              type="button"
              disabled={unread === 0 || markAll.isPending}
              onClick={() => markAll.mutate()}
              className={cn(
                "inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              <CheckCheck className="h-3.5 w-3.5" aria-hidden />
              Прочитать все
            </button>
          </div>

          <ul className="max-h-96 overflow-y-auto divide-y divide-border">
            {items.length === 0 && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">
                Нет уведомлений
              </li>
            )}
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onItemClick(item)}
                  className={cn(
                    "w-full text-left px-3 py-2.5 hover:bg-muted transition-colors",
                    "focus-visible:outline-none focus-visible:bg-muted",
                    !item.is_read && "bg-primary/5",
                  )}
                >
                  <div className="flex items-start gap-2">
                    {!item.is_read && (
                      <span
                        className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary"
                        aria-hidden
                      />
                    )}
                    <div className={cn("min-w-0", item.is_read && "pl-4")}>
                      <p className="text-sm font-medium truncate">{item.title}</p>
                      {item.body && (
                        <p className="text-xs text-muted-foreground line-clamp-2">{item.body}</p>
                      )}
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {new Date(item.created_at).toLocaleString("ru-RU", {
                          day: "2-digit",
                          month: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </p>
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
