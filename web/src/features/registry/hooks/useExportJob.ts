// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * useExportJob — request an xlsx export job, poll status every 2s, expose
 * download URL when complete.
 *
 * Lifecycle: request → poll (pending/running) → done | failed | expired
 * Download TTL: 24h (Q8). After expiry the server returns 410.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "@/shared/ui/toast";
import { downloadExportJob, getExportJob, listExportJobs, requestExportJob } from "../api";
import type { ExportJob, ExportRequestPayload } from "../types";

export const EXPORT_JOBS_QUERY_KEY = "export-jobs" as const;

// ── Request job ───────────────────────────────────────────────────────────────

export function useRequestExportJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ExportRequestPayload) => requestExportJob(payload),
    onSuccess: () => {
      toast.show({
        title: "Экспорт запущен",
        description: "Вы получите уведомление о готовности.",
        variant: "default",
      });
      void queryClient.invalidateQueries({ queryKey: [EXPORT_JOBS_QUERY_KEY] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Не удалось запустить экспорт";
      toast.show({ title: "Ошибка экспорта", description: msg, variant: "destructive" });
    },
  });
}

// ── Poll single job ───────────────────────────────────────────────────────────

export function useExportJobStatus(jobId: string | null) {
  return useQuery<ExportJob, Error>({
    queryKey: [EXPORT_JOBS_QUERY_KEY, jobId] as const,
    queryFn: () => {
      if (!jobId) return Promise.reject(new Error("No job id"));
      return getExportJob(jobId);
    },
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      // Stop polling when job reaches a terminal state
      if (status === "done" || status === "failed" || status === "expired") return false;
      return 2000; // 2s poll interval
    },
  });
}

// ── List all jobs ──────────────────────────────────────────────────────────────

export function useExportJobs() {
  return useQuery<ExportJob[], Error>({
    queryKey: [EXPORT_JOBS_QUERY_KEY] as const,
    queryFn: listExportJobs,
    staleTime: 10_000,
  });
}

// ── Download ──────────────────────────────────────────────────────────────────

export function useDownloadExportJob() {
  return useMutation({
    mutationFn: (jobId: string) => downloadExportJob(jobId),
    onSuccess: (url: string) => {
      // Trigger browser download
      const a = document.createElement("a");
      a.href = url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Ошибка скачивания";
      // Handle 410 Gone specifically
      const isExpired = err instanceof Error && err.message.includes("410");
      toast.show({
        title: isExpired ? "Файл экспорта истёк" : "Ошибка скачивания",
        description: isExpired ? "Файл экспорта истёк. Создайте новый экспорт." : msg,
        variant: "destructive",
      });
    },
  });
}

// ── Toast on completion (used when job is polled from the export jobs list) ───

export function useExportCompletionToast(jobId: string | null) {
  const prevStatusRef = React.useRef<string | null>(null);
  const { data } = useExportJobStatus(jobId);

  React.useEffect(() => {
    const prev = prevStatusRef.current;
    const curr = data?.status ?? null;
    if (prev !== curr && curr === "done") {
      toast.show({
        title: "Экспорт готов",
        description: "Файл доступен для скачивания.",
        variant: "success",
        duration: 8000,
      });
    }
    if (prev !== curr && curr === "failed") {
      toast.show({
        title: "Экспорт завершился с ошибкой",
        description: "Попробуйте снова.",
        variant: "destructive",
      });
    }
    prevStatusRef.current = curr;
  }, [data?.status]);
}
