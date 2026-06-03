// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/queues — Queues / Outbox / DLQ viewer.
 * TanStack Table. Highlights: dlq_size>0 → red; outbox>100 → yellow.
 * Shows stub banner if any row has note containing "stub".
 * No TOTP required (read-only).
 */

import { useQuery } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Info, Loader2 } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemQueues } from "@/features/system/api";
import type { QueueEntry } from "@/features/system/types";
import { cn } from "@/shared/lib/cn";

const col = createColumnHelper<QueueEntry>();

export function SystemQueuesPage() {
  const { t } = useTranslation();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["system", "queues"],
    queryFn: fetchSystemQueues,
    staleTime: 30_000,
  });

  const hasStub = React.useMemo(
    () => (data ?? []).some((r) => r.note?.toLowerCase().includes("stub")),
    [data],
  );

  const columns = React.useMemo(
    () => [
      col.accessor("service", {
        header: t("system.queues_col_service"),
        cell: (info) => <span className="font-medium text-sm">{info.getValue()}</span>,
      }),
      col.accessor("outbox_undispatched", {
        header: t("system.queues_col_outbox"),
        cell: (info) => (
          <span
            className={cn(
              "font-mono text-sm",
              info.getValue() > 100 && "text-status-soon font-semibold",
            )}
          >
            {info.getValue()}
          </span>
        ),
      }),
      col.accessor("stream_lag", {
        header: t("system.queues_col_lag"),
        cell: (info) => <span className="font-mono text-sm">{info.getValue()}</span>,
      }),
      col.accessor("dlq_size", {
        header: t("system.queues_col_dlq"),
        cell: (info) => (
          <span
            className={cn(
              "font-mono text-sm",
              info.getValue() > 0 && "text-status-overdue font-semibold",
            )}
          >
            {info.getValue()}
          </span>
        ),
      }),
      col.accessor("note", {
        header: t("system.queues_col_note"),
        cell: (info) => (
          <span className="text-sm text-muted-foreground">{info.getValue() ?? "—"}</span>
        ),
      }),
    ],
    [t],
  );

  const table = useReactTable({
    data: data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const getRowCls = (row: QueueEntry): string => {
    if (row.dlq_size > 0) return "bg-status-overdue/5";
    if (row.outbox_undispatched > 100) return "bg-status-soon/5";
    return "";
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.queues_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.queues_subtitle")}</p>
      </div>

      {/* Stub banner */}
      {hasStub && !isLoading && (
        <div
          role="note"
          className="mb-6 flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3"
        >
          <Info className="h-5 w-5 shrink-0 text-blue-500 mt-0.5" aria-hidden />
          <p className="text-sm text-blue-800">{t("system.queues_stub_note")}</p>
        </div>
      )}

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">{t("system.queues_error")}</p>
        </div>
      )}

      {isLoading && (
        <div
          role="status"
          aria-busy="true"
          aria-label={t("common.loading")}
          className="flex justify-center py-12"
        >
          <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden />
        </div>
      )}

      {!isLoading && (
        <div className="rounded-md border border-border overflow-auto">
          <table className="w-full text-sm" aria-label={t("system.queues_table_label")}>
            <thead className="bg-muted/50 sticky top-0">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      scope="col"
                      className="px-3 py-2 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide whitespace-nowrap"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody className="divide-y divide-border">
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-3 py-8 text-center text-sm text-muted-foreground"
                  >
                    {t("system.queues_empty")}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className={cn("transition-colors", getRowCls(row.original))}>
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
