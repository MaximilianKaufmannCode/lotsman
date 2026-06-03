// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /admin/notifications/history — read-only delivery history viewer (Phase C).
 *
 * Shows paginated notification.delivery_attempts rows from notification-svc.
 * Excel-like data-dense table per the project conventions with sticky header + colour-coded
 * status badges. Filters by status / template / date range. No mutations.
 */

import { useQuery } from "@tanstack/react-query";
import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import { Loader2, X } from "lucide-react";
import * as React from "react";
import { apiFetch } from "@/features/auth/api";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

interface HistoryItem {
  id: string;
  document_id: string;
  user_id: string;
  channel: "email" | "telegram" | "dion";
  template_code: "pre_notice" | "in_day" | "overdue";
  scheduled_at: string | null;
  sent_at: string | null;
  status: "pending" | "sent" | "failed";
  error: string | null;
  retry_count: number;
  created_at: string | null;
}

interface HistoryResponse {
  items: HistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

const STATUS_LABEL: Record<string, { text: string; cls: string; dot: string }> = {
  pending: { text: "В очереди", cls: "text-amber-700", dot: "bg-amber-500" },
  sent: { text: "Отправлено", cls: "text-green-700", dot: "bg-green-500" },
  failed: { text: "Ошибка", cls: "text-red-700", dot: "bg-red-500" },
};

const TEMPLATE_LABEL: Record<string, string> = {
  pre_notice: "Предуведомление",
  in_day: "В день истечения",
  overdue: "Просрочено",
};

const CHANNEL_LABEL: Record<string, string> = {
  email: "Email",
  telegram: "Telegram",
  dion: "Dion",
};

const PAGE_SIZE = 50;

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return format(parseISO(iso), "d MMM yyyy, HH:mm", { locale: ru });
  } catch {
    return iso;
  }
}

export function NotificationsHistoryPage() {
  const [page, setPage] = React.useState(0);
  const [filterStatus, setFilterStatus] = React.useState<string>("");
  const [filterTemplate, setFilterTemplate] = React.useState<string>("");

  const offset = page * PAGE_SIZE;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (filterStatus) params.set("status", filterStatus);
  if (filterTemplate) params.set("template_code", filterTemplate);

  const queryKey = ["admin", "notifications", "history", filterStatus, filterTemplate, offset];

  const query = useQuery({
    queryKey,
    queryFn: () =>
      apiFetch<HistoryResponse>(`/v1/admin/notifications/history?${params.toString()}`, {
        auth: true,
      }),
    staleTime: 15_000,
  });

  const totalPages = query.data ? Math.ceil(query.data.total / PAGE_SIZE) : 0;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-semibold">История уведомлений</h1>
          <p className="text-sm text-muted-foreground">
            Журнал отправки email/telegram/dion уведомлений по документам.
            Только чтение.
          </p>
        </div>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 mb-4">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Статус:
        </span>
        {(["", "sent", "failed", "pending"] as const).map((s) => (
          <FilterChip
            key={s || "all"}
            active={filterStatus === s}
            onClick={() => {
              setFilterStatus(s);
              setPage(0);
            }}
          >
            {s === "" ? "Все" : STATUS_LABEL[s]?.text || s}
          </FilterChip>
        ))}

        <span className="ml-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Тип:
        </span>
        {(["", "pre_notice", "in_day", "overdue"] as const).map((t) => (
          <FilterChip
            key={t || "all"}
            active={filterTemplate === t}
            onClick={() => {
              setFilterTemplate(t);
              setPage(0);
            }}
          >
            {t === "" ? "Все" : TEMPLATE_LABEL[t] || t}
          </FilterChip>
        ))}

        {(filterStatus || filterTemplate) && (
          <button
            type="button"
            onClick={() => {
              setFilterStatus("");
              setFilterTemplate("");
              setPage(0);
            }}
            className="ml-2 inline-flex items-center gap-1 rounded-full border border-dashed border-muted-foreground/40 px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted"
          >
            <X className="h-3 w-3" /> Сбросить
          </button>
        )}

        <span className="ml-auto text-xs text-muted-foreground">
          {query.data ? `${query.data.items.length} из ${query.data.total}` : "—"}
        </span>
      </div>

      {/* Table */}
      {query.isLoading && (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      )}

      {query.isError && (
        <div className="rounded bg-destructive/10 border border-destructive px-3 py-2">
          <p className="text-sm text-destructive">
            Ошибка загрузки: {(query.error as Error)?.message ?? "неизвестная"}
          </p>
        </div>
      )}

      {query.data && !query.isLoading && (
        <>
          <div className="rounded-md border border-border overflow-auto">
            <table className="w-full text-sm" aria-label="История уведомлений">
              <thead className="bg-muted/50 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Статус</th>
                  <th className="px-3 py-2 text-left font-medium">Тип</th>
                  <th className="px-3 py-2 text-left font-medium">Канал</th>
                  <th className="px-3 py-2 text-left font-medium">Запланировано</th>
                  <th className="px-3 py-2 text-left font-medium">Отправлено</th>
                  <th className="px-3 py-2 text-left font-medium">Документ</th>
                  <th className="px-3 py-2 text-left font-medium">Получатель</th>
                  <th className="px-3 py-2 text-left font-medium">Ошибка</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {query.data.items.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-3 py-12 text-center text-muted-foreground">
                      Пока ничего не отправлено.
                    </td>
                  </tr>
                )}
                {query.data.items.map((row) => {
                  const st = STATUS_LABEL[row.status] || {
                    text: row.status,
                    cls: "",
                    dot: "bg-gray-400",
                  };
                  return (
                    <tr key={row.id} className="hover:bg-muted/30 transition-colors">
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1.5 text-xs font-medium",
                            st.cls,
                          )}
                        >
                          <span
                            aria-hidden="true"
                            className={cn("h-2 w-2 rounded-full", st.dot)}
                          />
                          {st.text}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {TEMPLATE_LABEL[row.template_code] || row.template_code}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {CHANNEL_LABEL[row.channel] || row.channel}
                      </td>
                      <td className="px-3 py-2 text-xs whitespace-nowrap">
                        {fmtDate(row.scheduled_at)}
                      </td>
                      <td className="px-3 py-2 text-xs whitespace-nowrap">
                        {fmtDate(row.sent_at)}
                      </td>
                      <td className="px-3 py-2 text-xs font-mono">
                        {row.document_id.slice(0, 8)}…
                      </td>
                      <td className="px-3 py-2 text-xs font-mono">
                        {row.user_id.slice(0, 8)}…
                      </td>
                      <td className="px-3 py-2 text-xs text-red-700 max-w-md truncate">
                        {row.error || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="mt-3 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(p - 1, 0))}
              >
                ← Назад
              </Button>
              <span className="text-xs text-muted-foreground">
                Страница {page + 1} из {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Вперёд →
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-primary/10 text-primary border-primary/30"
          : "border-input bg-background text-muted-foreground hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}
