// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * ExportJobsModal — lists user's recent xlsx export jobs.
 * Download button enabled only when completed and not expired (24h TTL per Q8).
 */

import { format, isAfter, parseISO } from "date-fns";
import { ru } from "date-fns/locale";
import { CheckCircle, Clock, Download, FileX2, Loader2, X } from "lucide-react";
import * as React from "react";
import { useDownloadExportJob, useExportJobs } from "@/features/registry/hooks/useExportJob";
import type { ExportJob } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";

interface ExportJobsModalProps {
  open: boolean;
  onClose: () => void;
}

export function ExportJobsModal({ open, onClose }: ExportJobsModalProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);
  const { data: jobs, isLoading, isError, refetch } = useExportJobs();
  const downloadMutation = useDownloadExportJob();

  // Close on Escape
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // Focus dialog on open
  React.useEffect(() => {
    if (open) {
      requestAnimationFrame(() => dialogRef.current?.focus());
    }
  }, [open]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-jobs-title"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none",
          "max-h-[80vh] flex flex-col",
        )}
      >
        <div className="flex items-center justify-between p-4 border-b shrink-0">
          <h2 id="export-jobs-title" className="text-base font-semibold">
            Экспорты .xlsx
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && (
            <div className="space-y-3">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          )}

          {isError && (
            <div className="text-sm text-muted-foreground space-y-2">
              <p>Не удалось загрузить список экспортов.</p>
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                Повторить
              </Button>
            </div>
          )}

          {!isLoading && !isError && (!jobs || jobs.length === 0) && (
            <div className="flex flex-col items-center justify-center py-12 gap-3 text-center">
              <FileX2 className="size-10 text-muted-foreground/40" aria-hidden />
              <p className="text-sm text-muted-foreground">Нет экспортов</p>
            </div>
          )}

          {jobs && jobs.length > 0 && (
            <ul aria-label="Список экспортов" className="space-y-2">
              {jobs.map((job) => (
                <ExportJobRow
                  key={job.id}
                  job={job}
                  onDownload={() => downloadMutation.mutate(job.id)}
                  isDownloading={downloadMutation.isPending}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}

// ── Job row ───────────────────────────────────────────────────────────────────

function ExportJobRow({
  job,
  onDownload,
  isDownloading,
}: {
  job: ExportJob;
  onDownload: () => void;
  isDownloading: boolean;
}) {
  const isExpired =
    job.status === "expired" ||
    (job.expires_at !== null && isAfter(new Date(), parseISO(job.expires_at)));

  const canDownload = job.status === "done" && !isExpired;

  const statusIcon = {
    pending: <Clock className="size-4 text-muted-foreground" aria-label="Ожидание" />,
    running: <Loader2 className="size-4 text-status-soon animate-spin" aria-label="Выполняется" />,
    done: <CheckCircle className="size-4 text-status-ok" aria-label="Готово" />,
    failed: <FileX2 className="size-4 text-destructive" aria-label="Ошибка" />,
    expired: <FileX2 className="size-4 text-muted-foreground" aria-label="Истёк" />,
  }[job.status];

  const statusLabel = {
    pending: "Ожидание",
    running: "Выполняется...",
    done: isExpired ? "Истёк" : "Готов",
    failed: "Ошибка",
    expired: "Истёк",
  }[job.status];

  return (
    <li className="flex items-center gap-3 rounded-md border px-3 py-2.5">
      <span aria-hidden>{statusIcon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{statusLabel}</p>
        <p className="text-xs text-muted-foreground">
          {format(parseISO(job.created_at), "dd.MM.yyyy HH:mm", { locale: ru })}
          {job.expires_at && (
            <> · истекает {format(parseISO(job.expires_at), "dd.MM.yyyy HH:mm", { locale: ru })}</>
          )}
        </p>
        {job.error && <p className="mt-0.5 text-xs text-destructive">{job.error}</p>}
      </div>
      {canDownload && (
        <Button
          size="sm"
          variant="outline"
          onClick={onDownload}
          disabled={isDownloading}
          aria-label="Скачать файл экспорта"
        >
          <Download className="size-4" aria-hidden />
        </Button>
      )}
    </li>
  );
}
