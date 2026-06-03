// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * /system/migrations — Alembic migration versions per service.
 * Table: Service | Current | Latest | Status.
 * Pending rows show "Apply migration" button → MaintenanceConfirmDialog.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import * as React from "react";
import { useTranslation } from "react-i18next";
import { fetchSystemMigrations, migrateService } from "@/features/system/api";
import type { MigrationEntry } from "@/features/system/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { toast } from "@/shared/ui/toast";
import { MaintenanceConfirmDialog } from "./components/MaintenanceConfirmDialog";

const col = createColumnHelper<MigrationEntry>();

export function SystemMigrationsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [applyTarget, setApplyTarget] = React.useState<MigrationEntry | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["system", "migrations"],
    queryFn: fetchSystemMigrations,
    staleTime: 60_000,
  });

  const handleApply = async (totpCode: string, confirmation: string) => {
    if (!applyTarget) return;
    await migrateService({
      service: applyTarget.service,
      totp_code: totpCode,
      confirmation,
    });
    toast.show({ title: t("system.migrations_applied"), variant: "success" });
    setApplyTarget(null);
    qc.invalidateQueries({ queryKey: ["system", "migrations"] });
  };

  const columns = React.useMemo(
    () => [
      col.accessor("service", {
        header: t("system.migrations_col_service"),
        cell: (info) => <span className="font-medium text-sm">{info.getValue()}</span>,
      }),
      col.accessor("current", {
        header: t("system.migrations_col_current"),
        cell: (info) => (
          <span className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
            {info.getValue()}
          </span>
        ),
      }),
      col.accessor("latest_in_code", {
        header: t("system.migrations_col_latest"),
        cell: (info) => (
          <span className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
            {info.getValue()}
          </span>
        ),
      }),
      col.display({
        id: "status",
        header: t("system.migrations_col_status"),
        cell: ({ row }) => {
          const { pending } = row.original;
          if (pending) {
            return (
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-status-soon">
                <AlertCircle className="h-3.5 w-3.5" aria-hidden />
                {t("system.migrations_status_pending")}
              </span>
            );
          }
          return (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-status-ok">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
              {t("system.migrations_status_ok")}
            </span>
          );
        },
      }),
      col.display({
        id: "actions",
        header: () => <span className="sr-only">{t("admin.col_actions")}</span>,
        cell: ({ row }) => {
          if (!row.original.pending) return null;
          return (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setApplyTarget(row.original)}
              className="text-xs"
            >
              {t("system.migrations_apply_btn")}
            </Button>
          );
        },
      }),
    ],
    [t],
  );

  const table = useReactTable({
    data: data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">{t("system.migrations_title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("system.migrations_subtitle")}</p>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded bg-destructive/10 border border-destructive px-3 py-2 mb-6"
        >
          <p className="text-sm text-destructive">{t("system.migrations_error")}</p>
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
          <table className="w-full text-sm" aria-label={t("system.migrations_table_label")}>
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
                    {t("system.migrations_empty")}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={cn("transition-colors", row.original.pending && "bg-status-soon/5")}
                  >
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

      {/* Apply migration dialog */}
      <MaintenanceConfirmDialog
        open={applyTarget !== null}
        onClose={() => setApplyTarget(null)}
        title={t("system.migrations_apply_title")}
        description={
          applyTarget
            ? t("system.migrations_apply_description", { service: applyTarget.service })
            : undefined
        }
        expected={applyTarget?.service ?? ""}
        onConfirm={handleApply}
      />
    </div>
  );
}
