// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/audit — System audit events.
 * TanStack Table: Time | Event type | Actor | Details.
 * Filters: from-date, to-date, actor, event-type, page-size.
 * Click row → expand to show full JSON payload.
 * No TOTP.
 */

import { useQuery } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { format } from "date-fns";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemAudit, type SystemAuditParams } from "@/features/system/api";
import type { SystemAuditEntry } from "@/features/system/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

const col = createColumnHelper<SystemAuditEntry>();

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

// ── JSON viewer ───────────────────────────────────────────────────────────────

function JsonViewer({ data }: { data: Record<string, unknown> }) {
  return (
    <pre className="rounded bg-muted/50 border border-border p-3 text-xs font-mono overflow-auto max-h-48 whitespace-pre-wrap break-all">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function SystemAuditPage() {
  const { t } = useTranslation();

  const [fromDate, setFromDate] = React.useState("");
  const [toDate, setToDate] = React.useState("");
  const [actor, setActor] = React.useState("");
  const [eventType, setEventType] = React.useState("");
  const [pageSize, setPageSize] = React.useState<PageSize>(25);
  const [cursor, setCursor] = React.useState<string | undefined>(undefined);
  const [expandedRows, setExpandedRows] = React.useState<Set<string>>(new Set());

  // Committed filter state — only applied on "Apply filters" click
  const [committed, setCommitted] = React.useState({
    from: "",
    to: "",
    actor: "",
    type: "",
    limit: 25 as PageSize,
    cursor: undefined as string | undefined,
  });

  const { data, isLoading, isError, isFetching } = useQuery({
    queryKey: ["system", "audit", committed],
    queryFn: () => {
      const params: SystemAuditParams = { limit: committed.limit };
      if (committed.from) params.from = committed.from;
      if (committed.to) params.to = committed.to;
      if (committed.actor) params.actor = committed.actor;
      if (committed.type) params.type = committed.type;
      if (committed.cursor) params.cursor = committed.cursor;
      return fetchSystemAudit(params);
    },
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });

  const applyFilters = () => {
    setCursor(undefined);
    setCommitted({
      from: fromDate,
      to: toDate,
      actor,
      type: eventType,
      limit: pageSize,
      cursor: undefined,
    });
  };

  const goNext = () => {
    if (!data?.next_cursor) return;
    const nextCursor = data.next_cursor;
    setCursor(nextCursor);
    setCommitted((c) => ({ ...c, cursor: nextCursor }));
  };

  const goPrev = () => {
    setCursor(undefined);
    setCommitted((c) => ({ ...c, cursor: undefined }));
  };

  const toggleExpand = React.useCallback((id: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const columns = React.useMemo(
    () => [
      col.display({
        id: "expand",
        header: () => <span className="sr-only">{t("system.audit_col_expand")}</span>,
        cell: ({ row }) => {
          const isExpanded = expandedRows.has(row.original.id);
          return (
            <button
              type="button"
              aria-expanded={isExpanded}
              aria-label={isExpanded ? t("system.audit_collapse") : t("system.audit_expand")}
              onClick={() => toggleExpand(row.original.id)}
              className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {isExpanded ? (
                <ChevronDown className="h-4 w-4" aria-hidden />
              ) : (
                <ChevronRight className="h-4 w-4" aria-hidden />
              )}
            </button>
          );
        },
      }),
      col.accessor("occurred_at", {
        header: t("system.audit_col_time"),
        cell: (info) => (
          <span className="font-mono text-xs">
            {format(new Date(info.getValue()), "dd.MM.yyyy HH:mm:ss")}
          </span>
        ),
      }),
      col.accessor("event_type", {
        header: t("system.audit_col_type"),
        cell: (info) => (
          <span className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
            {info.getValue()}
          </span>
        ),
      }),
      col.accessor("actor_email", {
        header: t("system.audit_col_actor"),
        cell: (info) => <span className="text-sm">{info.getValue() ?? "—"}</span>,
      }),
    ],
    [t, expandedRows, toggleExpand],
  );

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.audit_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.audit_subtitle")}</p>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-end gap-4 rounded-lg border border-border bg-muted/20 p-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-from">{t("system.audit_filter_from")}</Label>
          <Input
            id="audit-from"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="w-36"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-to">{t("system.audit_filter_to")}</Label>
          <Input
            id="audit-to"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="w-36"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-actor">{t("system.audit_filter_actor")}</Label>
          <Input
            id="audit-actor"
            type="email"
            placeholder="user@example.com"
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            className="w-44"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-type">{t("system.audit_filter_type")}</Label>
          <Input
            id="audit-type"
            type="text"
            placeholder="key.rotate"
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
            className="w-36"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="audit-pagesize">{t("system.audit_filter_page_size")}</Label>
          <select
            id="audit-pagesize"
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value) as PageSize)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {PAGE_SIZE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
        <Button onClick={applyFilters} disabled={isFetching} className="self-end">
          {t("system.audit_apply_filters")}
        </Button>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-4"
        >
          <p className="text-sm text-destructive">{t("system.audit_error")}</p>
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
        <>
          <div className="rounded-md border border-border overflow-auto">
            <table
              className="w-full text-sm"
              aria-label={t("system.audit_table_label")}
              aria-busy={isFetching}
            >
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
                      {t("system.audit_empty")}
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map((row) => {
                    const isExpanded = expandedRows.has(row.original.id);
                    return (
                      <React.Fragment key={row.id}>
                        <tr
                          className={cn(
                            "transition-colors hover:bg-muted/30 cursor-pointer",
                            isExpanded && "bg-muted/20",
                          )}
                          onClick={() => toggleExpand(row.original.id)}
                        >
                          {row.getVisibleCells().map((cell) => (
                            <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                            </td>
                          ))}
                        </tr>
                        {isExpanded && (
                          <tr>
                            <td colSpan={columns.length} className="px-6 py-3 bg-muted/10">
                              <JsonViewer data={row.original.payload} />
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="mt-4 flex items-center justify-between">
            <Button variant="outline" size="sm" disabled={!cursor} onClick={goPrev}>
              {t("system.audit_prev_page")}
            </Button>
            <span className="text-sm text-muted-foreground">
              {data?.items.length ?? 0} {t("system.audit_items_shown")}
            </span>
            <Button variant="outline" size="sm" disabled={!data?.has_more} onClick={goNext}>
              {t("system.audit_next_page")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
