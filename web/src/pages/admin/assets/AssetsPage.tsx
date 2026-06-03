// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AssetsPage — admin-only (RoleGuard) management of partner companies.
 * US-12..US-15: list, create, edit, archive assets.
 */

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { format, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import { Archive, Pencil, Plus, RefreshCw } from "lucide-react";
import * as React from "react";
import {
  useArchiveAsset,
  useAssets,
  useCreateAsset,
  usePatchAsset,
} from "@/features/registry/hooks/useAssets";
import type { Asset } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Skeleton } from "@/shared/ui/skeleton";
import { AssetArchiveConfirmDialog } from "./components/AssetArchiveConfirmDialog";
import { AssetDialog } from "./components/AssetDialog";

const columnHelper = createColumnHelper<Asset>();

export function AssetsPage() {
  const [q, setQ] = React.useState("");
  const [debouncedQ, setDebouncedQ] = React.useState("");
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQ(q), 200);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [q]);

  const { data, isLoading, isError, refetch } = useAssets(debouncedQ ? { q: debouncedQ } : {});
  const createMutation = useCreateAsset();
  const patchMutation = usePatchAsset();
  const archiveMutation = useArchiveAsset();

  const [createOpen, setCreateOpen] = React.useState(false);
  const [editAsset, setEditAsset] = React.useState<Asset | null>(null);
  const [archiveAsset, setArchiveAsset] = React.useState<Asset | null>(null);

  const columns = React.useMemo(
    () => [
      columnHelper.accessor("name", {
        header: "Название",
        cell: (info) => <span className="font-medium">{info.getValue()}</span>,
      }),
      columnHelper.accessor("inn", {
        header: "ИНН",
        cell: (info) => <span className="font-mono text-xs">{info.getValue() ?? "—"}</span>,
      }),
      columnHelper.accessor("notes", {
        header: "Заметки",
        cell: (info) => (
          <span className="text-xs text-muted-foreground truncate max-w-[200px] block">
            {info.getValue() ?? "—"}
          </span>
        ),
      }),
      columnHelper.accessor("document_count", {
        header: "Документов",
        cell: (info) => <span className="tabular-nums">{info.getValue() ?? 0}</span>,
      }),
      columnHelper.accessor("created_at", {
        header: "Создан",
        cell: (info) => (
          <span className="font-mono text-xs tabular-nums">
            {format(parseISO(info.getValue()), "dd.MM.yyyy", { locale: ru })}
          </span>
        ),
      }),
      columnHelper.display({
        id: "actions",
        header: "Действия",
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setEditAsset(row.original)}
              aria-label={`Редактировать ${row.original.name}`}
              className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Pencil className="size-4" aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => setArchiveAsset(row.original)}
              aria-label={`Архивировать ${row.original.name}`}
              className="rounded p-1.5 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Archive className="size-4" aria-hidden />
            </button>
          </div>
        ),
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-6 py-4 border-b flex items-center justify-between gap-4">
        <h1 className="text-xl font-semibold">Контрагенты</h1>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" aria-hidden />
          Добавить контрагента
        </Button>
      </div>

      {/* Search */}
      <div className="px-6 py-3 border-b">
        <Input
          type="search"
          aria-label="Поиск контрагентов"
          placeholder="Поиск по названию..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-sm"
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {isLoading && !data && (
          <div className="p-6 space-y-3">
            {Array.from({ length: 6 }, (_, i) => i).map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        )}

        {isError && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <p className="text-muted-foreground">Не удалось загрузить контрагентов</p>
            <Button variant="outline" onClick={() => void refetch()}>
              <RefreshCw className="size-4 mr-2" aria-hidden />
              Повторить
            </Button>
          </div>
        )}

        {!isLoading && !isError && (
          <table className="w-full border-collapse text-sm" aria-label="Список контрагентов">
            <thead className="sticky top-0 z-10 bg-muted/95 border-b">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => (
                    <th
                      key={header.id}
                      scope="col"
                      className="px-4 py-2.5 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wider whitespace-nowrap"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="py-16 text-center text-sm text-muted-foreground"
                  >
                    {debouncedQ ? "Контрагенты не найдены" : "Нет контрагентов"}
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    className={cn(
                      "border-b hover:bg-muted/30 transition-colors",
                      row.original.deleted_at && "opacity-60",
                    )}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-3">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Dialogs */}
      <AssetDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSubmit={async (values) => {
          await createMutation.mutateAsync(values);
        }}
      />

      <AssetDialog
        open={!!editAsset}
        asset={editAsset}
        onClose={() => setEditAsset(null)}
        onSubmit={async (values) => {
          if (!editAsset) return;
          await patchMutation.mutateAsync({ id: editAsset.id, payload: values });
        }}
      />

      <AssetArchiveConfirmDialog
        open={!!archiveAsset}
        asset={archiveAsset}
        onConfirm={async () => {
          if (!archiveAsset) return;
          await archiveMutation.mutateAsync(archiveAsset.id);
          setArchiveAsset(null);
        }}
        onCancel={() => setArchiveAsset(null)}
      />
    </div>
  );
}
