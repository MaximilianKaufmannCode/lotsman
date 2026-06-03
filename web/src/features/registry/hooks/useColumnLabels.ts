// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Per-tenant column label overrides hook.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ColumnLabelsApiError,
  type ColumnLabelsResponse,
  getColumnLabels,
  updateColumnLabels,
} from "@/features/registry/columnLabelsApi";
import { toast } from "@/shared/ui/toast";

export const COLUMN_LABELS_QUERY_KEY = "registry-column-labels" as const;

export function useColumnLabels() {
  return useQuery<ColumnLabelsResponse, Error>({
    queryKey: [COLUMN_LABELS_QUERY_KEY] as const,
    queryFn: getColumnLabels,
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
  });
}

export function useUpdateColumnLabels() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (labels: Record<string, string>) => updateColumnLabels(labels),
    onSuccess: (data) => {
      queryClient.setQueryData<ColumnLabelsResponse>([COLUMN_LABELS_QUERY_KEY], data);
      toast.show({ title: "Название колонки обновлено", variant: "success" });
    },
    onError: (err) => {
      const msg =
        err instanceof ColumnLabelsApiError && err.status === 403
          ? "Только администратор может переименовывать колонки"
          : "Не удалось сохранить название";
      toast.show({ title: msg, variant: "destructive" });
    },
  });
}
